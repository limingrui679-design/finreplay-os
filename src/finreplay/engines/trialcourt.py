"""Immutable experiment ledger and adversarial adjudication for research claims."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from finreplay.contracts import TrialDisposition, TrialSpec


class TrialLedgerMutationError(RuntimeError):
    """Raised when an immutable registration, attempt, or decision changes in place."""


class AttackKind(StrEnum):
    PREREGISTRATION = "preregistration"
    LEAKAGE = "leakage"
    MULTIPLICITY = "multiplicity"
    REGIME = "regime"
    EXECUTION = "execution"
    CAPACITY = "capacity"


class FindingStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TrialAttempt(_StrictModel):
    """One disclosed research attempt, including failures and operational assumptions."""

    attempt_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    trial_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    attempt_number: int = Field(ge=1)
    completed_at: datetime
    code_commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|uncommitted)$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_time: datetime
    max_input_available_at: datetime
    training_end: date
    evaluation_start: date
    evaluation_end: date
    next_training_start: date | None = None
    sample_size: int = Field(ge=2)
    metric_value: float
    p_value: float = Field(ge=0.0, le=1.0)
    gross_return_bps: float
    one_way_turnover: float = Field(ge=0.0)
    short_fraction: float = Field(ge=0.0, le=1.0)
    requested_capital_usd: float = Field(gt=0.0)
    median_daily_volume_usd: float = Field(gt=0.0)
    regime_metric_values: dict[str, float]
    notes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_attempt(self) -> TrialAttempt:
        for name in (
            "completed_at",
            "decision_time",
            "max_input_available_at",
        ):
            _require_aware(getattr(self, name), name)
        if self.evaluation_end < self.evaluation_start:
            raise ValueError("evaluation_end must not precede evaluation_start")
        finite_values = (
            self.metric_value,
            self.gross_return_bps,
            self.one_way_turnover,
            self.short_fraction,
            self.requested_capital_usd,
            self.median_daily_volume_usd,
            *self.regime_metric_values.values(),
        )
        if any(not math.isfinite(value) for value in finite_values):
            raise ValueError("attempt numeric fields must be finite")
        if any(not key.strip() for key in self.regime_metric_values):
            raise ValueError("regime names must be non-empty")
        if any(not note.strip() for note in self.notes):
            raise ValueError("attempt notes must be non-empty")
        return self


class AttackFinding(_StrictModel):
    kind: AttackKind
    status: FindingStatus
    summary: str = Field(min_length=3, max_length=1000)
    metrics: dict[str, bool | float | int | str]


class TrialDecision(_StrictModel):
    """Deterministic decision over the complete disclosed attempt set at adjudication time."""

    decision_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,199}$")
    trial_id: str
    candidate_attempt_id: str
    decided_at: datetime
    attempt_ids: tuple[str, ...] = Field(min_length=1)
    attempt_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adjusted_p_values: dict[str, float]
    findings: tuple[AttackFinding, ...] = Field(min_length=6)
    disposition: TrialDisposition

    @model_validator(mode="after")
    def validate_decision(self) -> TrialDecision:
        _require_aware(self.decided_at, "decided_at")
        if self.candidate_attempt_id not in self.attempt_ids:
            raise ValueError("candidate_attempt_id must be in attempt_ids")
        if set(self.adjusted_p_values) != set(self.attempt_ids):
            raise ValueError("adjusted p-values must cover every disclosed attempt")
        if {finding.kind for finding in self.findings} != set(AttackKind):
            raise ValueError("decision must contain every required attack kind exactly once")
        return self


@dataclass(frozen=True, slots=True)
class LedgerAppendReceipt:
    inserted: bool
    sequence: int
    entry_hash: str
    chain_tip_sha256: str


@dataclass(frozen=True, slots=True)
class TrialLedgerManifest:
    registrations: int
    attempts: int
    decisions: int
    rejected_decisions: int
    entries: int
    chain_tip_sha256: str
    verified: bool


class TrialAttackSuite:
    """Apply leakage, multiplicity, regime, execution and capacity attacks."""

    def __init__(self, *, alpha: float = 0.05) -> None:
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be between zero and one")
        self.alpha = alpha

    def adjudicate(
        self,
        *,
        spec: TrialSpec,
        registered_at: datetime,
        attempts: tuple[TrialAttempt, ...],
        candidate_attempt_id: str,
        decided_at: datetime,
    ) -> TrialDecision:
        if not attempts:
            raise ValueError("at least one disclosed attempt is required")
        candidates = {attempt.attempt_id: attempt for attempt in attempts}
        try:
            candidate = candidates[candidate_attempt_id]
        except KeyError as error:
            raise ValueError("candidate attempt is not in the trial ledger") from error
        attempt_ids = tuple(attempt.attempt_id for attempt in attempts)
        attempt_set_hash = _attempt_set_hash(attempts)
        adjusted = holm_adjusted_p_values(
            {attempt.attempt_id: attempt.p_value for attempt in attempts},
            family_size=max(spec.declared_attempts, len(attempts)),
        )
        findings = (
            self._preregistration(spec, registered_at, len(attempts)),
            self._leakage(spec, candidate),
            self._multiplicity(candidate, adjusted, len(attempts), spec.declared_attempts),
            self._regime(spec, candidate),
            self._execution(spec, candidate),
            self._capacity(spec, candidate),
        )
        disposition = (
            TrialDisposition.REJECT
            if any(finding.status is FindingStatus.FAIL for finding in findings)
            else TrialDisposition.REVISE
            if any(finding.status is FindingStatus.WARN for finding in findings)
            else TrialDisposition.ELIGIBLE
        )
        decision_suffix = hashlib.sha256(
            f"{spec.trial_id}:{candidate_attempt_id}:{attempt_set_hash}".encode()
        ).hexdigest()[:24]
        return TrialDecision(
            decision_id=f"decision:{spec.trial_id}:{decision_suffix}",
            trial_id=spec.trial_id,
            candidate_attempt_id=candidate_attempt_id,
            decided_at=decided_at,
            attempt_ids=attempt_ids,
            attempt_set_sha256=attempt_set_hash,
            adjusted_p_values=adjusted,
            findings=findings,
            disposition=disposition,
        )

    @staticmethod
    def _preregistration(
        spec: TrialSpec, registered_at: datetime, observed_attempts: int
    ) -> AttackFinding:
        holdout_start = datetime.combine(spec.holdout_start, datetime.min.time(), tzinfo=UTC)
        before_holdout = registered_at < holdout_start
        declared_enough = spec.declared_attempts >= observed_attempts
        status = FindingStatus.PASS if before_holdout and declared_enough else FindingStatus.FAIL
        return AttackFinding(
            kind=AttackKind.PREREGISTRATION,
            status=status,
            summary=(
                "Registration preceded the holdout and covered the observed attempt family."
                if status is FindingStatus.PASS
                else "Registration was retrospective or the observed attempt family exceeded "
                "the preregistered declaration."
            ),
            metrics={
                "registered_before_holdout": before_holdout,
                "declared_attempts": spec.declared_attempts,
                "observed_attempts": observed_attempts,
            },
        )

    @staticmethod
    def _leakage(spec: TrialSpec, attempt: TrialAttempt) -> AttackFinding:
        purge_cutoff = attempt.evaluation_start - timedelta(days=spec.purge_days)
        conditions: dict[str, bool | float | int | str] = {
            "input_available_by_decision": (
                attempt.max_input_available_at <= attempt.decision_time
            ),
            "training_respects_purge": attempt.training_end <= purge_cutoff,
            "evaluation_inside_holdout": (
                attempt.evaluation_start >= spec.holdout_start
                and attempt.evaluation_end <= spec.holdout_end
            ),
            "embargo_respected": (
                attempt.next_training_start is None
                or attempt.next_training_start
                >= attempt.evaluation_end + timedelta(days=spec.embargo_days)
            ),
        }
        status = FindingStatus.PASS if all(conditions.values()) else FindingStatus.FAIL
        return AttackFinding(
            kind=AttackKind.LEAKAGE,
            status=status,
            summary=(
                "Point-in-time input, purge, holdout and embargo checks passed."
                if status is FindingStatus.PASS
                else "At least one point-in-time, purge, holdout or embargo boundary failed."
            ),
            metrics=conditions,
        )

    def _multiplicity(
        self,
        candidate: TrialAttempt,
        adjusted: Mapping[str, float],
        observed_attempts: int,
        declared_attempts: int,
    ) -> AttackFinding:
        adjusted_p = adjusted[candidate.attempt_id]
        passed = adjusted_p <= self.alpha
        return AttackFinding(
            kind=AttackKind.MULTIPLICITY,
            status=FindingStatus.PASS if passed else FindingStatus.FAIL,
            summary=(
                "Candidate survives family-wise Holm adjustment."
                if passed
                else "Candidate does not survive family-wise Holm adjustment."
            ),
            metrics={
                "raw_p_value": candidate.p_value,
                "adjusted_p_value": adjusted_p,
                "alpha": self.alpha,
                "observed_attempts": observed_attempts,
                "declared_attempts": declared_attempts,
            },
        )

    @staticmethod
    def _regime(spec: TrialSpec, candidate: TrialAttempt) -> AttackFinding:
        values = candidate.regime_metric_values
        if len(values) < 2:
            return AttackFinding(
                kind=AttackKind.REGIME,
                status=FindingStatus.FAIL,
                summary="At least two named regime slices are required.",
                metrics={"regime_count": len(values)},
            )
        if spec.expected_direction == "positive":
            consistent = candidate.metric_value > 0 and all(value > 0 for value in values.values())
        elif spec.expected_direction == "negative":
            consistent = candidate.metric_value < 0 and all(value < 0 for value in values.values())
        elif spec.expected_direction == "non-inferior":
            consistent = candidate.metric_value >= 0 and all(
                value >= 0 for value in values.values()
            )
        else:
            sign = 1 if candidate.metric_value > 0 else -1 if candidate.metric_value < 0 else 0
            consistent = sign != 0 and all(value * sign > 0 for value in values.values())
        return AttackFinding(
            kind=AttackKind.REGIME,
            status=FindingStatus.PASS if consistent else FindingStatus.FAIL,
            summary=(
                "Metric direction is stable across named regimes."
                if consistent
                else "Metric direction reverses or vanishes in at least one named regime."
            ),
            metrics={
                "regime_count": len(values),
                "worst_regime_metric": min(values.values()),
                "best_regime_metric": max(values.values()),
            },
        )

    @staticmethod
    def _execution(spec: TrialSpec, candidate: TrialAttempt) -> AttackFinding:
        model = spec.cost_model
        immediate_cost_bps = candidate.one_way_turnover * (
            model.commission_bps + model.half_spread_bps + model.market_impact_bps
        )
        borrow_cost_bps = candidate.short_fraction * model.borrow_bps_annual
        net_return_bps = candidate.gross_return_bps - immediate_cost_bps - borrow_cost_bps
        passed = net_return_bps > 0
        return AttackFinding(
            kind=AttackKind.EXECUTION,
            status=FindingStatus.PASS if passed else FindingStatus.FAIL,
            summary=(
                "Return remains positive after preregistered non-zero costs."
                if passed
                else "Preregistered execution and borrow costs erase the gross return."
            ),
            metrics={
                "gross_return_bps": candidate.gross_return_bps,
                "immediate_cost_bps": immediate_cost_bps,
                "borrow_cost_bps": borrow_cost_bps,
                "net_return_bps": net_return_bps,
            },
        )

    @staticmethod
    def _capacity(spec: TrialSpec, candidate: TrialAttempt) -> AttackFinding:
        daily_notional_usd = candidate.requested_capital_usd * candidate.one_way_turnover / 252.0
        allowed_daily_notional_usd = (
            candidate.median_daily_volume_usd * spec.cost_model.max_participation_rate
        )
        passed = daily_notional_usd <= allowed_daily_notional_usd
        return AttackFinding(
            kind=AttackKind.CAPACITY,
            status=FindingStatus.PASS if passed else FindingStatus.FAIL,
            summary=(
                "Requested capital fits the preregistered participation limit."
                if passed
                else "Requested capital exceeds the preregistered participation limit."
            ),
            metrics={
                "daily_notional_usd": daily_notional_usd,
                "allowed_daily_notional_usd": allowed_daily_notional_usd,
                "max_participation_rate": spec.cost_model.max_participation_rate,
            },
        )


class TrialCourt:
    """Append-only hash-chained ledger for preregistrations, attempts and decisions."""

    def __init__(
        self,
        database: str | Path = ":memory:",
        *,
        clock: Callable[[], datetime] | None = None,
        attack_suite: TrialAttackSuite | None = None,
    ) -> None:
        self.database = str(database)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._attack_suite = attack_suite or TrialAttackSuite()
        self._connection = duckdb.connect(self.database)
        self._connection.execute("SET TimeZone = 'UTC'")
        self._initialize_schema()

    def __enter__(self) -> TrialCourt:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def register(self, spec: TrialSpec) -> LedgerAppendReceipt:
        now = self._now()
        payload = spec.model_dump(mode="json")
        payload_hash = _payload_hash(payload)
        existing = self._connection.execute(
            "SELECT spec_sha256 FROM registrations WHERE trial_id = ?", [spec.trial_id]
        ).fetchone()
        if existing is not None:
            if existing[0] != payload_hash:
                raise TrialLedgerMutationError(
                    f"trial registration {spec.trial_id!r} attempted to mutate"
                )
            return self._existing_receipt("registration", spec.trial_id)
        self._connection.execute("BEGIN TRANSACTION")
        try:
            self._connection.execute(
                "INSERT INTO registrations VALUES (?, ?::JSON, ?, ?)",
                [spec.trial_id, _canonical_json(payload), payload_hash, now],
            )
            receipt = self._append_entry_in_transaction(
                trial_id=spec.trial_id,
                entry_kind="registration",
                entry_id=spec.trial_id,
                payload=payload,
                recorded_at=now,
            )
            self._connection.execute("COMMIT")
            return receipt
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    def record_attempt(self, attempt: TrialAttempt) -> LedgerAppendReceipt:
        self._require_registration(attempt.trial_id)
        existing = self._find_entry("attempt", attempt.attempt_id)
        payload = attempt.model_dump(mode="json")
        if existing is not None:
            if existing[3] != _payload_hash(payload):
                raise TrialLedgerMutationError(
                    f"trial attempt {attempt.attempt_id!r} attempted to mutate"
                )
            return LedgerAppendReceipt(False, existing[0], existing[1], self._chain_tip())
        observed = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM ledger_entries WHERE trial_id = ? AND entry_kind = 'attempt'",
                [attempt.trial_id],
            ).fetchone()[0]
        )
        if attempt.attempt_number != observed + 1:
            raise ValueError(
                f"attempt_number must be {observed + 1}; gaps can conceal failed attempts"
            )
        now = self._now()
        if attempt.completed_at > now:
            raise ValueError("completed_at cannot be after ledger recording time")
        return self._append_entry(
            trial_id=attempt.trial_id,
            entry_kind="attempt",
            entry_id=attempt.attempt_id,
            payload=payload,
            recorded_at=now,
        )

    def adjudicate(self, trial_id: str, *, candidate_attempt_id: str) -> TrialDecision:
        spec, registered_at = self.registration(trial_id)
        attempts = self.attempts(trial_id)
        attempt_set_hash = _attempt_set_hash(attempts)
        suffix = hashlib.sha256(
            f"{trial_id}:{candidate_attempt_id}:{attempt_set_hash}".encode()
        ).hexdigest()[:24]
        decision_id = f"decision:{trial_id}:{suffix}"
        existing = self._find_entry("decision", decision_id)
        if existing is not None:
            return TrialDecision.model_validate_json(existing[2])
        decided_at = self._now()
        decision = self._attack_suite.adjudicate(
            spec=spec,
            registered_at=registered_at,
            attempts=attempts,
            candidate_attempt_id=candidate_attempt_id,
            decided_at=decided_at,
        )
        self._append_entry(
            trial_id=trial_id,
            entry_kind="decision",
            entry_id=decision.decision_id,
            payload=decision.model_dump(mode="json"),
            recorded_at=decided_at,
        )
        return decision

    def registration(self, trial_id: str) -> tuple[TrialSpec, datetime]:
        row = self._connection.execute(
            "SELECT spec_json, registered_at FROM registrations WHERE trial_id = ?", [trial_id]
        ).fetchone()
        if row is None:
            raise KeyError(f"trial {trial_id!r} is not registered")
        raw = row[0] if isinstance(row[0], str) else json.dumps(row[0])
        return TrialSpec.model_validate_json(raw), row[1]

    def attempts(self, trial_id: str) -> tuple[TrialAttempt, ...]:
        rows = self._connection.execute(
            """
            SELECT payload_json
            FROM ledger_entries
            WHERE trial_id = ? AND entry_kind = 'attempt'
            ORDER BY sequence
            """,
            [trial_id],
        ).fetchall()
        return tuple(
            TrialAttempt.model_validate_json(
                row[0] if isinstance(row[0], str) else json.dumps(row[0])
            )
            for row in rows
        )

    def decisions(self, trial_id: str) -> tuple[TrialDecision, ...]:
        rows = self._connection.execute(
            """
            SELECT payload_json
            FROM ledger_entries
            WHERE trial_id = ? AND entry_kind = 'decision'
            ORDER BY sequence
            """,
            [trial_id],
        ).fetchall()
        return tuple(
            TrialDecision.model_validate_json(
                row[0] if isinstance(row[0], str) else json.dumps(row[0])
            )
            for row in rows
        )

    def manifest(self) -> TrialLedgerManifest:
        verified = self.verify_chain()
        counts = dict(
            self._connection.execute(
                "SELECT entry_kind, COUNT(*) FROM ledger_entries GROUP BY entry_kind"
            ).fetchall()
        )
        rejected = 0
        for (raw,) in self._connection.execute(
            "SELECT payload_json FROM ledger_entries WHERE entry_kind = 'decision'"
        ).fetchall():
            payload = raw if isinstance(raw, str) else json.dumps(raw)
            rejected += (
                TrialDecision.model_validate_json(payload).disposition
                is TrialDisposition.REJECT
            )
        entries = sum(int(value) for value in counts.values())
        return TrialLedgerManifest(
            registrations=int(counts.get("registration", 0)),
            attempts=int(counts.get("attempt", 0)),
            decisions=int(counts.get("decision", 0)),
            rejected_decisions=int(rejected),
            entries=entries,
            chain_tip_sha256=self._chain_tip(),
            verified=verified,
        )

    def verify_chain(self) -> bool:
        rows = self._connection.execute(
            """
            SELECT sequence, entry_kind, entry_id, trial_id, previous_hash, payload_hash,
                   entry_hash, recorded_at, payload_json
            FROM ledger_entries ORDER BY sequence
            """
        ).fetchall()
        prior = "0" * 64
        for expected_sequence, row in enumerate(rows, start=1):
            (
                sequence,
                kind,
                entry_id,
                trial_id,
                previous,
                payload_hash,
                entry_hash,
                recorded,
                raw,
            ) = row
            if sequence != expected_sequence or previous != prior:
                return False
            payload = json.loads(raw) if isinstance(raw, str) else raw
            if _payload_hash(payload) != payload_hash:
                return False
            expected_hash = _entry_hash(
                sequence=sequence,
                trial_id=trial_id,
                entry_kind=kind,
                entry_id=entry_id,
                previous_hash=previous,
                payload_hash=payload_hash,
                recorded_at=recorded,
            )
            if expected_hash != entry_hash:
                return False
            prior = entry_hash
        return True

    def _initialize_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS registrations (
                trial_id VARCHAR PRIMARY KEY,
                spec_json JSON NOT NULL,
                spec_sha256 VARCHAR NOT NULL,
                registered_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_entries (
                sequence BIGINT PRIMARY KEY,
                trial_id VARCHAR NOT NULL REFERENCES registrations(trial_id),
                entry_kind VARCHAR NOT NULL,
                entry_id VARCHAR NOT NULL,
                previous_hash VARCHAR NOT NULL,
                payload_hash VARCHAR NOT NULL,
                entry_hash VARCHAR NOT NULL UNIQUE,
                recorded_at TIMESTAMPTZ NOT NULL,
                payload_json JSON NOT NULL,
                UNIQUE (entry_kind, entry_id)
            );
            """
        )

    def _append_entry(
        self,
        *,
        trial_id: str,
        entry_kind: str,
        entry_id: str,
        payload: dict[str, Any],
        recorded_at: datetime,
    ) -> LedgerAppendReceipt:
        self._connection.execute("BEGIN TRANSACTION")
        try:
            receipt = self._append_entry_in_transaction(
                trial_id=trial_id,
                entry_kind=entry_kind,
                entry_id=entry_id,
                payload=payload,
                recorded_at=recorded_at,
            )
            self._connection.execute("COMMIT")
            return receipt
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    def _append_entry_in_transaction(
        self,
        *,
        trial_id: str,
        entry_kind: str,
        entry_id: str,
        payload: dict[str, Any],
        recorded_at: datetime,
    ) -> LedgerAppendReceipt:
        prior = self._connection.execute(
            "SELECT sequence, entry_hash FROM ledger_entries ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = 1 if prior is None else int(prior[0]) + 1
        previous_hash = "0" * 64 if prior is None else str(prior[1])
        payload_hash = _payload_hash(payload)
        entry_hash = _entry_hash(
            sequence=sequence,
            trial_id=trial_id,
            entry_kind=entry_kind,
            entry_id=entry_id,
            previous_hash=previous_hash,
            payload_hash=payload_hash,
            recorded_at=recorded_at,
        )
        self._connection.execute(
            "INSERT INTO ledger_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)",
            [
                sequence,
                trial_id,
                entry_kind,
                entry_id,
                previous_hash,
                payload_hash,
                entry_hash,
                recorded_at,
                _canonical_json(payload),
            ],
        )
        return LedgerAppendReceipt(True, sequence, entry_hash, entry_hash)

    def _find_entry(self, kind: str, entry_id: str) -> tuple[int, str, str, str] | None:
        row = self._connection.execute(
            """
            SELECT sequence, entry_hash, payload_json, payload_hash
            FROM ledger_entries WHERE entry_kind = ? AND entry_id = ?
            """,
            [kind, entry_id],
        ).fetchone()
        if row is None:
            return None
        raw = row[2] if isinstance(row[2], str) else json.dumps(row[2])
        return int(row[0]), str(row[1]), raw, str(row[3])

    def _existing_receipt(self, kind: str, entry_id: str) -> LedgerAppendReceipt:
        existing = self._find_entry(kind, entry_id)
        if existing is None:  # pragma: no cover - internal database corruption guard
            raise RuntimeError("registration exists without its ledger entry")
        return LedgerAppendReceipt(False, existing[0], existing[1], self._chain_tip())

    def _require_registration(self, trial_id: str) -> None:
        if self._connection.execute(
            "SELECT 1 FROM registrations WHERE trial_id = ?", [trial_id]
        ).fetchone() is None:
            raise KeyError(f"trial {trial_id!r} is not registered")

    def _chain_tip(self) -> str:
        row = self._connection.execute(
            "SELECT entry_hash FROM ledger_entries ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return "0" * 64 if row is None else str(row[0])

    def _now(self) -> datetime:
        now = self._clock()
        _require_aware(now, "clock")
        return now.astimezone(UTC)


def holm_adjusted_p_values(
    p_values: Mapping[str, float], *, family_size: int | None = None
) -> dict[str, float]:
    """Holm step-down adjusted p-values with an optional preregistered family floor."""

    if not p_values:
        raise ValueError("p_values must not be empty")
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in p_values.values()):
        raise ValueError("p-values must be finite values in [0, 1]")
    size = family_size or len(p_values)
    if size < len(p_values):
        raise ValueError("family_size cannot be smaller than observed p-values")
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (name, p_value) in enumerate(ordered):
        candidate = min(1.0, (size - rank) * p_value)
        running = max(running, candidate)
        adjusted[name] = running
    return {name: adjusted[name] for name in p_values}


def _attempt_set_hash(attempts: tuple[TrialAttempt, ...]) -> str:
    payloads = [attempt.model_dump(mode="json") for attempt in attempts]
    return hashlib.sha256(_canonical_json(payloads).encode()).hexdigest()


def _payload_hash(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _entry_hash(
    *,
    sequence: int,
    trial_id: str,
    entry_kind: str,
    entry_id: str,
    previous_hash: str,
    payload_hash: str,
    recorded_at: datetime,
) -> str:
    envelope = {
        "sequence": sequence,
        "trial_id": trial_id,
        "entry_kind": entry_kind,
        "entry_id": entry_id,
        "previous_hash": previous_hash,
        "payload_hash": payload_hash,
        "recorded_at": recorded_at.astimezone(UTC).isoformat(),
    }
    return hashlib.sha256(_canonical_json(envelope).encode()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
