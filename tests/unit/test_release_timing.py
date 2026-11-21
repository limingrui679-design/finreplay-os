from dataclasses import replace
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from typer.testing import CliRunner

from finreplay.cli import app
from finreplay.engines.release_timing import ReleasePath, evaluate_timing, quarter_index, read_paths

PANEL = Path(__file__).resolve().parents[2] / "examples/release_timing/quarterly_output.csv"
HEADER = "period,age1_previous,age1_current,age2_previous,age2_current,age5_previous,age5_current\n"


def test_rates_use_two_levels_within_each_vintage_and_expose_incomplete_paths() -> None:
    sample = "1990Q1,100,110,1000,1100,10,11\n1990Q2,1,,1,1,1,1\n"
    paths, incomplete = read_paths(StringIO(HEADER + sample))
    assert paths == [ReleasePath("1990Q1", *(Decimal("46.41") for _ in range(3)))]
    assert incomplete == ["1990Q2"]


@pytest.mark.parametrize(
    "rows",
    [
        "1990Q1,1,NaN,1,1,1,1",
        "1990Q5,1,1,1,1,1,1",
        "1990Q1,1,1,0,1,1,1",
        "1990Q1,1,1,1,1,wrong,1",
        "1990Q1,1,1,1,1,1,1,1",
        "1990Q1,1,1",
        "1990Q1,1,1,1,1,1,1\n1990Q1,1,1,1,1,1,1",
    ],
)
def test_rejects_ambiguous_or_invalid_panels(rows: str) -> None:
    with pytest.raises(ValueError, match=r"Invalid|finite|Malformed|Duplicate"):
        read_paths(StringIO(HEADER + rows))


def test_walk_forward_uses_only_matured_quarters_and_is_order_invariant() -> None:
    with PANEL.open() as source:
        paths, _ = read_paths(source)
    report = evaluate_timing(paths[:60])
    assert report == evaluate_timing(list(reversed(paths[:60])))
    decisions = report["decisions"]
    assert len(decisions) > 1
    for row in decisions:
        assert quarter_index(row["last_training_period"]) + 5 < quarter_index(row["period"]) + 1
    target = decisions[-1]["period"]
    decision = decisions[-1]
    # Rewrite terminal vintages that were unpublished when this choice was due.
    altered = [
        replace(p, reference=-p.reference)
        if quarter_index(p.period) + 5 >= quarter_index(target) + 1
        else p
        for p in paths[:60]
    ]
    after = evaluate_timing(altered)["decisions"][-1]
    assert decision["period"] == after["period"]
    assert decision["margin"] == after["margin"]
    assert decision["delayed"] == after["delayed"]
    assert decision["contraction_flag"] == after["contraction_flag"]
    assert decision["training_periods"] == after["training_periods"]


def test_archive_and_cost_sensitivity_against_paired_fixed_policies() -> None:
    with PANEL.open() as source:
        paths, incomplete = read_paths(source)
    result = evaluate_timing(paths)
    assert len(incomplete) == 6
    assert (result["complete_periods"], result["warmup_periods"], result["evaluated_periods"]) == (
        239,
        44,
        195,
    )
    assert result["policies"] == {
        "act_immediately": {"disagreements": 6, "delayed_quarters": 0, "loss": "6.00"},
        "always_wait": {"disagreements": 4, "delayed_quarters": 195, "loss": "23.50"},
        "adaptive_margin": {"disagreements": 5, "delayed_quarters": 7, "loss": "5.70"},
    }
    assert result["paired_vs_immediate"]["corrected_disagreements"] == 2
    assert result["paired_vs_immediate"]["introduced_disagreements"] == 1
    assert (
        evaluate_timing(paths, delay_cost=Decimal("0.25"))["policies"]["adaptive_margin"]["loss"]
        == "6.50"
    )


def test_cli_exports_decisions_and_exclusions() -> None:
    import json

    runner = CliRunner()
    summary = runner.invoke(app, ["release-timing", str(PANEL)])
    assert summary.exit_code == 0
    result = json.loads(summary.stdout)
    assert "decisions" not in result
    assert "1995Q4" in result["excluded_incomplete_periods"]
    detail = runner.invoke(app, ["release-timing", str(PANEL), "--show-decisions"])
    assert detail.exit_code == 0
    assert len(json.loads(detail.stdout)["decisions"]) == 195
    assert runner.invoke(app, ["release-timing", str(PANEL), "--delay-cost", "NaN"]).exit_code != 0
