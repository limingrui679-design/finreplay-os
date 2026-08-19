"""Installable user catalogs and deterministic offline scenario runners."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Iterable
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finreplay.engines import ReplayBuildResult, ReplayStudio


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AdapterCatalogEntry(_StrictModel):
    adapter_id: str
    title: str
    publisher: str
    temporal_coverage: str
    historical_replay_eligible: bool
    record_count: int = Field(ge=0)
    retrieved_at: str
    receipt: str


class AdapterCatalog(_StrictModel):
    schema_version: str
    catalog_kind: str
    adapter_count: int
    historical_replay_eligible_count: int
    temporal_coverage_counts: dict[str, int]
    source_path: str
    source_sha256: str
    claim_boundary: str
    adapters: tuple[AdapterCatalogEntry, ...]
    catalog_sha256: str

    @model_validator(mode="after")
    def validate_counts(self) -> AdapterCatalog:
        if self.adapter_count != len(self.adapters):
            raise ValueError("adapter_count does not match catalog entries")
        if self.historical_replay_eligible_count != sum(
            entry.historical_replay_eligible for entry in self.adapters
        ):
            raise ValueError("historical_replay_eligible_count mismatch")
        if self.catalog_sha256 != _catalog_hash(self):
            raise ValueError("adapter catalog self-hash mismatch")
        return self


class ScenarioCatalogEntry(_StrictModel):
    slug: str
    scenario_id: str
    scenario_version: str
    replay_id: str
    title: str
    mode: str
    decision_time: str
    code_commit: str
    distinct_input_records: int = Field(ge=0)
    source_set_historical_replay_eligible: bool
    pack_sha256: str
    trace_id: str
    proof_sha256: str
    proof_path: str
    report_path: str
    input_lock_resource: str
    input_lock_sha256: str
    loader: str
    builder: str


class ScenarioCatalog(_StrictModel):
    schema_version: str
    catalog_kind: str
    scenario_count: int
    source_path: str
    source_sha256: str
    claim_boundary: str
    scenarios: tuple[ScenarioCatalogEntry, ...]
    catalog_sha256: str

    @model_validator(mode="after")
    def validate_counts(self) -> ScenarioCatalog:
        if self.scenario_count != len(self.scenarios):
            raise ValueError("scenario_count does not match catalog entries")
        identities = {(entry.slug, entry.scenario_id) for entry in self.scenarios}
        if len(identities) != len(self.scenarios):
            raise ValueError("scenario catalog contains duplicate identities")
        if self.catalog_sha256 != _catalog_hash(self):
            raise ValueError("scenario catalog self-hash mismatch")
        return self


class CapabilityLens(_StrictModel):
    """A bounded analytical capability used to navigate the scenario portfolio."""

    lens_id: str
    label: str
    short_label: str
    question: str
    description: str


class ScenarioPathway(_StrictModel):
    """A curated, cross-scenario route through one decision problem."""

    pathway_id: str
    title: str
    description: str
    lens_ids: tuple[str, ...]
    scenario_slugs: tuple[str, ...]


class ScenarioExplorerEntry(_StrictModel):
    """Public navigation metadata that does not alter a ReplayPack claim."""

    slug: str
    order: int = Field(ge=1)
    public_title: str
    publisher: str
    family: Literal["Banking", "Macro", "Rates", "Regulatory"]
    result: str
    tone: Literal["boundary", "inside", "breach"]
    primary_method: str
    decision_question: str
    lens_ids: tuple[str, ...]


class ScenarioExplorerCatalog(_StrictModel):
    """Validated capability, pathway, and presentation metadata for all scenarios."""

    schema_version: str
    catalog_kind: str
    scenario_count: int = Field(ge=1)
    catalog_sha256: str
    claim_boundary: str
    lenses: tuple[CapabilityLens, ...]
    pathways: tuple[ScenarioPathway, ...]
    scenarios: tuple[ScenarioExplorerEntry, ...]

    @model_validator(mode="after")
    def validate_graph(self) -> ScenarioExplorerCatalog:
        if self.scenario_count != len(self.scenarios):
            raise ValueError("scenario_count does not match explorer entries")
        lens_ids = [lens.lens_id for lens in self.lenses]
        if len(lens_ids) != len(set(lens_ids)):
            raise ValueError("scenario explorer contains duplicate lens IDs")
        pathway_ids = [pathway.pathway_id for pathway in self.pathways]
        if len(pathway_ids) != len(set(pathway_ids)):
            raise ValueError("scenario explorer contains duplicate pathway IDs")
        scenario_slugs = [scenario.slug for scenario in self.scenarios]
        if len(scenario_slugs) != len(set(scenario_slugs)):
            raise ValueError("scenario explorer contains duplicate scenario slugs")
        if sorted(scenario.order for scenario in self.scenarios) != list(
            range(1, self.scenario_count + 1)
        ):
            raise ValueError("scenario explorer order must be contiguous and one-based")
        valid_lenses = set(lens_ids)
        valid_slugs = set(scenario_slugs)
        for scenario in self.scenarios:
            if len(scenario.lens_ids) < 2 or len(set(scenario.lens_ids)) != len(
                scenario.lens_ids
            ):
                raise ValueError(f"invalid capability lenses for scenario: {scenario.slug}")
            unknown = set(scenario.lens_ids) - valid_lenses
            if unknown:
                raise ValueError(
                    f"unknown capability lenses for scenario {scenario.slug}: {unknown}"
                )
        for pathway in self.pathways:
            if len(pathway.scenario_slugs) < 3:
                raise ValueError(f"pathway needs at least three scenarios: {pathway.pathway_id}")
            unknown_lenses = set(pathway.lens_ids) - valid_lenses
            unknown_slugs = set(pathway.scenario_slugs) - valid_slugs
            if unknown_lenses or unknown_slugs:
                raise ValueError(
                    f"invalid pathway references for {pathway.pathway_id}: "
                    f"lenses={unknown_lenses}, scenarios={unknown_slugs}"
                )
        represented_lenses = {
            lens_id for scenario in self.scenarios for lens_id in scenario.lens_ids
        }
        if represented_lenses != valid_lenses:
            raise ValueError("every capability lens must be represented by at least one scenario")
        if self.catalog_sha256 != _catalog_hash(self):
            raise ValueError("scenario explorer self-hash mismatch")
        return self


class CapabilityCatalogEntry(_StrictModel):
    """One evidence-bounded route through the implementation and case portfolio."""

    capability_id: str
    title: str
    short_title: str
    scope: Literal["direct", "transferable", "boundary_only"]
    summary: str
    disciplines: tuple[str, ...]
    questions: tuple[str, ...]
    scenario_slugs: tuple[str, ...]
    evidence_locators: tuple[str, ...]
    does_not_prove: tuple[str, ...]


class CapabilityCatalog(_StrictModel):
    """School-neutral map from capabilities to scenarios and machine evidence."""

    schema_version: str
    catalog_kind: str
    capability_count: int
    source_path: str
    source_sha256: str
    scenario_catalog_sha256: str
    claim_boundary: str
    capabilities: tuple[CapabilityCatalogEntry, ...]
    catalog_sha256: str

    @model_validator(mode="after")
    def validate_entries(self) -> CapabilityCatalog:
        if self.capability_count != len(self.capabilities):
            raise ValueError("capability_count does not match catalog entries")
        identities = {entry.capability_id for entry in self.capabilities}
        if len(identities) != len(self.capabilities):
            raise ValueError("capability catalog contains duplicate identities")
        if any(not entry.scenario_slugs for entry in self.capabilities):
            raise ValueError("every capability must select at least one scenario")
        if self.catalog_sha256 != _catalog_hash(self):
            raise ValueError("capability catalog self-hash mismatch")
        return self


def load_adapter_catalog() -> AdapterCatalog:
    """Load the formal live-adapter catalog bundled with the installed package."""

    return AdapterCatalog.model_validate(_resource_json("adapter-catalog.json"))


def load_scenario_catalog() -> ScenarioCatalog:
    """Load the 30-scenario offline-runner catalog bundled with the package."""

    return ScenarioCatalog.model_validate(_resource_json("scenario-catalog.json"))


def load_scenario_explorer_catalog() -> ScenarioExplorerCatalog:
    """Load and cross-check the portfolio navigation layer bundled with the package."""

    explorer = ScenarioExplorerCatalog.model_validate(
        _resource_json("scenario-explorer.json")
    )
    canonical_slugs = {entry.slug for entry in load_scenario_catalog().scenarios}
    explorer_slugs = {entry.slug for entry in explorer.scenarios}
    if explorer_slugs != canonical_slugs:
        missing = sorted(canonical_slugs - explorer_slugs)
        extra = sorted(explorer_slugs - canonical_slugs)
        raise ValueError(
            f"scenario explorer and runner catalog differ: missing={missing}, extra={extra}"
        )
    return explorer


def load_capability_catalog() -> CapabilityCatalog:
    """Load and cross-check the evidence-bounded capability map."""

    catalog = CapabilityCatalog.model_validate(_resource_json("capability-catalog.json"))
    canonical = load_scenario_catalog()
    if catalog.scenario_catalog_sha256 != canonical.catalog_sha256:
        raise ValueError("capability map is not bound to the packaged scenario catalog")
    canonical_slugs = {entry.slug for entry in canonical.scenarios}
    referenced_slugs = {
        slug for capability in catalog.capabilities for slug in capability.scenario_slugs
    }
    unknown = referenced_slugs - canonical_slugs
    if unknown:
        raise ValueError(f"capability map references unknown scenarios: {sorted(unknown)}")
    return catalog


def find_scenario(value: str) -> ScenarioCatalogEntry:
    """Resolve a scenario by user-facing slug, scenario ID, or replay ID."""

    normalized = value.strip().lower()
    matches = [
        entry
        for entry in load_scenario_catalog().scenarios
        if normalized in {entry.slug.lower(), entry.scenario_id.lower(), entry.replay_id.lower()}
    ]
    if len(matches) != 1:
        raise KeyError(f"unknown scenario: {value}")
    return matches[0]


def find_capability(value: str) -> CapabilityCatalogEntry:
    """Resolve a capability by its stable ID or short title."""

    normalized = value.strip().lower()
    matches = [
        entry
        for entry in load_capability_catalog().capabilities
        if normalized in {entry.capability_id.lower(), entry.short_title.lower()}
    ]
    if len(matches) != 1:
        raise KeyError(f"unknown capability: {value}")
    return matches[0]


def run_scenario(
    value: str,
    destination: Path,
    *,
    archive: Path | None = None,
    code_commit: str | None = None,
) -> ReplayBuildResult:
    """Run a bundled scenario from its byte-locked official-source inputs."""

    entry = find_scenario(value)
    scenario_module = importlib.import_module("finreplay.scenarios")
    loader = getattr(scenario_module, entry.loader)
    builder = getattr(scenario_module, entry.builder)
    resource = files("finreplay").joinpath("resources", entry.input_lock_resource)
    with as_file(resource) as input_lock:
        lock = loader(input_lock)
    spec = builder(lock, code_commit=code_commit or entry.code_commit)
    studio = ReplayStudio()
    result = studio.build(spec, destination)
    if archive is not None:
        studio.archive(result.root, archive)
    return result


def catalog_rows(
    entries: Iterable[BaseModel], fields_to_show: tuple[str, ...]
) -> list[tuple[str, ...]]:
    """Return deterministic display rows without coupling catalogs to a UI toolkit."""

    return [tuple(str(getattr(entry, field)) for field in fields_to_show) for entry in entries]


def _resource_json(name: str) -> dict[str, Any]:
    resource = files("finreplay").joinpath("resources", name)
    value = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"package resource is not a JSON object: {name}")
    return value


def _catalog_hash(catalog: BaseModel) -> str:
    payload = catalog.model_dump(mode="json", exclude={"catalog_sha256"})
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()
