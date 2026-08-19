from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import finreplay.catalog as catalog_module
from finreplay.catalog import (
    AdapterCatalog,
    CapabilityCatalog,
    ScenarioCatalog,
    ScenarioExplorerCatalog,
    find_capability,
    find_scenario,
    load_adapter_catalog,
    load_capability_catalog,
    load_scenario_catalog,
    load_scenario_explorer_catalog,
)


def _payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _rehash(payload: dict[str, Any]) -> None:
    without_hash = {key: value for key, value in payload.items() if key != "catalog_sha256"}
    canonical = json.dumps(
        without_hash,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    payload["catalog_sha256"] = hashlib.sha256(canonical).hexdigest()


def test_adapter_catalog_rejects_both_count_mismatches() -> None:
    count_payload = _payload(load_adapter_catalog())
    count_payload["adapter_count"] = 29
    with pytest.raises(ValidationError, match="adapter_count does not match"):
        AdapterCatalog.model_validate(count_payload)

    historical_payload = _payload(load_adapter_catalog())
    historical_payload["historical_replay_eligible_count"] = 2
    with pytest.raises(ValidationError, match="historical_replay_eligible_count mismatch"):
        AdapterCatalog.model_validate(historical_payload)


def test_every_installable_catalog_rejects_a_self_hash_mismatch() -> None:
    adapter_payload = _payload(load_adapter_catalog())
    adapter_payload["catalog_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="adapter catalog self-hash mismatch"):
        AdapterCatalog.model_validate(adapter_payload)

    scenario_payload = _payload(load_scenario_catalog())
    scenario_payload["catalog_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="scenario catalog self-hash mismatch"):
        ScenarioCatalog.model_validate(scenario_payload)

    explorer_payload = _payload(load_scenario_explorer_catalog())
    explorer_payload["catalog_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="scenario explorer self-hash mismatch"):
        ScenarioExplorerCatalog.model_validate(explorer_payload)

    capability_payload = _payload(load_capability_catalog())
    capability_payload["catalog_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="capability catalog self-hash mismatch"):
        CapabilityCatalog.model_validate(capability_payload)


def test_scenario_catalog_rejects_count_and_identity_mismatches() -> None:
    count_payload = _payload(load_scenario_catalog())
    count_payload["scenario_count"] = 29
    with pytest.raises(ValidationError, match="scenario_count does not match"):
        ScenarioCatalog.model_validate(count_payload)

    duplicate_payload = _payload(load_scenario_catalog())
    duplicate_payload["scenarios"].append(deepcopy(duplicate_payload["scenarios"][0]))
    duplicate_payload["scenario_count"] = 31
    with pytest.raises(ValidationError, match="duplicate identities"):
        ScenarioCatalog.model_validate(duplicate_payload)


def test_explorer_rejects_duplicate_graph_identities() -> None:
    count_payload = _payload(load_scenario_explorer_catalog())
    count_payload["scenario_count"] = 29
    with pytest.raises(ValidationError, match="scenario_count does not match"):
        ScenarioExplorerCatalog.model_validate(count_payload)

    lens_payload = _payload(load_scenario_explorer_catalog())
    lens_payload["lenses"].append(deepcopy(lens_payload["lenses"][0]))
    with pytest.raises(ValidationError, match="duplicate lens IDs"):
        ScenarioExplorerCatalog.model_validate(lens_payload)

    pathway_payload = _payload(load_scenario_explorer_catalog())
    pathway_payload["pathways"].append(deepcopy(pathway_payload["pathways"][0]))
    with pytest.raises(ValidationError, match="duplicate pathway IDs"):
        ScenarioExplorerCatalog.model_validate(pathway_payload)

    scenario_payload = _payload(load_scenario_explorer_catalog())
    scenario_payload["scenarios"].append(deepcopy(scenario_payload["scenarios"][0]))
    scenario_payload["scenario_count"] = 31
    with pytest.raises(ValidationError, match="duplicate scenario slugs"):
        ScenarioExplorerCatalog.model_validate(scenario_payload)


def test_explorer_rejects_invalid_order_and_scenario_lenses() -> None:
    order_payload = _payload(load_scenario_explorer_catalog())
    order_payload["scenarios"][0]["order"] = 2
    with pytest.raises(ValidationError, match="order must be contiguous"):
        ScenarioExplorerCatalog.model_validate(order_payload)

    duplicate_lens_payload = _payload(load_scenario_explorer_catalog())
    first_lens = duplicate_lens_payload["scenarios"][0]["lens_ids"][0]
    duplicate_lens_payload["scenarios"][0]["lens_ids"] = [first_lens, first_lens]
    with pytest.raises(ValidationError, match="invalid capability lenses"):
        ScenarioExplorerCatalog.model_validate(duplicate_lens_payload)

    unknown_lens_payload = _payload(load_scenario_explorer_catalog())
    unknown_lens_payload["scenarios"][0]["lens_ids"].append("unknown-lens")
    with pytest.raises(ValidationError, match="unknown capability lenses"):
        ScenarioExplorerCatalog.model_validate(unknown_lens_payload)


def test_explorer_rejects_invalid_pathways_and_orphan_dimensions() -> None:
    short_path_payload = _payload(load_scenario_explorer_catalog())
    short_path_payload["pathways"][0]["scenario_slugs"] = ["svb-2023", "pacwest-2023"]
    with pytest.raises(ValidationError, match="at least three scenarios"):
        ScenarioExplorerCatalog.model_validate(short_path_payload)

    reference_payload = _payload(load_scenario_explorer_catalog())
    reference_payload["pathways"][0]["lens_ids"].append("unknown-lens")
    reference_payload["pathways"][0]["scenario_slugs"].append("unknown-scenario")
    with pytest.raises(ValidationError, match="invalid pathway references"):
        ScenarioExplorerCatalog.model_validate(reference_payload)

    orphan_payload = _payload(load_scenario_explorer_catalog())
    orphan_payload["lenses"].append(
        {
            "lens_id": "orphan",
            "label": "Orphan",
            "short_label": "Orphan",
            "question": "Is this represented?",
            "description": "Validation fixture.",
        }
    )
    with pytest.raises(ValidationError, match="every capability lens"):
        ScenarioExplorerCatalog.model_validate(orphan_payload)


def test_explorer_loader_rejects_drift_from_runner_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explorer_payload = _payload(load_scenario_explorer_catalog())
    old_slug = explorer_payload["scenarios"][0]["slug"]
    explorer_payload["scenarios"][0]["slug"] = "not-in-runner-catalog"
    for pathway in explorer_payload["pathways"]:
        pathway["scenario_slugs"] = [
            "not-in-runner-catalog" if slug == old_slug else slug
            for slug in pathway["scenario_slugs"]
        ]
    _rehash(explorer_payload)
    real_resource_json = catalog_module._resource_json

    def fake_resource_json(name: str) -> dict[str, Any]:
        if name == "scenario-explorer.json":
            return explorer_payload
        return real_resource_json(name)

    monkeypatch.setattr(catalog_module, "_resource_json", fake_resource_json)
    with pytest.raises(ValueError, match="explorer and runner catalog differ"):
        load_scenario_explorer_catalog()


def test_capability_catalog_rejects_invalid_entries() -> None:
    count_payload = _payload(load_capability_catalog())
    count_payload["capability_count"] = 9
    with pytest.raises(ValidationError, match="capability_count does not match"):
        CapabilityCatalog.model_validate(count_payload)

    duplicate_payload = _payload(load_capability_catalog())
    duplicate_payload["capabilities"].append(deepcopy(duplicate_payload["capabilities"][0]))
    duplicate_payload["capability_count"] = 11
    with pytest.raises(ValidationError, match="duplicate identities"):
        CapabilityCatalog.model_validate(duplicate_payload)

    empty_payload = _payload(load_capability_catalog())
    empty_payload["capabilities"][0]["scenario_slugs"] = []
    with pytest.raises(ValidationError, match="at least one scenario"):
        CapabilityCatalog.model_validate(empty_payload)


@pytest.mark.parametrize("failure", ["hash", "slug"])
def test_capability_loader_rejects_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    capability_payload = _payload(load_capability_catalog())
    if failure == "hash":
        capability_payload["scenario_catalog_sha256"] = "0" * 64
        expected = "not bound"
    else:
        capability_payload["capabilities"][0]["scenario_slugs"] = ["unknown-scenario"]
        expected = "unknown scenarios"
    _rehash(capability_payload)
    real_resource_json = catalog_module._resource_json

    def fake_resource_json(name: str) -> dict[str, Any]:
        if name == "capability-catalog.json":
            return capability_payload
        return real_resource_json(name)

    monkeypatch.setattr(catalog_module, "_resource_json", fake_resource_json)
    with pytest.raises(ValueError, match=expected):
        load_capability_catalog()


def test_unknown_catalog_aliases_fail_closed() -> None:
    with pytest.raises(KeyError, match="unknown scenario"):
        find_scenario("not-a-scenario")
    with pytest.raises(KeyError, match="unknown capability"):
        find_capability("not-a-capability")
