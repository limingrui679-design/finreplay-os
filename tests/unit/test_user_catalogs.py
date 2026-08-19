from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from finreplay.catalog import (
    find_capability,
    find_scenario,
    load_adapter_catalog,
    load_capability_catalog,
    load_scenario_catalog,
    load_scenario_explorer_catalog,
    run_scenario,
)
from finreplay.engines import EngineName, ReplayStudio

REPOSITORY = Path(__file__).resolve().parents[2]


def test_installable_catalogs_match_verified_repository_sources() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/build_user_catalogs.py", "--check"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (
        "catalogs_current=true adapters=30 scenarios=30 capabilities=10 input_locks=30"
        in completed.stdout
    )


def test_adapter_catalog_discloses_temporal_eligibility() -> None:
    catalog = load_adapter_catalog()
    assert catalog.adapter_count == 30
    assert catalog.historical_replay_eligible_count == 3
    assert catalog.temporal_coverage_counts == {"immutable_event": 7, "latest_only": 23}
    assert "not automatically eligible" in catalog.claim_boundary


def test_scenario_catalog_resolves_all_public_identities() -> None:
    catalog = load_scenario_catalog()
    assert catalog.scenario_count == 30
    assert len({entry.slug for entry in catalog.scenarios}) == 30
    for entry in catalog.scenarios:
        assert find_scenario(entry.slug) == entry
        assert find_scenario(entry.scenario_id) == entry
        assert find_scenario(entry.replay_id) == entry


def test_capability_catalog_is_bounded_and_scenario_complete() -> None:
    catalog = load_capability_catalog()
    scenario_slugs = {entry.slug for entry in load_scenario_catalog().scenarios}

    assert catalog.capability_count == 10
    assert {entry.scope for entry in catalog.capabilities} == {
        "direct",
        "transferable",
        "boundary_only",
    }
    for entry in catalog.capabilities:
        assert find_capability(entry.capability_id) == entry
        assert set(entry.scenario_slugs) <= scenario_slugs
        assert entry.evidence_locators
        assert entry.does_not_prove
    assert "not evidence of domain deployment" in catalog.claim_boundary


def test_scenario_explorer_covers_every_case_dimension_and_pathway() -> None:
    explorer = load_scenario_explorer_catalog()
    scenario_slugs = {entry.slug for entry in load_scenario_catalog().scenarios}
    lens_ids = {lens.lens_id for lens in explorer.lenses}

    assert explorer.scenario_count == 30
    assert len(explorer.lenses) == 10
    assert len(explorer.pathways) == 5
    assert {entry.slug for entry in explorer.scenarios} == scenario_slugs
    assert Counter(entry.tone for entry in explorer.scenarios) == {
        "boundary": 3,
        "inside": 8,
        "breach": 19,
    }
    assert {lens_id for entry in explorer.scenarios for lens_id in entry.lens_ids} == lens_ids
    for pathway in explorer.pathways:
        assert set(pathway.lens_ids) <= lens_ids
        assert set(pathway.scenario_slugs) <= scenario_slugs
    assert "do not add evidence" in explorer.claim_boundary


def test_bundled_svb_demo_runs_all_seven_engines(tmp_path: Path) -> None:
    destination = tmp_path / "svb-demo"
    archive = tmp_path / "svb-demo.zip"
    result = run_scenario("svb-2023", destination, archive=archive)
    receipt = ReplayStudio().verify(destination)
    report = json.loads((destination / "report.json").read_text(encoding="utf-8"))
    assert result.root == destination.resolve()
    assert receipt.replay_id == "svb-2023-seven-engine-v1"
    assert set(report["engine_artifact_counts"]) == {engine.value for engine in EngineName}
    assert archive.is_file()
