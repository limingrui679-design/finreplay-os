from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from finreplay.catalog import (
    find_scenario,
    load_adapter_catalog,
    load_scenario_catalog,
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
    assert "catalogs_current=true adapters=30 scenarios=30 input_locks=30" in completed.stdout


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
