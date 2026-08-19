from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from finreplay.cli import app

runner = CliRunner()


def test_root_help_exposes_unified_command_groups() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "adapter" in result.output
    assert "scenario" in result.output
    assert "capability" in result.output
    assert "replaypack" in result.output
    assert "evidence" in result.output
    assert "demo" in result.output


def test_adapter_catalog_commands_keep_temporal_boundary_visible() -> None:
    listing = runner.invoke(app, ["adapter", "list", "--historical-only"])
    detail = runner.invoke(app, ["adapter", "show", "fdic.bankfind.financials"])

    assert listing.exit_code == 0
    assert "count=3 formal_live_total=30 historical_replay_eligible=3" in listing.output
    assert detail.exit_code == 0
    assert '"temporal_coverage": "latest_only"' in detail.output
    assert '"historical_replay_eligible": false' in detail.output


def test_scenario_verification_matches_recorded_pack_hash() -> None:
    result = runner.invoke(app, ["scenario", "verify", "svb-2023"])

    assert result.exit_code == 0
    assert "verified=true offline=true scenario=svb-2023" in result.output
    assert "c62c22dcbd15e29592a10811117a565d2bf9bee34877a4fbcbf24994383efd35" in (result.output)


def test_scenario_explanation_and_pathways_keep_case_context_bounded() -> None:
    explanation = runner.invoke(app, ["scenario", "explain", "svb-2023"])
    pathways = runner.invoke(app, ["scenario", "pathways"])

    assert explanation.exit_code == 0
    assert '"primary_method": "Seven-engine point-in-time boundary replay"' in explanation.output
    assert '"decision_question"' in explanation.output
    assert '"capability_ids"' in explanation.output
    assert '"claim_boundary"' in explanation.output
    assert pathways.exit_code == 0
    assert "funding-liquidity-and-allocation\t5" in pathways.output
    assert "count=5 dimensions=10 scenarios=30" in pathways.output
    assert "do not add evidence" in pathways.output


def test_capability_commands_keep_scope_and_negative_boundary_visible() -> None:
    listing = runner.invoke(app, ["capability", "list", "--scope", "boundary_only"])
    detail = runner.invoke(app, ["capability", "show", "decision-risk"])

    assert listing.exit_code == 0
    assert "count=2 capability_total=10" in listing.output
    assert "not evidence of domain deployment" in listing.output
    assert detail.exit_code == 0
    assert '"scope": "direct"' in detail.output
    assert '"scenario_slugs"' in detail.output
    assert '"does_not_prove"' in detail.output


def test_capability_cli_json_and_invalid_inputs_fail_predictably() -> None:
    listing = runner.invoke(app, ["capability", "list", "--scope", "direct", "--json"])
    invalid_scope = runner.invoke(app, ["capability", "list", "--scope", "adjacent"])
    unknown_capability = runner.invoke(app, ["capability", "show", "not-a-capability"])
    unknown_scenario = runner.invoke(app, ["scenario", "explain", "not-a-scenario"])

    assert listing.exit_code == 0
    payload = json.loads(listing.output)
    assert payload
    assert {entry["scope"] for entry in payload} == {"direct"}
    assert invalid_scope.exit_code == 2
    assert "must be direct, transferable, or boundary_only" in invalid_scope.output
    assert unknown_capability.exit_code == 2
    assert "unknown capability" in unknown_capability.output
    assert unknown_scenario.exit_code == 2
    assert "unknown scenario" in unknown_scenario.output


def test_evidence_verification_reports_all_navigation_catalogs() -> None:
    result = runner.invoke(app, ["evidence", "verify"])

    assert result.exit_code == 0
    assert "adapters=30 scenarios=30 capabilities=10" in result.output
    assert "dimensions=10 pathways=5 scenarios_rerun=0" in result.output


def test_demo_builds_and_replaypack_command_verifies(tmp_path: Path) -> None:
    destination = tmp_path / "svb-demo"
    demo = runner.invoke(
        app,
        [
            "demo",
            "svb-2023",
            "--destination",
            str(destination),
            "--offline",
            "--no-archive",
            "--no-open",
        ],
    )
    verified = runner.invoke(app, ["replaypack", "verify", str(destination)])

    assert demo.exit_code == 0
    assert "demo_complete=true offline=true scenario=svb-2023 engines=7" in demo.output
    assert (destination / "index.html").is_file()
    assert verified.exit_code == 0
    assert "verified=true replay_id=svb-2023-seven-engine-v1" in verified.output
