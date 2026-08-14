"""April 2020 FHFA House Price Index release boundary."""

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

FHFA_HPI_SOURCE_ID = "fhfa.hpi.archived_purchase_only_monthly_change"
_ENTITY_ID = "fhfa_hpi:us_purchase_only_seasonally_adjusted"
_DECISION_TIME = datetime(2020, 4, 22, 13, 0, tzinfo=UTC)
_SCHEDULE_SHA256 = "02f589a1d47ef046e87be9391a74f1d6e65fe92cdd552b87ad4144722f67cfba"
_JANUARY_PDF_SHA256 = "bc885fac528f66a02a3f0760b81dcace6fe1ef0f0f980aecb5e34c600d239a46"
_FEBRUARY_PDF_SHA256 = "3624bf523c7afa70616e155deb506fe419b756511a0c14a22d1fb3f16b0da993"
_AVAILABILITY_RULE = (
    "FHFA's August 20, 2019 official schedule states that 2020 HPI releases occur at "
    "9 a.m. ET and lists the selected report dates. FinReplay validates that schedule, "
    "resolves 9 a.m. through America/New_York for each date, and requires the matching "
    "dated report PDF. Current HTTP headers and retrieval times are not backdated."
)
_SCHEDULE_URL = (
    "https://www.fhfa.gov/news/news-release/fhfa-announces-2020-release-dates-for-house-price-index"
)
_REDISTRIBUTION_NOTE = (
    "Full FHFA HTML and PDF responses remain in local content-addressed storage. The "
    "repository retains only minimal reported facts, hashes, URLs, attribution, and "
    "release-snapshot semantics; no redistribution right is inferred."
)
_MONTHLY_ROTATIONS = [0, 0, 0, 0, 90, 90, 90, 0, 90, 0, 0, 90]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HousePriceChangeBoundaryRoles(_StrictModel):
    """Two initial-release national monthly changes assigned to the boundary."""

    january_initial_change: str = Field(min_length=1, max_length=300)
    february_initial_change: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> HousePriceChangeBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("FHFA HPI role record IDs must be unique")
        return self


class HousePriceChangeBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision FHFA HPI release facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: HousePriceChangeBoundaryRoles
    source_evidence_sha256s: tuple[str, ...] = Field(min_length=3, max_length=3)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> HousePriceChangeBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("FHFA HPI build_epoch cannot precede decision_time")
        if self.decision_time != _DECISION_TIME:
            raise ValueError("FHFA HPI decision_time must equal the February-data release")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("FHFA HPI records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("FHFA HPI roles must cover every locked record exactly once")
        if self.source_evidence_sha256s != tuple(sorted(set(self.source_evidence_sha256s))):
            raise ValueError("FHFA HPI evidence hashes must be unique and sorted")
        if set(self.source_evidence_sha256s) != {
            _SCHEDULE_SHA256,
            _JANUARY_PDF_SHA256,
            _FEBRUARY_PDF_SHA256,
        }:
            raise ValueError("FHFA HPI evidence hash set does not match the two releases")
        if {record.source.sha256 for record in self.records} != {
            _JANUARY_PDF_SHA256,
            _FEBRUARY_PDF_SHA256,
        }:
            raise ValueError("FHFA HPI PDF hashes do not match locked records")
        if {
            str(record.payload.get("official_schedule_semantic_sha256")) for record in self.records
        } != {_SCHEDULE_SHA256}:
            raise ValueError("FHFA HPI schedule semantic hash does not match locked records")

        by_id = {record.record_id: record for record in self.records}
        expected = _expected_records()
        for role, values in expected.items():
            record = by_id[getattr(self.roles, role)]
            published_at = values["published_at"]
            assert isinstance(published_at, datetime)
            if record.source.source_id != FHFA_HPI_SOURCE_ID:
                raise ValueError("FHFA HPI lock accepts only archived FHFA HPI facts")
            if record.source.publisher != "Federal Housing Finance Agency":
                raise ValueError(f"FHFA HPI {role} source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("FHFA HPI inputs must use versioned release snapshots")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("FHFA HPI source license boundary mismatch")
            if record.source.redistribution_note != _REDISTRIBUTION_NOTE:
                raise ValueError("FHFA HPI redistribution boundary mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("FHFA HPI changes must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"FHFA HPI {role} entity mismatch")
            if record.payload_schema_version != "1.0.0":
                raise ValueError(f"FHFA HPI {role} payload schema mismatch")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("FHFA HPI timing must be deterministic")
            if record.interval.availability_rule != _AVAILABILITY_RULE:
                raise ValueError("FHFA HPI availability rule mismatch")
            if record.interval.published_at != published_at:
                raise ValueError(f"FHFA HPI {role} publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"FHFA HPI {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("FHFA HPI lock contains a post-decision input")
            if record.interval.revised_at is not None:
                raise ValueError("FHFA HPI inputs must be initial monthly-release facts")
            if record.interval.valid_to is not None:
                raise ValueError("FHFA HPI monthly facts must have open valid-time intervals")
            expected_valid_from = datetime.fromisoformat(
                f"{values['reference_month']}-01T00:00:00+00:00"
            )
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"FHFA HPI {role} valid time mismatch")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"FHFA HPI {role} source vintage mismatch")
            if record.source.retrieved_at != record.interval.ingested_at:
                raise ValueError("FHFA HPI retrieval and ingestion times must agree")
            if record.source.retrieved_at < published_at:
                raise ValueError("FHFA HPI retrieval cannot precede official release")
            if record.source.sha256 != values["pdf_sha256"]:
                raise ValueError(f"FHFA HPI {role} PDF hash mismatch")
            if str(record.source.url) != values["pdf_url"]:
                raise ValueError(f"FHFA HPI {role} source URL mismatch")
            expected_source_version = (
                f"FHFA-HPI:{values['reference_month']}:monthly:"
                f"pdf:{str(values['pdf_sha256'])[:20]}:schedule:{_SCHEDULE_SHA256[:20]}"
            )
            if record.source.source_version != expected_source_version:
                raise ValueError(f"FHFA HPI {role} source version mismatch")
            checks = values["payload"]
            assert isinstance(checks, dict)
            if set(record.payload) != set(checks):
                raise ValueError(f"FHFA HPI {role} payload field set mismatch")
            for field, expected_value in checks.items():
                if record.payload.get(field) != expected_value:
                    raise ValueError(f"FHFA HPI {role} {field} mismatch")
            if _change(record) != int(values["value_basis_points"]):
                raise ValueError(f"FHFA HPI {role} national change mismatch")

        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match FHFA HPI input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> HousePriceChangeBoundaryInputLock:
        """Normalize, validate, and self-hash an FHFA HPI input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_house_price_change_boundary_input_lock(
    path: Path,
) -> HousePriceChangeBoundaryInputLock:
    try:
        return HousePriceChangeBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid FHFA HPI input lock: {path}") from error


def build_house_price_change_boundary_replay_spec(
    lock: HousePriceChangeBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the April 2020 FHFA HPI boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = lock.source_evidence_sha256s
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(vault.records_as_of(lock.decision_time, source_ids=[FHFA_HPI_SOURCE_ID]))
        manifest = vault.manifest(generated_at=lock.build_epoch)
    selected_by_id = {record.record_id: record for record in selected}
    if set(selected_by_id) != set(record_ids):
        raise ValueError("TimeVault did not reproduce the FHFA HPI record IDs")
    for record in records:
        if selected_by_id[record.record_id].model_dump(mode="json") != record.model_dump(
            mode="json"
        ):
            raise ValueError("TimeVault changed an FHFA HPI locked fact")

    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-fhfa-hpi-release-query",
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
                "national purchase-only seasonally adjusted monthly HPI changes from each "
                "reference month's first verified FHFA report; later revisions are excluded"
            ),
            "schedule_evidence": {
                "semantic_sha256": _SCHEDULE_SHA256,
                "url": _SCHEDULE_URL,
                "release_time_rule": "09:00 America/New_York",
                "raw_html_byte_identity_claimed": False,
            },
        },
        limitations=(
            "The lock contains only January and February 2020 national monthly changes.",
            "Both initial values are later revised in official FHFA report snapshots.",
            "The purchase-only Enterprise repeat-transactions index is not every U.S. home.",
            "Full report PDFs and schedule HTML remain local download evidence.",
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
                "This FHFA release boundary requires TimeVault, ShockCompiler, TrialCourt, "
                "and ReplayStudio; no property, borrower, mortgage, transaction, appraisal, "
                "position, order, portfolio, or allocation input is invented."
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
            "Four actual engines ran over the exact January and February national purchase-"
            "only seasonally adjusted monthly HPI changes from their first verified FHFA "
            "reports, both knowable at the April 22 decision time. Reported values remain "
            "reported; the latest-change-persistence-or-repeat-known-increase envelope remains "
            "inferred with no probability. The May 26 March value and the report's January and "
            "February revision snapshot stay only in a disjoint event lock. The January report "
            "footer's '9AM EST' wording is retained alongside the controlling official schedule's "
            "9 a.m. ET rule; it is not silently harmonized. This is not a forecast, calibrated "
            "interval, universal home-price measure, contemporaneous COVID effect, causal model, "
            "trading signal, deployment, external validation, or user-impact claim."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are latest initial-change persistence or one repetition of the only "
            "known increase between the two initial-release national changes.",
            "The May 26 report and all of its later-known values are excluded from every input.",
            "FHFA HPI changes are repeat-transactions index estimates, not transaction counts.",
            "January and February report language says the observations reflected little or no "
            "COVID-19 influence; the scenario makes no contemporaneous pandemic-effect claim.",
            "No property, mortgage, appraisal, position, order, return, or recommendation exists.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: HousePriceChangeBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    january = _change(records_by_role["january_initial_change"])
    february = _change(records_by_role["february_initial_change"])
    known_increase = february - january
    if known_increase <= 0:
        raise ValueError("two initial FHFA HPI changes must establish a positive known increase")
    lower = february
    upper = february + known_increase
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    variable = "next_us_purchase_only_hpi_monthly_change_basis_points"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-hpi-monthly-change-range",
        target_id=_ENTITY_ID,
        variable=variable,
        unit="basis_points_of_month_over_month_price_change",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use February initial-change persistence or one repetition of the only known "
            "increase between January and February first-report national monthly changes."
        ),
        limitations=(
            "Two first-report changes and one increase define a stress range, not a forecast, "
            "probability, confidence interval, or calibrated predictive interval.",
            "Later FHFA revisions and the March event are absent from range construction.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-hpi-monthly-change-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate February initial-change persistence or one repetition of the known "
            "initial-release change increase using only facts available at the boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, or regime meaning.",
            "The May 26 report and its revision snapshot are evaluated only afterward.",
            "The index does not represent every property, mortgage, or transaction.",
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
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.hpi-change-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-fhfa-hpi-monthly-change-program",
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
                "definition": "persistence of the February initial FHFA national change",
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
            "The May 26 March value and revised January/February values are absent.",
            "The range is not an official confidence interval, probability, or causal model.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: HousePriceChangeBoundaryInputLock,
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
            "A retrospectively constructed one-increase FHFA HPI boundary qualifies for "
            "research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "FHFA HPI is a repeat-transactions index, but two initial monthly changes and one "
            "later outcome cannot establish predictive validity or housing, credit, pandemic, "
            "policy, regional, property, mortgage, or macroeconomic causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-initial-increase FHFA HPI range width in basis points",
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
        output_manifest_sha256=_hash({"hpi_range_width_basis_points": range_width}),
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
            "No May 26 FHFA HPI fact or revision is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective FHFA HPI attempt must fail closed")
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
            claim_id="claim-reported-fhfa-hpi-initial-changes",
            statement=(
                "Archived FHFA reports state initial national purchase-only seasonally adjusted "
                f"monthly HPI changes of {metrics['january_initial_change_basis_points']} basis "
                "points for January and "
                f"{metrics['february_initial_change_basis_points']} basis points for February "
                "2020."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are first-report national index changes, not later revised values, "
                "universal home prices, transaction counts, appraisals, or property records."
            ),
            limitations=("The pack includes only two initial-release reference months.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-fhfa-hpi-change-range",
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
                "The May 26 event and all revisions were not used to set the range.",
                "The range has no housing, credit, pandemic, or policy causality.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-fhfa-hpi-trial-rejection",
            statement="TrialCourt rejected the retrospective one-increase FHFA HPI attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-fhfa-hpi-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not transactions, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-fhfa-hpi-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _expected_records() -> dict[str, dict[str, Any]]:
    common = {
        "release_series": "FHFA House Price Index",
        "metric": "us_purchase_only_hpi_monthly_change_basis_points",
        "release_time_local": "09:00:00",
        "release_timezone": "America/New_York",
        "release_timezone_abbreviation": "EDT",
        "official_schedule_url": _SCHEDULE_URL,
        "official_schedule_published_date": "2019-08-20",
        "official_schedule_conservative_knowledge_at": "2019-08-22T00:00:00+00:00",
        "official_schedule_semantic_sha256": _SCHEDULE_SHA256,
        "purchase_only_index": True,
        "seasonally_adjusted": True,
        "index_base": "January 1991 = 100",
        "report_table_snapshot_verified": True,
        "report_revision_rows_verified": True,
        "covid_timing_statement_present": True,
        "report_pdf_pages": 12,
        "report_pdf_page_width_points": 612,
        "report_pdf_page_height_points": 792,
        "report_pdf_page_rotations": _MONTHLY_ROTATIONS,
        "report_pdf_metadata_modified_after_release": False,
        "availability_method": "preannounced_2019_schedule_9am_et_and_matching_dated_report",
        "unit": "Basis Points of Month-over-Month Price Change",
        "snapshot_semantics": (
            "reported purchase-only seasonally adjusted HPI value in this release"
        ),
    }
    january_payload = {
        **common,
        "release_date": "2020-03-25",
        "reference_month": "2020-01",
        "report_kind": "monthly",
        "value_basis_points": 30,
        "value_percent": "0.3",
        "reported_year_over_year_change_basis_points": 520,
        "reported_year_over_year_change_percent": "5.2",
        "reported_monthly_change_by_geography_basis_points": {
            "East North Central": 30,
            "East South Central": 20,
            "Middle Atlantic": 60,
            "Mountain": -20,
            "New England": 40,
            "Pacific": 50,
            "South Atlantic": 70,
            "U.S.": 30,
            "West North Central": -10,
            "West South Central": 0,
        },
        "reported_current_index_by_geography": {
            "East North Central": "235.0",
            "East South Central": "260.3",
            "Middle Atlantic": "252.6",
            "Mountain": "389.8",
            "New England": "266.4",
            "Pacific": "329.5",
            "South Atlantic": "292.0",
            "U.S.": "284.4",
            "West North Central": "279.9",
            "West South Central": "295.9",
        },
        "release_snapshot_monthly_change_basis_points": {"2020-01": 30},
        "release_snapshot_previous_estimate_basis_points": {"2020-01": None},
        "release_snapshot_revision_delta_basis_points": {"2020-01": None},
        "official_release_at": "2020-03-25T13:00:00+00:00",
        "report_footer_release_time_label": "9AM EST",
        "report_footer_time_label_differs_from_schedule_wording": True,
        "report_pdf_url": (
            "https://www.fhfa.gov/document/d/hpi/house-price-index-report-january-2020"
        ),
        "report_pdf_sha256": _JANUARY_PDF_SHA256,
        "report_pdf_metadata_creation_date": "D:20200318115301-04'00'",
        "report_pdf_metadata_modification_date": "D:20200320124635-04'00'",
    }
    february_payload = {
        **common,
        "release_date": "2020-04-22",
        "reference_month": "2020-02",
        "report_kind": "monthly",
        "value_basis_points": 70,
        "value_percent": "0.7",
        "reported_year_over_year_change_basis_points": 570,
        "reported_year_over_year_change_percent": "5.7",
        "reported_monthly_change_by_geography_basis_points": {
            "East North Central": 100,
            "East South Central": 70,
            "Middle Atlantic": 120,
            "Mountain": 100,
            "New England": 40,
            "Pacific": 80,
            "South Atlantic": 40,
            "U.S.": 70,
            "West North Central": 90,
            "West South Central": 30,
        },
        "reported_current_index_by_geography": {
            "East North Central": "237.6",
            "East South Central": "263.9",
            "Middle Atlantic": "256.0",
            "Mountain": "395.9",
            "New England": "268.0",
            "Pacific": "333.0",
            "South Atlantic": "293.4",
            "U.S.": "287.0",
            "West North Central": "283.7",
            "West South Central": "296.0",
        },
        "release_snapshot_monthly_change_basis_points": {
            "2020-01": 50,
            "2020-02": 70,
        },
        "release_snapshot_previous_estimate_basis_points": {
            "2020-01": 30,
            "2020-02": None,
        },
        "release_snapshot_revision_delta_basis_points": {
            "2020-01": 20,
            "2020-02": None,
        },
        "official_release_at": "2020-04-22T13:00:00+00:00",
        "report_footer_release_time_label": "9AM ET",
        "report_footer_time_label_differs_from_schedule_wording": False,
        "report_pdf_url": (
            "https://www.fhfa.gov/document/d/hpi/house-price-index-report-february-2020"
        ),
        "report_pdf_sha256": _FEBRUARY_PDF_SHA256,
        "report_pdf_metadata_creation_date": "D:20200415162251-04'00'",
        "report_pdf_metadata_modification_date": "D:20200420145006-04'00'",
    }
    return {
        "january_initial_change": {
            "reference_month": "2020-01",
            "published_at": datetime(2020, 3, 25, 13, 0, tzinfo=UTC),
            "pdf_sha256": _JANUARY_PDF_SHA256,
            "pdf_url": january_payload["report_pdf_url"],
            "value_basis_points": 30,
            "payload": january_payload,
        },
        "february_initial_change": {
            "reference_month": "2020-02",
            "published_at": _DECISION_TIME,
            "pdf_sha256": _FEBRUARY_PDF_SHA256,
            "pdf_url": february_payload["report_pdf_url"],
            "value_basis_points": 70,
            "payload": february_payload,
        },
    }


def _records_by_role(
    lock: HousePriceChangeBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _change(record: BitemporalRecord) -> int:
    value = record.payload["value_basis_points"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("FHFA HPI change must be integer basis points")
    if not -5_000 <= value <= 5_000:
        raise ValueError("FHFA HPI change is outside the supported range")
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
