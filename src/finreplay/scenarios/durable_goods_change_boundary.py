"""March 2020 Census M3 advance durable-goods release boundary."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
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

CENSUS_DURABLE_GOODS_SOURCE_ID = "census.m3.archived_advance_durable_goods"
_ENTITY_ID = "census_m3:total_durable_goods_new_orders"
_DECISION_TIME = datetime(2020, 3, 25, 12, 30, tzinfo=UTC)
_JANUARY_PDF_SHA256 = "b58f95a053d07c367f550e4acb0a941cb338869b12ba01d2d9cbd032c4ad38b4"
_FEBRUARY_PDF_SHA256 = "84be58245193913f73c80400b6209328a5d0e3be6daac3c064b47500ac1fbf00"
_AVAILABILITY_RULE = (
    "Each selected official Census M3 Advance Durable Goods report states an exact 8:30 a.m. "
    "EST/EDT release date and time. FinReplay validates that label against America/New_York "
    "and assigns the report's semantic release facts to that stated time. The current archived "
    "PDF hash and metadata remain present-retrieval evidence; because each PDF has post-release "
    "modification metadata, exact current bytes are not claimed to be identical to the bytes "
    "served at the historical release instant."
)
_REDISTRIBUTION_NOTE = (
    "Full Census M3 PDFs remain in local content-addressed storage. The repository retains "
    "only minimal reported facts, URLs, hashes, attribution, and release-snapshot semantics; "
    "no redistribution right is inferred."
)
_ROTATIONS = [0, 0, 0, 0, 0, 0, 0]
_STANDARD_DIMENSIONS = [[612.0, 792.0]] * 7
_JANUARY_DIMENSIONS = [
    *[[612.0, 792.0]] * 5,
    [1492.68, 1931.71],
    [1423.26, 1841.86],
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DurableGoodsChangeBoundaryRoles(_StrictModel):
    """Two initial-release total new-orders changes assigned to the boundary."""

    january_initial_change: str = Field(min_length=1, max_length=300)
    february_initial_change: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> DurableGoodsChangeBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("Census M3 durable-goods role record IDs must be unique")
        return self


class DurableGoodsChangeBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision Census M3 durable-goods release facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: DurableGoodsChangeBoundaryRoles
    source_evidence_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> DurableGoodsChangeBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("Census M3 durable-goods build_epoch cannot precede decision_time")
        if self.decision_time != _DECISION_TIME:
            raise ValueError(
                "Census M3 durable-goods decision_time must equal the February-data release"
            )
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("Census M3 durable-goods records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError(
                "Census M3 durable-goods roles must cover every locked record exactly once"
            )
        if self.source_evidence_sha256s != tuple(sorted(set(self.source_evidence_sha256s))):
            raise ValueError("Census M3 durable-goods evidence hashes must be unique and sorted")
        if set(self.source_evidence_sha256s) != {
            _JANUARY_PDF_SHA256,
            _FEBRUARY_PDF_SHA256,
        }:
            raise ValueError(
                "Census M3 durable-goods evidence hash set does not match the two releases"
            )
        if {record.source.sha256 for record in self.records} != {
            _JANUARY_PDF_SHA256,
            _FEBRUARY_PDF_SHA256,
        }:
            raise ValueError("Census M3 durable-goods PDF hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected = _expected_records()
        for role, values in expected.items():
            record = by_id[getattr(self.roles, role)]
            published_at = values["published_at"]
            assert isinstance(published_at, datetime)
            if record.source.source_id != CENSUS_DURABLE_GOODS_SOURCE_ID:
                raise ValueError("Census M3 durable-goods lock accepts only archived M3 facts")
            if record.source.publisher != "U.S. Census Bureau":
                raise ValueError(f"Census M3 durable-goods {role} source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError(
                    "Census M3 durable-goods inputs must use versioned release snapshots"
                )
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("Census M3 durable-goods source license boundary mismatch")
            if record.source.redistribution_note != _REDISTRIBUTION_NOTE:
                raise ValueError("Census M3 durable-goods redistribution boundary mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("Census M3 durable-goods changes must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"Census M3 durable-goods {role} entity mismatch")
            if record.payload_schema_version != "1.0.0":
                raise ValueError(f"Census M3 durable-goods {role} payload schema mismatch")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("Census M3 durable-goods timing must be deterministic")
            if record.interval.availability_rule != _AVAILABILITY_RULE:
                raise ValueError("Census M3 durable-goods availability rule mismatch")
            if record.interval.published_at != published_at:
                raise ValueError(f"Census M3 durable-goods {role} publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"Census M3 durable-goods {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("Census M3 durable-goods lock contains a post-decision input")
            if record.interval.revised_at is not None:
                raise ValueError(
                    "Census M3 durable-goods inputs must be initial monthly-release facts"
                )
            if record.interval.valid_to is not None:
                raise ValueError(
                    "Census M3 durable-goods monthly facts must have open valid-time intervals"
                )
            expected_valid_from = datetime.fromisoformat(
                f"{values['reference_month']}-01T00:00:00+00:00"
            )
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"Census M3 durable-goods {role} valid time mismatch")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"Census M3 durable-goods {role} source vintage mismatch")
            if record.source.retrieved_at != record.interval.ingested_at:
                raise ValueError("Census M3 durable-goods retrieval and ingestion times must agree")
            if record.source.retrieved_at < published_at:
                raise ValueError(
                    "Census M3 durable-goods retrieval cannot precede official release"
                )
            if record.source.sha256 != values["pdf_sha256"]:
                raise ValueError(f"Census M3 durable-goods {role} PDF hash mismatch")
            if str(record.source.url) != values["pdf_url"]:
                raise ValueError(f"Census M3 durable-goods {role} source URL mismatch")
            expected_source_version = (
                f"CENSUS-M3-DURABLE:{values['reference_month']}:"
                f"{str(values['release_number']).replace(' ', '')}:"
                f"pdf:{str(values['pdf_sha256'])[:24]}"
            )
            if record.source.source_version != expected_source_version:
                raise ValueError(f"Census M3 durable-goods {role} source version mismatch")
            checks = values["payload"]
            assert isinstance(checks, dict)
            if set(record.payload) != set(checks):
                raise ValueError(f"Census M3 durable-goods {role} payload field set mismatch")
            for field, expected_value in checks.items():
                if record.payload.get(field) != expected_value:
                    raise ValueError(f"Census M3 durable-goods {role} {field} mismatch")
            if _change(record) != int(values["value_basis_points"]):
                raise ValueError(f"Census M3 durable-goods {role} new-orders change mismatch")

        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError(
                "lock_sha256 does not match Census M3 durable-goods input-lock content"
            )
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> DurableGoodsChangeBoundaryInputLock:
        """Normalize, validate, and self-hash a Census M3 durable-goods input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_durable_goods_change_boundary_input_lock(
    path: Path,
) -> DurableGoodsChangeBoundaryInputLock:
    try:
        return DurableGoodsChangeBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid Census M3 durable-goods input lock: {path}") from error


def build_durable_goods_change_boundary_replay_spec(
    lock: DurableGoodsChangeBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the April 2020 Census M3 durable-goods boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = lock.source_evidence_sha256s
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(lock.decision_time, source_ids=[CENSUS_DURABLE_GOODS_SOURCE_ID])
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    selected_by_id = {record.record_id: record for record in selected}
    if set(selected_by_id) != set(record_ids):
        raise ValueError("TimeVault did not reproduce the Census M3 durable-goods record IDs")
    for record in records:
        if selected_by_id[record.record_id].model_dump(mode="json") != record.model_dump(
            mode="json"
        ):
            raise ValueError("TimeVault changed a Census M3 durable-goods locked fact")

    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-census-m3-durable-goods-release-query",
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
            "value_semantics": (
                "total durable-goods new-orders month-over-month changes from each reference "
                "month's first verified Census M3 advance report; later revisions are excluded"
            ),
            "release_time_rule": "08:30 America/New_York from each dated report",
            "current_pdf_byte_identity_at_release_claimed": False,
        },
        limitations=(
            "The lock contains only January and February 2020 first-report changes.",
            "Both initial values are later revised in official Census report snapshots.",
            "M3 is not a probability sample; sampling uncertainty cannot be computed here.",
            "Figures are seasonally adjusted but not adjusted for price changes.",
            "Full report PDFs remain local download evidence.",
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
        range_width=metrics["range_width_basis_points"],
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
                "This Census M3 release boundary requires TimeVault, ShockCompiler, TrialCourt, "
                "and ReplayStudio; no firm, product, shipment, contract, position, portfolio, "
                "or allocation input is invented."
            ),
        },
        limitations=("Static rendering does not validate the two-release range heuristic.",),
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
            "Four actual engines ran over the exact January and February total durable-goods "
            "new-orders monthly changes from their first verified Census M3 advance reports, "
            "both knowable at the March 25 decision time. Reported values remain reported; the "
            "latest-change-persistence-or-repeat-known-increase envelope remains inferred with "
            "no probability. The April 24 March value and that report's January and February "
            "revision snapshot stay only in a disjoint event lock. The current official PDF "
            "bytes are not backdated to their release times. This is not a forecast, calibrated "
            "interval, probability statement, inflation-adjusted measure, COVID effect, causal "
            "model, trading signal, deployment, external validation, or user-impact claim."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are latest initial-change persistence or one repetition of the only "
            "known increase between the two initial-release total new-orders changes.",
            "The April 24 report and all later-known values are excluded from every input.",
            "M3 sampling error, confidence intervals, and statistical significance are not "
            "measurable because the panel is not a probability sample.",
            "The series is seasonally adjusted but not adjusted for inflation or price changes.",
            "No firm, product, contract, position, return, or recommendation exists.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: DurableGoodsChangeBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    january = _change(records_by_role["january_initial_change"])
    february = _change(records_by_role["february_initial_change"])
    known_increase = february - january
    if known_increase <= 0:
        raise ValueError(
            "two initial Census M3 durable-goods changes must establish a positive known increase"
        )
    lower = february
    upper = february + known_increase
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    variable = "next_total_durable_goods_new_orders_change_basis_points"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-new-orders-change-range",
        target_id=_ENTITY_ID,
        variable=variable,
        unit="basis_points_of_month_over_month_new_orders_change",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use February initial-change persistence or one repetition of the only known "
            "increase between January and February first-report total new-orders changes."
        ),
        limitations=(
            "Two first-report changes and one increase define a stress range, not a forecast, "
            "probability, confidence interval, or calibrated predictive interval.",
            "Later Census revisions and the March event are absent from range construction.",
            "No official M3 confidence interval exists for this nonprobability sample.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-new-orders-change-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate February initial-change persistence or one repetition of the known "
            "initial-release change increase using only facts available at the boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, inflation, or regime meaning.",
            "The April 24 report and its revision snapshot are evaluated only afterward.",
            "The aggregate series does not represent every firm, product, or transaction.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, variable)
    initial_state = {state_key: float(february)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "january_initial_change_basis_points": january,
        "february_initial_change_basis_points": february,
        "known_initial_increase_basis_points": known_increase,
        "lower_change_basis_points": lower,
        "upper_change_basis_points": upper,
        "range_width_basis_points": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.new-orders-change-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-census-m3-durable-goods-change-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_initial_release_changes": metrics,
            "naive_baseline": {
                variable: february,
                "definition": "persistence of the February initial M3 new-orders change",
            },
            "bound_construction": {
                "lower_change_basis_points": lower,
                "upper_change_basis_points": upper,
                "range_width_basis_points": width,
                "known_initial_increase_basis_points": known_increase,
                "endpoint_method": (
                    "latest_initial_change_persistence_or_repeat_known_initial_increase"
                ),
                "official_confidence_interval_used": False,
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
            "The endpoints mechanically reuse one initial-release change increase.",
            "The April 24 March value and revised January/February values are absent.",
            "The range is not an official confidence interval, probability, or causal model.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: DurableGoodsChangeBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    range_width: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=45)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-range-screen",
        hypothesis=(
            "A retrospectively constructed one-increase Census M3 durable-goods boundary "
            "qualifies for research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "Census M3 is an aggregate nonprobability-sample survey estimate. Two initial "
            "monthly changes and one later outcome cannot establish predictive validity or "
            "manufacturing, inflation, pandemic, policy, sector, firm, or macroeconomic causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-initial-increase Census M3 durable-goods range width in basis points",
        expected_direction="positive",
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
        output_manifest_sha256=_hash({"durable_goods_range_width_basis_points": range_width}),
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
            "known-initial-change-increase": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No April 24 Census M3 fact or revision is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective Census M3 durable-goods attempt must fail closed")
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
            claim_id="claim-reported-census-m3-durable-goods-initial-changes",
            statement=(
                "Archived Census M3 advance reports state initial total durable-goods new-orders "
                f"monthly changes of {metrics['january_initial_change_basis_points']} basis "
                "points for January and "
                f"{metrics['february_initial_change_basis_points']} basis points for February "
                "2020."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are first-report aggregate seasonally adjusted changes, not later revised "
                "values, price-adjusted output, firm records, contracts, or transactions."
            ),
            limitations=("The pack includes only two initial-release reference months.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-census-m3-durable-goods-change-range",
            statement=(
                "The next-release stress endpoints are February-change persistence or one "
                f"repeat of the known {metrics['known_initial_increase_basis_points']}-basis-"
                "point initial increase: "
                f"[{metrics['lower_change_basis_points']}, "
                f"{metrics['upper_change_basis_points']}] basis points."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability, coverage, or causal guarantee.",
            limitations=(
                "The April 24 event and all revisions were not used to set the range.",
                "The range has no manufacturing, inflation, pandemic, or policy causality.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-census-m3-durable-goods-trial-rejection",
            statement=(
                "TrialCourt rejected the retrospective one-increase Census M3 "
                "durable-goods attempt."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-census-m3-durable-goods-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not transactions, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-census-m3-durable-goods-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _expected_records() -> dict[str, dict[str, Any]]:
    common = {
        "release_series": "Monthly Advance Report on Durable Goods",
        "metric": "total_durable_goods_new_orders_monthly_change_basis_points",
        "release_time_local": "08:30:00",
        "release_timezone": "America/New_York",
        "probability_sample": False,
        "sampling_error_measurable": False,
        "confidence_intervals_computable": False,
        "statistical_significance_measurable": False,
        "seasonally_adjusted": True,
        "adjusted_for_price_changes": False,
        "text_describes_not_adjusted_for_inflation": True,
        "new_and_unfilled_orders_exclude_semiconductor_manufacturing": True,
        "annual_benchmark_notice_present": True,
        "pdf_table_snapshot_verified": True,
        "current_pdf_byte_identity_at_release_claimed": False,
        "report_pdf_pages": 7,
        "report_pdf_page_rotations": _ROTATIONS,
        "report_pdf_metadata_modified_after_release": True,
        "availability_method": (
            "exact_time_in_report_for_semantic_facts_current_pdf_bytes_retrieval_only"
        ),
        "unit": "Basis Points of Month-over-Month New Orders Change",
        "snapshot_semantics": (
            "reported total durable-goods new-orders fact in this archived release"
        ),
    }
    january_payload = {
        **common,
        "release_date": "2020-02-27",
        "reference_month": "2020-01",
        "release_number": "CB 20-31",
        "release_code": "M3-1 (20)-01",
        "value_basis_points": -20,
        "value_percent": "-0.2",
        "value_million_dollars": 246_199,
        "reported_headline_delta_billion_dollars": "0.4",
        "reported_rounded_value_billion_dollars": "246.2",
        "prior_month": "2019-12",
        "prior_month_revised_change_basis_points": 290,
        "prior_month_revised_value_million_dollars": 246_634,
        "older_month_change_basis_points": -310,
        "older_month_value_million_dollars": 239_718,
        "excluding_transportation_change_basis_points": 90,
        "excluding_defense_change_basis_points": 360,
        "shipments_value_million_dollars": 250_098,
        "shipments_change_basis_points": -20,
        "unfilled_orders_value_million_dollars": 1_157_012,
        "unfilled_orders_change_basis_points": 0,
        "inventories_value_million_dollars": 435_379,
        "inventories_change_basis_points": 0,
        "transportation_equipment_change_basis_points": -220,
        "transportation_equipment_rounded_level_million_dollars": 82_000,
        "release_snapshot_change_basis_points": {"2020-01": -20},
        "release_snapshot_previous_change_basis_points": {"2020-01": None},
        "release_snapshot_revision_delta_basis_points": {"2020-01": None},
        "release_snapshot_new_orders_million_dollars": {"2020-01": 246_199},
        "release_snapshot_previous_new_orders_million_dollars": {"2020-01": None},
        "release_snapshot_level_revision_delta_million_dollars": {"2020-01": None},
        "release_timezone_abbreviation": "EST",
        "official_release_at": "2020-02-27T13:30:00+00:00",
        "full_report_release_date": "2020-03-05",
        "full_report_release_time_label": "10:00 a.m. EST",
        "next_advance_release_date": "2020-03-25",
        "next_advance_release_time_label": "8:30 a.m. EST",
        "covid_publication_standard_statement_present": False,
        "report_pdf_url": (
            "https://www.census.gov/manufacturing/m3/historical_data/"
            "pressreleases/adv/2020/jan20adv.pdf"
        ),
        "report_pdf_sha256": _JANUARY_PDF_SHA256,
        "report_pdf_page_dimensions_points": _JANUARY_DIMENSIONS,
        "report_pdf_metadata_creation_date": "D:20200226105721-05'00'",
        "report_pdf_metadata_modification_date": "D:20200324112228-04'00'",
    }
    february_payload = {
        **common,
        "release_date": "2020-03-25",
        "reference_month": "2020-02",
        "release_number": "CB 20-47",
        "release_code": "M3-1 (20)-02",
        "value_basis_points": 120,
        "value_percent": "1.2",
        "value_million_dollars": 249_409,
        "reported_headline_delta_billion_dollars": "2.9",
        "reported_rounded_value_billion_dollars": "249.4",
        "prior_month": "2020-01",
        "prior_month_revised_change_basis_points": 10,
        "prior_month_revised_value_million_dollars": 246_541,
        "older_month_change_basis_points": 280,
        "older_month_value_million_dollars": 246_375,
        "excluding_transportation_change_basis_points": -60,
        "excluding_defense_change_basis_points": 10,
        "shipments_value_million_dollars": 252_329,
        "shipments_change_basis_points": 80,
        "unfilled_orders_value_million_dollars": 1_158_641,
        "unfilled_orders_change_basis_points": 10,
        "inventories_value_million_dollars": 434_881,
        "inventories_change_basis_points": 0,
        "transportation_equipment_change_basis_points": 460,
        "transportation_equipment_rounded_level_million_dollars": 87_000,
        "release_snapshot_change_basis_points": {
            "2020-01": 10,
            "2020-02": 120,
        },
        "release_snapshot_previous_change_basis_points": {
            "2020-01": -20,
            "2020-02": None,
        },
        "release_snapshot_revision_delta_basis_points": {
            "2020-01": 30,
            "2020-02": None,
        },
        "release_snapshot_new_orders_million_dollars": {
            "2020-01": 246_541,
            "2020-02": 249_409,
        },
        "release_snapshot_previous_new_orders_million_dollars": {
            "2020-01": 246_199,
            "2020-02": None,
        },
        "release_snapshot_level_revision_delta_million_dollars": {
            "2020-01": 342,
            "2020-02": None,
        },
        "release_timezone_abbreviation": "EDT",
        "official_release_at": "2020-03-25T12:30:00+00:00",
        "full_report_release_date": "2020-04-02",
        "full_report_release_time_label": "10:00 a.m. EDT",
        "next_advance_release_date": "2020-04-24",
        "next_advance_release_time_label": "8:30 a.m. EDT",
        "covid_publication_standard_statement_present": False,
        "report_pdf_url": (
            "https://www.census.gov/manufacturing/m3/historical_data/"
            "pressreleases/adv/2020/feb20adv.pdf"
        ),
        "report_pdf_sha256": _FEBRUARY_PDF_SHA256,
        "report_pdf_page_dimensions_points": _STANDARD_DIMENSIONS,
        "report_pdf_metadata_creation_date": "D:20200324110955-04'00'",
        "report_pdf_metadata_modification_date": "D:20200423094958-04'00'",
    }
    return {
        "january_initial_change": {
            "reference_month": "2020-01",
            "release_number": "CB 20-31",
            "published_at": datetime(2020, 2, 27, 13, 30, tzinfo=UTC),
            "pdf_sha256": _JANUARY_PDF_SHA256,
            "pdf_url": january_payload["report_pdf_url"],
            "value_basis_points": -20,
            "payload": january_payload,
        },
        "february_initial_change": {
            "reference_month": "2020-02",
            "release_number": "CB 20-47",
            "published_at": _DECISION_TIME,
            "pdf_sha256": _FEBRUARY_PDF_SHA256,
            "pdf_url": february_payload["report_pdf_url"],
            "value_basis_points": 120,
            "payload": february_payload,
        },
    }


def _records_by_role(
    lock: DurableGoodsChangeBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _change(record: BitemporalRecord) -> int:
    value = record.payload["value_basis_points"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("Census M3 durable-goods change must be integer basis points")
    if not -5_000 <= value <= 5_000:
        raise ValueError("Census M3 durable-goods change is outside the supported range")
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
