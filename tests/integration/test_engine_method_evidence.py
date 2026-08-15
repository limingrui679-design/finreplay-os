from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from finreplay.contracts import EvidenceClass, ScenarioMode, TemporalCoverage
from finreplay.engines import (
    CompiledShockProgram,
    ContagionResult,
    MarketSnapshot,
    ShockProgram,
    TimeVault,
    holm_adjusted_p_values,
)
from finreplay.scenarios import MacroRevisionInputLock, OfficialEventLock

REPOSITORY = Path(__file__).resolve().parents[2]
EVIDENCE = REPOSITORY / "verification" / "evidence"


def test_timevault_real_alfred_replay_excludes_future_revision() -> None:
    decision_lock = MacroRevisionInputLock.model_validate_json(
        (REPOSITORY / "scenarios/gdp-revision-2022q4/input-lock.json").read_text(
            encoding="utf-8"
        )
    )
    event_lock = OfficialEventLock.model_validate_json(
        (REPOSITORY / "scenarios/gdp-revision-2022q4/event-lock.json").read_text(
            encoding="utf-8"
        )
    )
    event = event_lock.records[0]
    with TimeVault() as vault:
        receipt = vault.append((*decision_lock.records, event))
        decision_records = vault.records_as_of(decision_lock.decision_time)
        post_event_records = vault.records_as_of(event.interval.available_at)

    decision_ids = {record.record_id for record in decision_records}
    assert receipt.inserted_records == 5
    assert decision_ids == {record.record_id for record in decision_lock.records}
    assert event.record_id not in decision_ids
    assert event.record_id in {record.record_id for record in post_event_records}


def test_trialcourt_receipt_matches_published_holm_procedure() -> None:
    receipt = _load_self_hashed(EVIDENCE / "trialcourt-holm-method.json")
    assert receipt["citation"]["doi"] == "10.2307/4615733"
    assert receipt["citation_sha256"] == _hash(receipt["citation"])
    implementation = REPOSITORY / cast(str, receipt["implementation_path"])
    assert receipt["implementation_sha256"] == hashlib.sha256(
        implementation.read_bytes()
    ).hexdigest()
    raw = cast(dict[str, float], receipt["raw_p_values"])
    observed = holm_adjusted_p_values(raw, family_size=cast(int, receipt["family_size"]))
    assert receipt["exact_match"] is True
    assert observed == receipt["expected_adjusted_p_values"]
    assert observed == receipt["trialcourt_adjusted_p_values"]
    assert observed == {
        "hypothesis-a": 0.03,
        "hypothesis-b": 0.06,
        "hypothesis-c": 0.06,
    }


def test_markettwin_official_multisource_receipt_is_bound_and_bounded() -> None:
    receipt = _load_self_hashed(EVIDENCE / "svb-markettwin.json")
    historical = MarketSnapshot.model_validate(receipt["historical_safe_snapshot"])
    current = MarketSnapshot.model_validate(receipt["current_multisource_snapshot"])
    shock = ContagionResult.model_validate(receipt["bounded_shock"])

    historical_sources = {
        node.source.source_id for node in historical.nodes if node.source is not None
    }
    current_sources = {node.source.source_id for node in current.nodes if node.source is not None}
    assert historical_sources == {"sec.xbrl.companyfacts"}
    assert current_sources == {
        "fdic.bankfind.financials",
        "sec.xbrl.companyfacts",
        "treasury.fiscaldata.debt_to_penny",
    }
    assert len(historical.nodes) == 3
    assert len(historical.edges) == 2
    assert len(current.nodes) == 5
    assert len(current.edges) == 4
    assert {
        node.source.temporal_coverage
        for node in current.nodes
        if node.source is not None
        and node.source.source_id
        in {"fdic.bankfind.financials", "treasury.fiscaldata.debt_to_penny"}
    } == {TemporalCoverage.LATEST_ONLY}
    assert shock.snapshot_sha256 == historical.graph_sha256
    assert shock.converged is True
    assert all(
        shock.lower_loss_fraction[node_id] <= value
        for node_id, value in shock.upper_loss_fraction.items()
    )


def test_shockcompiler_four_mode_receipt_preserves_provenance() -> None:
    receipt = _load_self_hashed(EVIDENCE / "svb-shock-programs.json")
    programs = tuple(ShockProgram.model_validate(value) for value in receipt["programs"])
    compiled = tuple(
        CompiledShockProgram.model_validate(value) for value in receipt["compiled"]
    )
    expected_modes = {
        ScenarioMode.OBSERVED_RECONSTRUCTION,
        ScenarioMode.BOUNDED_RECONSTRUCTION,
        ScenarioMode.COUNTERFACTUAL,
        ScenarioMode.ADVERSARIAL,
    }
    assert {program.mode for program in programs} == expected_modes
    assert {result.mode for result in compiled} == expected_modes
    assert receipt["mode_trial_counts"] == {
        "observed_reconstruction": 1,
        "bounded_reconstruction": 2,
        "counterfactual": 1,
        "adversarial": 12,
    }
    for program, result in zip(programs, compiled, strict=True):
        assert result.mode is program.mode
        for parameter in program.parameters:
            if program.mode in {
                ScenarioMode.OBSERVED_RECONSTRUCTION,
                ScenarioMode.BOUNDED_RECONSTRUCTION,
            }:
                assert parameter.evidence_class is not EvidenceClass.SIMULATED
                assert parameter.source_record_ids
                assert parameter.sources
            else:
                assert parameter.evidence_class is EvidenceClass.SIMULATED
                assert not parameter.source_record_ids
                assert not parameter.sources
        for trial in result.trials:
            for shock in trial.shocks:
                if shock.evidence_class is EvidenceClass.SIMULATED:
                    assert not shock.source_record_ids
                    assert not shock.source_hashes
                else:
                    assert shock.source_record_ids
                    assert shock.source_hashes


def _load_self_hashed(path: Path) -> dict[str, Any]:
    values = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    claimed = values.pop("receipt_sha256")
    assert claimed == _hash(values)
    values["receipt_sha256"] = claimed
    return values


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
