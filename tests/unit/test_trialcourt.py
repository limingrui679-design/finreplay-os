from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from finreplay.contracts import CostModel, TrialDisposition, TrialSpec
from finreplay.engines import (
    AttackKind,
    FindingStatus,
    TrialAttempt,
    TrialCourt,
    TrialLedgerMutationError,
    holm_adjusted_p_values,
)

REGISTERED = datetime(2022, 12, 1, tzinfo=UTC)
RECORDED = datetime(2024, 1, 15, tzinfo=UTC)


def trial_spec(
    *,
    declared_attempts: int = 3,
    expected_direction: str = "positive",
    purge_days: int = 5,
    embargo_days: int = 5,
) -> TrialSpec:
    return TrialSpec(
        trial_id="svb-deposit-risk-signal",
        hypothesis=(
            "A preregistered deposit-risk signal has positive holdout information content after "
            "multiplicity and execution attacks."
        ),
        economic_mechanism=(
            "Uninsured deposit concentration and securities-duration sensitivity can increase "
            "funding fragility when rates rise and depositor confidence changes."
        ),
        preregistered_at=REGISTERED,
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2023, 12, 31),
        purge_days=purge_days,
        embargo_days=embargo_days,
        declared_attempts=declared_attempts,
        primary_metric="rank information coefficient",
        expected_direction=expected_direction,
        cost_model=CostModel(
            commission_bps=1.0,
            half_spread_bps=3.0,
            market_impact_bps=4.0,
            borrow_bps_annual=100.0,
            max_participation_rate=0.05,
        ),
    )


def attempt(
    number: int,
    *,
    p_value: float = 0.001,
    metric_value: float = 0.08,
    gross_return_bps: float = 100.0,
    turnover: float = 1.0,
    short_fraction: float = 0.0,
    capital: float = 10_000_000.0,
    adv: float = 50_000_000.0,
    max_available_at: datetime = datetime(2022, 12, 20, tzinfo=UTC),
    training_end: date = date(2022, 12, 20),
    evaluation_start: date = date(2023, 1, 1),
    evaluation_end: date = date(2023, 12, 31),
    next_training_start: date | None = None,
    regimes: dict[str, float] | None = None,
) -> TrialAttempt:
    token = str(number)
    return TrialAttempt(
        attempt_id=f"svb-attempt-{number}",
        trial_id="svb-deposit-risk-signal",
        attempt_number=number,
        completed_at=RECORDED - timedelta(days=4 - number),
        code_commit="a" * 40,
        config_sha256=hashlib.sha256(f"config-{token}".encode()).hexdigest(),
        input_manifest_sha256=hashlib.sha256(f"input-{token}".encode()).hexdigest(),
        output_manifest_sha256=hashlib.sha256(f"output-{token}".encode()).hexdigest(),
        decision_time=datetime(2022, 12, 31, 23, 59, tzinfo=UTC),
        max_input_available_at=max_available_at,
        training_end=training_end,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        next_training_start=next_training_start,
        sample_size=250,
        metric_value=metric_value,
        p_value=p_value,
        gross_return_bps=gross_return_bps,
        one_way_turnover=turnover,
        short_fraction=short_fraction,
        requested_capital_usd=capital,
        median_daily_volume_usd=adv,
        regime_metric_values=regimes or {"low-volatility": 0.06, "high-volatility": 0.03},
        notes=("Fixture attempt; not investment performance.",),
    )


def trial_clock(
    registered_at: datetime = REGISTERED,
    recorded_at: datetime = RECORDED,
) -> Callable[[], datetime]:
    calls = 0

    def now() -> datetime:
        nonlocal calls
        calls += 1
        return registered_at if calls == 1 else recorded_at

    return now


def test_holm_adjustment_matches_known_step_down_example() -> None:
    adjusted = holm_adjusted_p_values(
        {"a": 0.01, "b": 0.04, "c": 0.03}, family_size=3
    )
    assert adjusted == pytest.approx({"a": 0.03, "b": 0.06, "c": 0.06})
    assert holm_adjusted_p_values({"a": 0.01}, family_size=5)["a"] == pytest.approx(0.05)

    with pytest.raises(ValueError, match="must not be empty"):
        holm_adjusted_p_values({})
    with pytest.raises(ValueError, match=r"in \[0, 1\]"):
        holm_adjusted_p_values({"bad": float("nan")})
    with pytest.raises(ValueError, match="smaller"):
        holm_adjusted_p_values({"a": 0.1, "b": 0.2}, family_size=1)


def test_registration_attempt_and_decision_are_hash_chained_and_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "trialcourt.duckdb"
    spec = trial_spec(declared_attempts=1)
    candidate = attempt(1)
    with TrialCourt(database, clock=trial_clock()) as court:
        registration = court.register(spec)
        assert registration.inserted is True
        assert court.register(spec).inserted is False
        first = court.record_attempt(candidate)
        assert first.inserted is True
        assert court.record_attempt(candidate).inserted is False
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=candidate.attempt_id)
        assert decision.disposition is TrialDisposition.ELIGIBLE
        assert court.adjudicate(
            spec.trial_id, candidate_attempt_id=candidate.attempt_id
        ) == decision
        manifest = court.manifest()
    assert manifest.registrations == 1
    assert manifest.attempts == 1
    assert manifest.decisions == 1
    assert manifest.rejected_decisions == 0
    assert manifest.entries == 3
    assert manifest.verified is True
    assert manifest.chain_tip_sha256 != "0" * 64


def test_registration_and_attempt_content_cannot_mutate() -> None:
    spec = trial_spec(declared_attempts=1)
    candidate = attempt(1)
    with TrialCourt(clock=trial_clock()) as court:
        court.register(spec)
        with pytest.raises(TrialLedgerMutationError, match="registration"):
            court.register(spec.model_copy(update={"primary_metric": "mutated metric"}))
        court.record_attempt(candidate)
        with pytest.raises(TrialLedgerMutationError, match="attempt"):
            court.record_attempt(candidate.model_copy(update={"p_value": 0.5}))


def test_attempt_sequence_cannot_skip_or_conceal_failures() -> None:
    with TrialCourt(clock=trial_clock()) as court:
        court.register(trial_spec())
        with pytest.raises(ValueError, match="must be 1"):
            court.record_attempt(attempt(2))
        court.record_attempt(attempt(1, p_value=0.9, metric_value=-0.01))
        court.record_attempt(attempt(2, p_value=0.001))
        assert [item.attempt_number for item in court.attempts("svb-deposit-risk-signal")] == [
            1,
            2,
        ]


def test_all_six_attacks_pass_for_clean_candidate() -> None:
    spec = trial_spec(declared_attempts=1)
    candidate = attempt(1)
    with TrialCourt(clock=trial_clock()) as court:
        court.register(spec)
        court.record_attempt(candidate)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=candidate.attempt_id)
    assert {finding.kind for finding in decision.findings} == set(AttackKind)
    assert all(finding.status is FindingStatus.PASS for finding in decision.findings)
    assert decision.adjusted_p_values[candidate.attempt_id] == pytest.approx(0.001)


@pytest.mark.parametrize(
    ("spec", "candidate", "expected_kind"),
    [
        (
            trial_spec(declared_attempts=3),
            attempt(1, p_value=0.02),
            AttackKind.MULTIPLICITY,
        ),
        (
            trial_spec(declared_attempts=1),
            attempt(
                1,
                max_available_at=datetime(2023, 1, 2, tzinfo=UTC),
            ),
            AttackKind.LEAKAGE,
        ),
        (
            trial_spec(declared_attempts=1),
            attempt(1, regimes={"calm": 0.05, "stress": -0.01}),
            AttackKind.REGIME,
        ),
        (
            trial_spec(declared_attempts=1),
            attempt(1, gross_return_bps=5.0, turnover=1.0),
            AttackKind.EXECUTION,
        ),
        (
            trial_spec(declared_attempts=1),
            attempt(1, turnover=252.0, capital=100_000_000.0, adv=10_000_000.0),
            AttackKind.CAPACITY,
        ),
    ],
)
def test_each_adversarial_failure_rejects_candidate(
    spec: TrialSpec, candidate: TrialAttempt, expected_kind: AttackKind
) -> None:
    with TrialCourt(clock=trial_clock()) as court:
        court.register(spec)
        court.record_attempt(candidate)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=candidate.attempt_id)
    assert decision.disposition is TrialDisposition.REJECT
    finding = next(item for item in decision.findings if item.kind is expected_kind)
    assert finding.status is FindingStatus.FAIL


def test_retrospective_registration_and_undeclared_attempt_family_fail() -> None:
    retrospective_clock = trial_clock(
        datetime(2023, 2, 1, tzinfo=UTC),
        datetime(2023, 2, 1, tzinfo=UTC),
    )
    spec = trial_spec(declared_attempts=1)
    candidate = attempt(1)
    with TrialCourt(clock=retrospective_clock) as court:
        court.register(spec)
        court.record_attempt(
            candidate.model_copy(
                update={"completed_at": datetime(2023, 1, 31, tzinfo=UTC)}
            )
        )
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=candidate.attempt_id)
    finding = next(item for item in decision.findings if item.kind is AttackKind.PREREGISTRATION)
    assert finding.status is FindingStatus.FAIL
    assert decision.disposition is TrialDisposition.REJECT


def test_negative_result_is_retained_and_included_in_multiple_testing_family() -> None:
    spec = trial_spec(declared_attempts=2)
    failed = attempt(1, p_value=0.8, metric_value=-0.02, regimes={"calm": -0.01, "stress": -0.03})
    candidate = attempt(2, p_value=0.02)
    with TrialCourt(clock=trial_clock()) as court:
        court.register(spec)
        court.record_attempt(failed)
        court.record_attempt(candidate)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=candidate.attempt_id)
        manifest = court.manifest()
        stored_attempts = court.attempts(spec.trial_id)
    assert manifest.attempts == 2
    assert stored_attempts == (failed, candidate)
    assert decision.attempt_ids == (failed.attempt_id, candidate.attempt_id)
    assert decision.adjusted_p_values[candidate.attempt_id] == pytest.approx(0.04)


def test_chain_verification_detects_database_tampering(tmp_path: Path) -> None:
    database = tmp_path / "tamper.duckdb"
    with TrialCourt(database, clock=trial_clock()) as court:
        court.register(trial_spec(declared_attempts=1))
        court.record_attempt(attempt(1))
        assert court.verify_chain() is True
    connection = duckdb.connect(str(database))
    connection.execute(
        "UPDATE ledger_entries SET payload_json = ?::JSON WHERE entry_kind = 'attempt'",
        ['{"tampered":true}'],
    )
    connection.close()
    with TrialCourt(database, clock=trial_clock()) as court:
        assert court.verify_chain() is False


def test_attempt_contract_rejects_invalid_clocks_ranges_and_nonfinite_values() -> None:
    payload = attempt(1).model_dump()
    payload["decision_time"] = datetime(2022, 12, 31, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValidationError, match="timezone-aware"):
        TrialAttempt.model_validate(payload)
    payload = attempt(1).model_dump()
    payload["evaluation_end"] = date(2022, 1, 1)
    with pytest.raises(ValidationError, match="evaluation_end"):
        TrialAttempt.model_validate(payload)
    payload = attempt(1).model_dump()
    payload["metric_value"] = float("inf")
    with pytest.raises(ValidationError, match="finite"):
        TrialAttempt.model_validate(payload)


def test_unregistered_future_or_unknown_attempts_fail_closed() -> None:
    with TrialCourt(clock=trial_clock()) as court:
        with pytest.raises(KeyError, match="not registered"):
            court.record_attempt(attempt(1))
        court.register(trial_spec(declared_attempts=1))
        future = attempt(1).model_copy(update={"completed_at": RECORDED + timedelta(seconds=1)})
        with pytest.raises(ValueError, match="after ledger"):
            court.record_attempt(future)
        court.record_attempt(attempt(1))
        with pytest.raises(ValueError, match="not in the trial ledger"):
            court.adjudicate("svb-deposit-risk-signal", candidate_attempt_id="unknown")
