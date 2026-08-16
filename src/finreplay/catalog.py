"""Installable user catalogs and deterministic offline scenario runners."""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterable
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

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
        return self


def load_adapter_catalog() -> AdapterCatalog:
    """Load the formal live-adapter catalog bundled with the installed package."""

    return AdapterCatalog.model_validate(_resource_json("adapter-catalog.json"))


def load_scenario_catalog() -> ScenarioCatalog:
    """Load the 30-scenario offline-runner catalog bundled with the package."""

    return ScenarioCatalog.model_validate(_resource_json("scenario-catalog.json"))


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
