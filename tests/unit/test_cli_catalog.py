from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from finreplay.cli import app

runner = CliRunner()


def test_root_help_exposes_unified_command_groups() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "adapter" in result.output
    assert "scenario" in result.output
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
