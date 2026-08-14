"""July 2026 CFTC TFF UST 2-year open-interest level boundary."""

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

CFTC_TFF_SCHEDULE_SOURCE_ID = "cftc.cot.tff_scheduled_ust2y"
_ENTITY_ID = "cftc_contract:042601"
_DECISION_TIME = datetime(2026, 7, 24, 19, 30, tzinfo=UTC)
_SOURCE_SNAPSHOT_THROUGH = datetime(2026, 7, 31, 19, 30, tzinfo=UTC)
_API_SHA256 = "6d9be78582d398274618bd19114b08479c4fb4c35367b05193818003460bf5da"
_INPUT_SOURCE_HASHES = tuple(
    sorted(
        (
            "3488b3fb375fcee6b53d8e3dffc4f5c0b1f5e35e83e9cb4d881475a5c88bcc3b",
            _API_SHA256,
            "9e795b8609b595b004211c1df8af3a06936d002582f2a1274812e148d368335a",
            "a4ffcf3bb82606d167b3492c826f2b03ced9df2a88e292bb9213fa78c464ecea",
            "a9695fe93031cc81f7ff22a6b5c12b1f6d9599b972248e9a65ce8634eaab34fa",
        )
    )
)
_SUPPORTING_RECEIPT_SHA256 = "ea85ba99ecf5a7d77871e066673d55b0bfde2ebd1aff9e4e86472e366f87da9c"
_SOURCE_VERSION = (
    "CFTC-TFF:042601:2026-07-14..2026-07-28:api:6d9be78582d398274618:"
    "annual:4b068e3dcf5ccdcb:schedule:140f29d909566cc5:policy:fb0b5a3f4c936f2a:"
    "notes:312120a31b2a2d1e"
)
_SOURCE_URL = (
    "https://publicreporting.cftc.gov/resource/gpe5-46if.json?%24limit=3&%24where="
    "cftc_contract_market_code%3D%22042601%22+AND+report_date_as_yyyy_mm_dd+in%28%22"
    "2026-07-14T00%3A00%3A00.000%22%2C%222026-07-21T00%3A00%3A00.000%22%2C%22"
    "2026-07-28T00%3A00%3A00.000%22%29&%24order=report_date_as_yyyy_mm_dd+ASC%2C"
    "cftc_contract_market_code+ASC%2Cid+ASC"
)
_AVAILABILITY_RULE = (
    "CFTC's current 2026 release schedule lists the selected unstarred Fridays and states that "
    "COT reports are released at 3:30 p.m. Eastern time using the previous Tuesday's data. "
    "FinReplay maps each selected Tuesday to that official scheduled time and validates "
    "America/New_York. The schedule calls itself tentative and CFTC provides no row-level "
    "actual-publication log, so this is scheduled availability, not independent confirmation "
    "of the actual second of publication."
)
_REDISTRIBUTION_NOTE = (
    "CFTC government information is public domain and may be copied or distributed with "
    "appropriate CFTC acknowledgement. Do not imply CFTC endorsement, and recheck current "
    "policy for any separately identified third-party material."
)
_EXPECTED_PAYLOAD_HASHES = {
    "july14_release": "fc2ffff6f3c56b8a8b534080f504af618d50df75995532168297dad5554ec7d4",
    "july21_decision_release": ("e871cbd3b3acf9d9a25d94abe41c0ce66aefac00276079772b49a98e0f357259"),
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CFTCOpenInterestBoundaryRoles(_StrictModel):
    """The two CFTC report rows scheduled to be available at decision time."""

    july14_release: str = Field(min_length=1, max_length=300)
    july21_decision_release: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> CFTCOpenInterestBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("CFTC TFF role record IDs must be unique")
        return self


class CFTCOpenInterestBoundaryInputLock(_StrictModel):
    """Content-addressed CFTC TFF observations and supporting evidence."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: CFTCOpenInterestBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=5, max_length=5)
    supporting_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(
        self,
        info: ValidationInfo,
    ) -> CFTCOpenInterestBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.decision_time != _DECISION_TIME:
            raise ValueError("CFTC TFF decision_time must equal the July 24 scheduled release")
        if self.build_epoch < self.decision_time:
            raise ValueError("CFTC TFF build_epoch cannot precede decision_time")
        if self.source_response_sha256s != _INPUT_SOURCE_HASHES:
            raise ValueError("CFTC TFF source hashes do not match the five official responses")
        if self.supporting_receipt_sha256 != _SUPPORTING_RECEIPT_SHA256:
            raise ValueError("CFTC TFF supporting receipt hash mismatch")

        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("CFTC TFF records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("CFTC TFF roles must cover every locked record exactly once")
        by_id = {record.record_id: record for record in self.records}
        for role, expected in _expected_records().items():
            record = by_id[getattr(self.roles, role)]
            published_at = expected["published_at"]
            valid_from = expected["valid_from"]
            assert isinstance(published_at, datetime)
            assert isinstance(valid_from, datetime)
            if record.source.source_id != CFTC_TFF_SCHEDULE_SOURCE_ID:
                raise ValueError("CFTC TFF lock accepts only scheduled UST 2Y facts")
            if record.source.publisher != "U.S. Commodity Futures Trading Commission":
                raise ValueError(f"CFTC TFF {role} publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.IMMUTABLE_EVENT:
                raise ValueError("CFTC TFF inputs must retain immutable-event coverage")
            if record.source.license_class is not LicenseClass.REDISTRIBUTABLE:
                raise ValueError("CFTC TFF source license boundary mismatch")
            if record.source.redistribution_note != _REDISTRIBUTION_NOTE:
                raise ValueError("CFTC TFF redistribution boundary mismatch")
            if record.source.sha256 != _API_SHA256:
                raise ValueError(f"CFTC TFF {role} API hash mismatch")
            if str(record.source.url) != _SOURCE_URL:
                raise ValueError(f"CFTC TFF {role} source URL mismatch")
            if record.source.source_version != _SOURCE_VERSION:
                raise ValueError(f"CFTC TFF {role} source version mismatch")
            if record.source.vintage_as_of != _SOURCE_SNAPSHOT_THROUGH:
                raise ValueError("CFTC TFF composite source vintage mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("CFTC TFF open interest must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"CFTC TFF {role} entity mismatch")
            if record.payload_schema_version != "1.0.0":
                raise ValueError(f"CFTC TFF {role} payload schema mismatch")
            if record.interval.availability_confidence != 0.98:
                raise ValueError("CFTC TFF scheduled-time confidence must remain 0.98")
            if record.interval.availability_rule != _AVAILABILITY_RULE:
                raise ValueError("CFTC TFF availability rule mismatch")
            if record.interval.published_at != published_at:
                raise ValueError(f"CFTC TFF {role} scheduled publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"CFTC TFF {role} scheduled availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("CFTC TFF lock contains a post-decision input")
            if record.interval.valid_from != valid_from:
                raise ValueError(f"CFTC TFF {role} valid time mismatch")
            if record.interval.valid_to is not None or record.interval.revised_at is not None:
                raise ValueError("CFTC TFF selected observations must remain open events")
            if record.source.retrieved_at != record.interval.ingested_at:
                raise ValueError("CFTC TFF retrieval and ingestion times must agree")
            if record.source.retrieved_at < _SOURCE_SNAPSHOT_THROUGH:
                raise ValueError("CFTC TFF retrieval cannot precede the composite snapshot")
            if record.source.retrieved_at > self.build_epoch:
                raise ValueError("CFTC TFF retrieval cannot occur after build_epoch")
            for field, expected_value in expected["critical_payload"].items():
                if record.payload.get(field) != expected_value:
                    raise ValueError(f"CFTC TFF {role} {field} mismatch")
            if _hash(record.payload) != _EXPECTED_PAYLOAD_HASHES[role]:
                raise ValueError(f"CFTC TFF {role} payload hash mismatch")

        earlier = by_id[self.roles.july14_release]
        decision = by_id[self.roles.july21_decision_release]
        earlier_level = _open_interest(earlier)
        decision_level = _open_interest(decision)
        if earlier_level - decision_level != 130_124:
            raise ValueError("CFTC TFF inputs must establish the 130,124-contract decline")
        if decision.payload["reported_change_from_prior_week_contracts"] != -130_124:
            raise ValueError("CFTC TFF decision row reported change does not reconcile")

        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match CFTC TFF input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> CFTCOpenInterestBoundaryInputLock:
        """Normalize, validate, and self-hash a CFTC TFF input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_cftc_open_interest_boundary_input_lock(
    path: Path,
) -> CFTCOpenInterestBoundaryInputLock:
    try:
        return CFTCOpenInterestBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid CFTC TFF input lock: {path}") from error


def build_cftc_open_interest_boundary_replay_spec(
    lock: CFTCOpenInterestBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the July 2026 open-interest boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = lock.source_response_sha256s
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                source_ids=[CFTC_TFF_SCHEDULE_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    selected_by_id = {record.record_id: record for record in selected}
    if set(selected_by_id) != set(record_ids):
        raise ValueError("TimeVault did not reproduce the CFTC TFF record IDs")
    for record in records:
        if selected_by_id[record.record_id].model_dump(mode="json") != record.model_dump(
            mode="json"
        ):
            raise ValueError("TimeVault changed a locked CFTC TFF fact")

    earlier = by_role["july14_release"]
    decision = by_role["july21_decision_release"]
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.scheduled-release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="cftc-tff-api-annual-schedule-evidence-query",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.REPORTED: len(records)},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "supporting_receipt_sha256": lock.supporting_receipt_sha256,
            "append": asdict(append_receipt),
            "manifest": {
                **asdict(manifest),
                "generated_at": _canonical_datetime(manifest.generated_at),
            },
            "selected_record_ids": list(record_ids),
            "max_scheduled_available_at": _canonical_datetime(
                max(record.interval.available_at for record in records)
            ),
            "decision_time": _canonical_datetime(lock.decision_time),
            "decision_observations_contracts": {
                "july14_open_interest": _open_interest(earlier),
                "july21_open_interest": _open_interest(decision),
                "july21_reported_weekly_change": decision.payload[
                    "reported_change_from_prior_week_contracts"
                ],
            },
            "api_annual_crosscheck_verified": True,
            "classification_and_intent_caveats_validated": True,
            "schedule_self_describes_as_tentative": True,
            "actual_row_publication_log_available": False,
            "source_auxiliary_positions_used_as_range_input": False,
            "contract_face_value_notional_conversion_performed": False,
            "source_response_file_count": len(source_hashes),
        },
        limitations=(
            "The exact timestamp is official scheduled availability, not a row-level actual log.",
            "The CFTC page calls the schedule tentative; confidence remains 0.98.",
            "Only total open interest sets the range; category positions and counts do not.",
            "Open interest is not volume, executions, accounts, P&L, or user activity.",
            "The contract face-value label is not converted to notional exposure.",
            "The July 28 report is excluded from every ReplayPack input.",
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
        range_width=metrics["range_width_contracts"],
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
                "This aggregate open-interest boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no trader intent, directional exposure, order, "
                "portfolio, execution, return, notional, or user input is invented."
            ),
        },
        limitations=("Static rendering does not validate the one-decline range heuristic.",),
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
            "Four actual engines ran over two official CFTC Futures Only TFF open-interest "
            "observations cross-checked against the annual file and anchored to the current "
            "official schedule. Reported levels remain reported; the 4,204,951-to-4,335,075 "
            "contract persistence-or-repeat-decline envelope remains inferred with no "
            "probability. The July 28 event stays only in a disjoint event lock. The 3:30 p.m. "
            "ET time is scheduled availability at 0.98 confidence, not a row-level actual log. "
            "This is not a CFTC forecast, calibrated interval, trader-intent classification, "
            "directional exposure, notional, P&L, volume, execution, causal model, deployment, "
            "external validation, investment result, or user-impact claim."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are July 21 persistence or one repetition of one known decline.",
            "The July 28 event and every later-known value are excluded from every input.",
            "Category positions, trader counts, and face value do not set either endpoint.",
            "CFTC classifications do not establish the intent of each trading activity.",
            "No probability, coverage, causal, market-impact, or performance claim is made.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: CFTCOpenInterestBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    earlier = _open_interest(records_by_role["july14_release"])
    latest = _open_interest(records_by_role["july21_decision_release"])
    known_decline = earlier - latest
    if known_decline != 130_124:
        raise ValueError("CFTC TFF decision inputs must establish the verified decline")
    lower = latest - known_decline
    upper = latest
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    variable = "next_ust_2y_tff_open_interest_contracts"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-open-interest-range",
        target_id=_ENTITY_ID,
        variable=variable,
        unit="futures_contracts",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use July 21 open-interest persistence or one repetition of the only known decline "
            "from July 14 to July 21."
        ),
        limitations=(
            "One adjacent weekly decline defines a stress range, not a forecast or probability.",
            "Category positions, trader counts, and face value do not set either endpoint.",
            "The July 28 report is absent from range construction.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-open-interest-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate open-interest persistence or one repetition of the known weekly decline "
            "using only scheduled-available observations at the boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, or regime meaning.",
            "The July 28 outcome is evaluated only afterward.",
            "Open interest is an aggregate contract count, not trading intent or activity.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, variable)
    initial_state = {state_key: float(latest)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "july14_open_interest_contracts": earlier,
        "july21_open_interest_contracts": latest,
        "known_decline_contracts": known_decline,
        "lower_level_contracts": lower,
        "upper_level_contracts": upper,
        "range_width_contracts": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.open-interest-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-cftc-tff-open-interest-level-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_open_interest_levels": metrics,
            "naive_baseline": {
                variable: latest,
                "definition": "persistence of the July 21 total open-interest level",
            },
            "bound_construction": {
                "lower_level_contracts": lower,
                "upper_level_contracts": upper,
                "range_width_contracts": width,
                "known_decline_contracts": known_decline,
                "endpoint_method": "latest_level_persistence_or_repeat_one_known_decline",
                "total_open_interest_only": True,
                "category_positions_used": False,
                "trader_counts_used": False,
                "contract_face_value_used": False,
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
            "The endpoints mechanically reuse one reported weekly decline.",
            "The July 28 value is absent.",
            "The range is not an official interval, probability, causal model, or forecast.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: CFTCOpenInterestBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    range_width: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=21)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-range-screen",
        hypothesis=(
            "A retrospectively constructed one-decline CFTC open-interest boundary qualifies "
            "for research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "TFF open interest is an aggregate weekly contract count with classification and "
            "schedule limitations. Two inputs and one later outcome cannot establish predictive "
            "validity, intent, direction, market impact, or causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-decline CFTC open-interest range width in contracts",
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
        output_manifest_sha256=_hash({"open_interest_range_width_contracts": range_width}),
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
            "known-weekly-decline": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No July 28 CFTC fact is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective CFTC TFF attempt must fail closed")
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
            claim_id="claim-reported-cftc-tff-open-interest-levels",
            statement=(
                "The selected CFTC Futures Only TFF rows report total UST 2-year open interest "
                f"of {metrics['july14_open_interest_contracts']:,} contracts on July 14 and "
                f"{metrics['july21_open_interest_contracts']:,} on July 21, 2026."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "The timestamps are official scheduled availability at 0.98 confidence, not "
                "row-level actual-publication confirmations."
            ),
            limitations=(
                "Open interest is an aggregate contract count, not volume, executions, users, "
                "P&L, direction, or intent.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-cftc-tff-open-interest-range",
            statement=(
                "The next-report stress endpoints are July 21 persistence or one repeat of the "
                f"known {metrics['known_decline_contracts']:,}-contract decline: "
                f"[{metrics['lower_level_contracts']:,}, "
                f"{metrics['upper_level_contracts']:,}] contracts."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability, coverage, official, or causal guarantee.",
            limitations=(
                "The July 28 event was not used to set the range.",
                "Category positions, trader counts, and contract face value are excluded.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-cftc-tff-trial-rejection",
            statement="TrialCourt rejected the retrospective one-decline CFTC TFF attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external or domain review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-cftc-tff-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not trades, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-cftc-tff-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not market correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _expected_records() -> dict[str, dict[str, Any]]:
    common_payload: dict[str, object] = {
        "actual_row_publication_log_available": False,
        "api_annual_crosscheck_verified": True,
        "availability_method": "official_current_schedule_exact_time_no_actual_row_log",
        "cftc_contract_market_code": "042601",
        "cftc_historical_data_not_updated_statement_present": True,
        "classification_and_intent_caveats_validated": True,
        "contract_face_value_notional_conversion_performed": False,
        "contract_market_name": "UST 2Y NOTE",
        "contract_units_source_text": "(CONTRACTS OF $200,000 FACE VALUE)",
        "historical_rows_pinned": True,
        "metric": "open_interest_all_futures_only",
        "official_scheduled_release_time_local": "15:30:00",
        "official_scheduled_release_timezone": "America/New_York",
        "official_scheduled_release_timezone_abbreviation": "EDT",
        "report_mode": "FutOnly",
        "schedule_self_describes_as_tentative": True,
        "source_snapshot_through_scheduled_release_at": "2026-07-31T19:30:00+00:00",
        "tff_notes_pdf_pages": 4,
        "unit": "Futures Contracts",
    }
    return {
        "july14_release": {
            "published_at": datetime(2026, 7, 17, 19, 30, tzinfo=UTC),
            "valid_from": datetime(2026, 7, 14, tzinfo=UTC),
            "critical_payload": {
                **common_payload,
                "report_date": "2026-07-14",
                "report_week": "2026 Report Week 28",
                "source_row_id": "260714042601F",
                "official_scheduled_release_date": "2026-07-17",
                "official_scheduled_release_at": "2026-07-17T19:30:00+00:00",
                "open_interest_contracts": 4_465_199,
                "reported_change_from_prior_week_contracts": 4_262,
            },
        },
        "july21_decision_release": {
            "published_at": _DECISION_TIME,
            "valid_from": datetime(2026, 7, 21, tzinfo=UTC),
            "critical_payload": {
                **common_payload,
                "report_date": "2026-07-21",
                "report_week": "2026 Report Week 29",
                "source_row_id": "260721042601F",
                "official_scheduled_release_date": "2026-07-24",
                "official_scheduled_release_at": "2026-07-24T19:30:00+00:00",
                "open_interest_contracts": 4_335_075,
                "reported_change_from_prior_week_contracts": -130_124,
            },
        },
    }


def _records_by_role(
    lock: CFTCOpenInterestBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _open_interest(record: BitemporalRecord) -> int:
    value = record.payload["open_interest_contracts"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("CFTC TFF open interest must be integer contracts")
    if not 1 <= value <= 100_000_000:
        raise ValueError("CFTC TFF open interest is outside the supported range")
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
