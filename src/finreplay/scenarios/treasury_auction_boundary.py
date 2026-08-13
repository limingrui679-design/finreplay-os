"""March 2020 Treasury 91-day bill auction-rate boundary."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from finreplay.contracts import (
    ArtifactStatus,
    BitemporalRecord,
    CostModel,
    EvidenceClass,
    LicenseClass,
    ScenarioMode,
    TemporalCoverage,
    TrialDisposition,
    TrialSpec,
)
from finreplay.engines import (
    EngineName,
    ReplayArtifact,
    ReplayClaim,
    ReplayPackSpec,
    ShockCompiler,
    ShockOperation,
    ShockParameter,
    ShockProgram,
    TimeVault,
    TrialAttempt,
    TrialCourt,
)

TREASURY_AUCTION_SOURCE_ID = "treasury.auctions.archived_91_day_bill_results"
_ENTITY_ID = "us_treasury_auction:91_day_bill"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TreasuryAuctionBoundaryRoles(_StrictModel):
    """Two archived auction high-rate facts assigned to the decision boundary."""

    march09_high_rate: str = Field(min_length=1, max_length=300)
    march16_high_rate: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> TreasuryAuctionBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("Treasury auction boundary role record IDs must be unique")
        return self


class TreasuryAuctionBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision Treasury 91-day bill result facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: TreasuryAuctionBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> TreasuryAuctionBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("Treasury auction build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("Treasury auction records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("Treasury auction roles must cover every locked record exactly once")
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("Treasury auction source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("Treasury auction source hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected: dict[str, dict[str, Any]] = {
            "march09_high_rate": {
                "auction_date": "2020-03-09",
                "announcement_date": "2020-03-05",
                "issue_date": "2020-03-12",
                "maturity_date": "2020-06-11",
                "cusip": "912796TZ2",
                "filename": "R_20200309_2",
                "xml_sha256": (
                    "4ca42500fa381d14750aee6902f73d886eb1ea233d84bfa6d83d5271216fa505"
                ),
                "pdf_sha256": (
                    "e486be2d621155bb4bcfc11582d7ed8825d01054698ee904179a2d9db616c3e3"
                ),
                "published_at": datetime(2020, 3, 9, 15, 32, tzinfo=UTC),
                "available_at": datetime(2020, 3, 10, 4, tzinfo=UTC),
                "release_time": "11:32",
                "value": 39,
                "high": "0.390",
                "median": "0.310",
                "low": "0.110",
                "investment": "0.396",
                "price": "99.901417",
                "cover": "2.74",
                "competitive_tendered": 114_112_630_000,
                "competitive_accepted": 41_162_930_000,
                "subtotal_tendered": 114_949_714_900,
                "subtotal_accepted": 42_000_014_900,
                "total_tendered": 116_751_407_300,
                "total_accepted": 43_801_707_300,
            },
            "march16_high_rate": {
                "auction_date": "2020-03-16",
                "announcement_date": "2020-03-12",
                "issue_date": "2020-03-19",
                "maturity_date": "2020-06-18",
                "cusip": "912796SV2",
                "filename": "R_20200316_2",
                "xml_sha256": (
                    "862afbc310eb7fe583acc176384a9ea743af374b2a82e0f545c8039bfdf17fee"
                ),
                "pdf_sha256": (
                    "23b098fa40165e6b5fb33c6cc5e9c0f0c286183ec475c866de072cf5e14f2fb0"
                ),
                "published_at": datetime(2020, 3, 16, 15, 32, tzinfo=UTC),
                "available_at": datetime(2020, 3, 17, 4, tzinfo=UTC),
                "release_time": "11:32",
                "value": 29,
                "high": "0.290",
                "median": "0.200",
                "low": "0.100",
                "investment": "0.294",
                "price": "99.926694",
                "cover": "2.58",
                "competitive_tendered": 106_585_264_000,
                "competitive_accepted": 40_409_534_000,
                "subtotal_tendered": 108_175_772_900,
                "subtotal_accepted": 42_000_042_900,
                "total_tendered": 109_614_749_300,
                "total_accepted": 43_439_019_300,
            },
        }
        for role, values in expected.items():
            record = by_id[getattr(self.roles, role)]
            auction_date = str(values["auction_date"])
            published_at = values["published_at"]
            available_at = values["available_at"]
            assert isinstance(published_at, datetime)
            assert isinstance(available_at, datetime)
            if record.source.source_id != TREASURY_AUCTION_SOURCE_ID:
                raise ValueError("Treasury auction lock accepts only archived result facts")
            if record.source.publisher != (
                "U.S. Department of the Treasury, Bureau of the Fiscal Service"
            ):
                raise ValueError("Treasury auction source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("Treasury auction inputs must use versioned snapshots")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("Treasury auction source license boundary mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("Treasury auction rates must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"Treasury auction {role} entity mismatch")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("Treasury auction timing must be deterministic")
            if record.interval.published_at != published_at:
                raise ValueError(f"Treasury auction {role} publication time mismatch")
            if record.interval.available_at != available_at:
                raise ValueError(f"Treasury auction {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("Treasury auction lock contains a post-decision input")
            expected_valid_from = datetime.fromisoformat(
                f"{auction_date}T00:00:00+00:00"
            )
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"Treasury auction {role} valid time mismatch")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"Treasury auction {role} source vintage mismatch")
            if record.source.sha256 != values["xml_sha256"]:
                raise ValueError(f"Treasury auction {role} XML hash mismatch")
            expected_url = (
                f"https://www.treasurydirect.gov/xml/{values['filename']}.xml"
            )
            if str(record.source.url) != expected_url:
                raise ValueError(f"Treasury auction {role} source URL mismatch")
            expected_source_version = (
                f"TreasuryAuction:{auction_date}:{values['cusip']}:"
                f"xml:{record.source.sha256[:20]}:pdf:{str(values['pdf_sha256'])[:20]}"
            )
            if record.source.source_version != expected_source_version:
                raise ValueError(f"Treasury auction {role} source version mismatch")
            payload = record.payload
            checks = {
                "auction_date": values["auction_date"],
                "announcement_date": values["announcement_date"],
                "issue_date": values["issue_date"],
                "maturity_date": values["maturity_date"],
                "cusip": values["cusip"],
                "security_term": "91-Day Bill",
                "metric": "high_discount_rate",
                "value_basis_points": values["value"],
                "reported_high_rate_percent": values["high"],
                "reported_median_rate_percent": values["median"],
                "reported_low_rate_percent": values["low"],
                "reported_investment_rate_percent": values["investment"],
                "reported_price_per_100": values["price"],
                "bid_to_cover_ratio": values["cover"],
                "competitive_tendered_dollars": values["competitive_tendered"],
                "competitive_accepted_dollars": values["competitive_accepted"],
                "subtotal_tendered_dollars": values["subtotal_tendered"],
                "subtotal_accepted_dollars": values["subtotal_accepted"],
                "total_tendered_dollars": values["total_tendered"],
                "total_accepted_dollars": values["total_accepted"],
                "official_release_time_local": values["release_time"],
                "official_release_timezone": "America/New_York",
                "official_release_at": published_at.isoformat(),
                "unit": "Basis Points",
                "xml_pdf_crosscheck_verified": True,
                "auction_arithmetic_verified": True,
                "price_formula_verified": True,
                "release_pdf_sha256": values["pdf_sha256"],
                "availability_method": (
                    "official_release_time_then_next_local_midnight"
                ),
            }
            for field, expected_value in checks.items():
                if payload.get(field) != expected_value:
                    raise ValueError(f"Treasury auction {role} {field} mismatch")
            expected_pdf_url = (
                "https://www.treasurydirect.gov/instit/annceresult/press/preanre/"
                f"2020/{values['filename']}.pdf"
            )
            if payload.get("release_pdf_url") != expected_pdf_url:
                raise ValueError(f"Treasury auction {role} PDF URL mismatch")
            value = _rate(record)
            reported = payload.get("reported_high_rate_percent")
            if not isinstance(reported, str):
                raise ValueError(f"Treasury auction {role} reported rate must be a string")
            try:
                reported_basis_points = Decimal(reported) * 100
            except InvalidOperation as error:
                raise ValueError(
                    f"Treasury auction {role} reported rate must be decimal"
                ) from error
            if not reported_basis_points.is_finite() or reported_basis_points != value:
                raise ValueError(f"Treasury auction {role} percent and basis points mismatch")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match Treasury auction input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> TreasuryAuctionBoundaryInputLock:
        """Normalize, validate, and self-hash a Treasury auction input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_treasury_auction_boundary_input_lock(
    path: Path,
) -> TreasuryAuctionBoundaryInputLock:
    try:
        return TreasuryAuctionBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid Treasury auction input lock: {path}") from error


def build_treasury_auction_boundary_replay_spec(
    lock: TreasuryAuctionBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 Treasury auction boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(lock.decision_time, source_ids=[TREASURY_AUCTION_SOURCE_ID])
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the Treasury auction input lock")
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.auction-result-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-treasury-auction-result-query",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.REPORTED: len(records)},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "append": asdict(append_receipt),
            "manifest": {
                **asdict(manifest),
                "generated_at": _canonical_datetime(manifest.generated_at),
            },
            "selected_record_ids": list(record_ids),
            "max_available_at": _canonical_datetime(
                max(record.interval.available_at for record in records)
            ),
            "decision_time": _canonical_datetime(lock.decision_time),
        },
        limitations=(
            "The lock contains only two 91-day bill auction results.",
            "Each XML release time is conservatively delayed to the next local midnight.",
            "Paired PDFs corroborate identity and values but remain local download evidence.",
            "Auction rates and bid classes do not identify individual bidder decisions.",
        ),
    )
    shock_artifact, metrics = _run_shockcompiler(
        lock=lock,
        records_by_role=by_role,
        source_hashes=source_hashes,
        upstream=timevault_artifact.artifact_id,
    )
    trial_artifact = _run_trialcourt(
        lock=lock,
        records_by_role=by_role,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        range_width=metrics["rate_range_width_basis_points"],
        upstream=(timevault_artifact.artifact_id, shock_artifact.artifact_id),
        code_commit=code_commit,
    )
    replaystudio_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.replaystudio.render",
        engine=EngineName.REPLAYSTUDIO,
        artifact_kind="static-report-contract",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.EXTRACTED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=tuple(
            sorted(
                (
                    shock_artifact.artifact_id,
                    timevault_artifact.artifact_id,
                    trial_artifact.artifact_id,
                )
            )
        ),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "renderer": "ReplayStudio",
            "portable_file_count_excluding_manifest": 5,
            "truth_labels_visible": True,
            "engine_selection": (
                "This aggregate Treasury auction-rate boundary requires TimeVault, "
                "ShockCompiler, TrialCourt, and ReplayStudio; no individual bid, order, "
                "execution, portfolio, or allocation input is invented."
            ),
        },
        limitations=(
            "Static rendering does not validate the auction-rate range heuristic.",
        ),
    )
    artifacts = (
        timevault_artifact,
        shock_artifact,
        trial_artifact,
        replaystudio_artifact,
    )
    return ReplayPackSpec(
        replay_id=lock.replay_id,
        scenario_id=lock.scenario_id,
        scenario_version=lock.scenario_version,
        title=lock.title,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        created_at=lock.build_epoch,
        code_commit=code_commit,
        status=ArtifactStatus.REPRODUCED,
        artifacts=artifacts,
        claims=_claims(artifacts, metrics),
        require_all_engines=False,
        distinct_input_records=len(records),
        derived_records=(
            int(trial_artifact.payload["manifest"]["entries"])
            + len(shock_artifact.payload["compiled"]["trials"])
            + 1
        ),
        compressed_input_bytes=len(
            gzip.compress(_canonical_json(lock.model_dump(mode="json")).encode(), mtime=0)
        ),
        elapsed_seconds=0.0,
        claim_boundary=(
            "Four actual engines ran over two locked Treasury 91-day bill auction facts "
            "available before the decision time. Reported high rates remain reported; the "
            "persistence-or-repeat-known-decline range remains inferred with no assigned "
            "probability; TrialCourt rejects retrospective promotion. The March 23 zero-rate "
            "result is held only in a disjoint post-decision event lock. This is not a forecast, "
            "calibrated interval, auction-demand or policy causal model, trading signal, "
            "production deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are latest-rate persistence or one repetition of the single known "
            "10-basis-point decline, with a zero floor and no probability.",
            "The March 23 zero-rate result is excluded from every decision input and artifact.",
            "Two auction outcomes do not identify bidders, motivations, demand curves, or causes.",
            "The range has no policy, liquidity, return, or forecast interpretation.",
            "No position, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: TreasuryAuctionBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    march09 = _rate(records_by_role["march09_high_rate"])
    march16 = _rate(records_by_role["march16_high_rate"])
    known_decline = march09 - march16
    if known_decline <= 0:
        raise ValueError("two known Treasury auctions must establish a positive rate decline")
    lower = max(0, march16 - known_decline)
    upper = march16
    width = upper - lower
    if width <= 0:
        raise ValueError("Treasury auction continuation range must have positive width")
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-high-rate-range",
        target_id=_ENTITY_ID,
        variable="next_91_day_bill_high_rate_basis_points",
        unit="basis_points",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use latest-rate persistence or one repeat of the only known weekly high-rate "
            "decline, floored at zero, as transparent next-auction stress endpoints."
        ),
        limitations=(
            "Two auction results and one decline define a stress range, not a forecast or "
            "confidence interval.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-high-rate-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate latest persistence or one repetition of the known 91-day bill high-rate "
            "decline using only auction results available at the historical decision boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, demand, causal, or policy meaning.",
            "The March 23 zero-rate result is excluded and evaluated only afterward.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, "next_91_day_bill_high_rate_basis_points")
    initial_state = {state_key: float(march16)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "march09_high_rate_basis_points": march09,
        "march16_high_rate_basis_points": march16,
        "known_weekly_decline_basis_points": known_decline,
        "rate_lower_basis_points": lower,
        "rate_upper_basis_points": upper,
        "rate_range_width_basis_points": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.high-rate-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-treasury-auction-high-rate-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_rates": metrics,
            "naive_baseline": {
                "next_91_day_bill_high_rate_basis_points": march16,
                "definition": "persistence of the latest known 91-day bill high rate",
            },
            "bound_construction": {
                "lower_rate_basis_points": lower,
                "upper_rate_basis_points": upper,
                "range_width_basis_points": width,
                "known_weekly_decline_basis_points": known_decline,
                "zero_floor_applied": True,
                "endpoint_method": (
                    "latest_persistence_or_repeat_known_weekly_decline_with_zero_floor"
                ),
                "probability_assigned": False,
                "future_event_used": False,
            },
            "program": program.model_dump(mode="json"),
            "compiled": compiled.model_dump(mode="json"),
            "applied_endpoints": {
                "lower": lower_state[state_key],
                "upper": upper_state[state_key],
            },
        },
        limitations=(
            "The endpoints mechanically reuse one known weekly decline and a zero floor.",
            "The March 23 zero-rate result is absent from the bound construction.",
            "The range is not an auction-demand probability or causal explanation.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: TreasuryAuctionBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    range_width: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=35)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-range-screen",
        hypothesis=(
            "A retrospectively constructed one-decline Treasury auction-rate boundary qualifies "
            "for research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "A bill auction high rate is a reported price-clearing result, but two auctions and "
            "one later outcome cannot establish predictive validity, demand, or policy causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-decline 91-day bill high-rate range width in basis points",
        expected_direction="negative",
        cost_model=_cost_model(),
        disposition=TrialDisposition.REVISE,
    )
    attempt = TrialAttempt(
        attempt_id=f"{lock.artifact_prefix}-retrospective-attempt-1",
        trial_id=spec.trial_id,
        attempt_number=1,
        completed_at=lock.build_epoch,
        code_commit=code_commit,
        config_sha256=_hash(spec.model_dump(mode="json")),
        input_manifest_sha256=lock.lock_sha256,
        output_manifest_sha256=_hash({"rate_range_width_basis_points": range_width}),
        decision_time=lock.decision_time,
        max_input_available_at=max(
            record.interval.available_at for record in records_by_role.values()
        ),
        training_end=lock.decision_time.date() - timedelta(days=2),
        evaluation_start=holdout_start,
        evaluation_end=holdout_end,
        sample_size=len(records_by_role),
        metric_value=float(range_width),
        p_value=1.0,
        gross_return_bps=0.0,
        one_way_turnover=0.0,
        short_fraction=0.0,
        requested_capital_usd=1.0,
        median_daily_volume_usd=1.0,
        regime_metric_values={
            "known-weekly-rate-decline": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No March 23 Treasury auction event fact is present in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective Treasury auction attempt must fail closed")
    return ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.trialcourt.retrospective-gate",
        engine=EngineName.TRIALCOURT,
        artifact_kind="adversarial-decision",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={
            EvidenceClass.REPORTED: len(source_record_ids),
            EvidenceClass.INFERRED: 1,
            EvidenceClass.SIMULATED: 1,
        },
        source_set_historical_replay_eligible=True,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=tuple(sorted(upstream)),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "spec": spec.model_dump(mode="json"),
            "attempt": attempt.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "registration_receipt": asdict(registration_receipt),
            "attempt_receipt": asdict(attempt_receipt),
            "manifest": asdict(manifest),
        },
        limitations=(
            "The rejected attempt is a retrospective method boundary, not a validated forecast.",
            "Operational sentinel fields contain no execution, capacity, or return evidence.",
        ),
    )


def _claims(
    artifacts: tuple[ReplayArtifact, ...],
    metrics: dict[str, int],
) -> tuple[ReplayClaim, ...]:
    by_engine = {artifact.engine: artifact for artifact in artifacts}
    return (
        ReplayClaim(
            claim_id="claim-reported-treasury-auction-high-rates",
            statement=(
                "Paired TreasuryDirect XML/PDF results report 91-day bill high rates of "
                f"{metrics['march09_high_rate_basis_points']} basis points on March 9 and "
                f"{metrics['march16_high_rate_basis_points']} basis points on March 16, 2020."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary="These are aggregate auction results, not forecasts or causal estimates.",
            limitations=("The pack includes only two auction dates.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-treasury-auction-rate-range",
            statement=(
                "The next-auction stress endpoints are latest persistence or one repeat of the "
                f"known {metrics['known_weekly_decline_basis_points']}-basis-point decline: "
                f"[{metrics['rate_lower_basis_points']}, "
                f"{metrics['rate_upper_basis_points']}] basis points."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability or coverage guarantee.",
            limitations=(
                "The March 23 zero-rate result was not used to set the interval.",
                "The range has no bidder-demand, causal, or policy interpretation.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement="TrialCourt rejected the retrospective one-decline auction-rate attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not bids, orders, capacity, capital, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _records_by_role(
    lock: TreasuryAuctionBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _rate(record: BitemporalRecord) -> int:
    value = record.payload["value_basis_points"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("Treasury auction rate must be integer basis points")
    if not 0 <= value <= 10_000:
        raise ValueError("Treasury auction rate is outside supported range")
    return value


def _cost_model() -> CostModel:
    return CostModel(
        commission_bps=1.0,
        half_spread_bps=1.0,
        market_impact_bps=1.0,
        borrow_bps_annual=0.0,
        max_participation_rate=0.05,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
