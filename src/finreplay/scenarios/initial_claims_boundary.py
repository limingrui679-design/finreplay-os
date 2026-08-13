"""March 2020 DOL initial-claims boundary over archived weekly releases."""

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

DOL_UI_CLAIMS_SOURCE_ID = "dol.eta.archived_weekly_initial_claims"
_ENTITY_ID = "dol_ui_claims:united_states"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InitialClaimsBoundaryRoles(_StrictModel):
    """Two archived DOL claims facts assigned to the decision boundary."""

    march07_claims: str = Field(min_length=1, max_length=300)
    march14_claims: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> InitialClaimsBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("DOL initial-claims boundary role record IDs must be unique")
        return self


class InitialClaimsBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision DOL initial-claims release facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: InitialClaimsBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> InitialClaimsBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("DOL initial-claims build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("DOL initial-claims records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError(
                "DOL initial-claims roles must cover every locked record exactly once"
            )
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("DOL initial-claims source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("DOL initial-claims source hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected = {
            "march07_claims": {
                "release_date": "2020-03-12",
                "week_ending": "2020-03-07",
                "published_at": datetime(2020, 3, 12, 12, 30, tzinfo=UTC),
                "available_at": datetime(2020, 3, 12, 12, 30, 10, tzinfo=UTC),
                "pdf_last_modified_at": "2020-03-12T12:30:10+00:00",
                "filename": "eta20200432.pdf",
                "release_number": "USDL 20-432-NAT",
                "value": 211_000,
                "prior": 215_000,
                "change": -4_000,
                "direction": "decrease",
                "prior_status": "revised",
                "revision_old": 216_000,
                "revision_new": 215_000,
                "revision_delta": -1_000,
                "annual_revision": False,
            },
            "march14_claims": {
                "release_date": "2020-03-19",
                "week_ending": "2020-03-14",
                "published_at": datetime(2020, 3, 19, 12, 30, tzinfo=UTC),
                "available_at": datetime(2020, 3, 19, 12, 30, tzinfo=UTC),
                "pdf_last_modified_at": "2020-03-19T12:29:55+00:00",
                "filename": "20200480.pdf",
                "release_number": "USDL 20-480-NAT",
                "value": 281_000,
                "prior": 211_000,
                "change": 70_000,
                "direction": "increase",
                "prior_status": "unrevised",
                "revision_old": None,
                "revision_new": None,
                "revision_delta": None,
                "annual_revision": True,
            },
        }
        for role, values in expected.items():
            record = by_id[getattr(self.roles, role)]
            release_date = str(values["release_date"])
            week_ending = str(values["week_ending"])
            expected_published_at = values["published_at"]
            expected_available_at = values["available_at"]
            assert isinstance(expected_published_at, datetime)
            assert isinstance(expected_available_at, datetime)
            if record.source.source_id != DOL_UI_CLAIMS_SOURCE_ID:
                raise ValueError("DOL initial-claims lock accepts only archived release facts")
            if record.source.publisher != (
                "U.S. Department of Labor Employment and Training Administration"
            ):
                raise ValueError("DOL initial-claims source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("DOL initial-claims inputs must use versioned snapshots")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("DOL initial-claims source license boundary mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("DOL initial-claims values must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"DOL initial-claims {role} entity mismatch")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("DOL initial-claims timing must be deterministic")
            if record.interval.published_at != expected_published_at:
                raise ValueError(f"DOL initial-claims {role} publication time mismatch")
            if record.interval.available_at != expected_available_at:
                raise ValueError(f"DOL initial-claims {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("DOL initial-claims lock contains a post-decision input")
            expected_valid_from = datetime.fromisoformat(f"{week_ending}T00:00:00+00:00")
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"DOL initial-claims {role} valid time mismatch")
            expected_pdf_last_modified = datetime.fromisoformat(
                str(values["pdf_last_modified_at"])
            )
            if record.source.vintage_as_of != expected_pdf_last_modified:
                raise ValueError(f"DOL initial-claims {role} source vintage mismatch")
            expected_url = (
                "https://www.dol.gov/sites/dolgov/files/OPA/newsreleases/ui-claims/"
                f"{values['filename']}"
            )
            if str(record.source.url) != expected_url:
                raise ValueError(f"DOL initial-claims {role} source URL mismatch")
            expected_source_version = (
                f"DOL-UI:{release_date}:{str(values['release_number']).replace(' ', '-')}:"
                f"sha256:{record.source.sha256[:24]}"
            )
            if record.source.source_version != expected_source_version:
                raise ValueError(f"DOL initial-claims {role} source version mismatch")
            payload = record.payload
            if payload.get("release_date") != release_date:
                raise ValueError(f"DOL initial-claims {role} release-date mismatch")
            if payload.get("week_ending") != week_ending:
                raise ValueError(f"DOL initial-claims {role} week-ending mismatch")
            if payload.get("metric") != "seasonally_adjusted_initial_claims":
                raise ValueError(f"DOL initial-claims {role} metric mismatch")
            if payload.get("unit") != "Persons":
                raise ValueError(f"DOL initial-claims {role} unit mismatch")
            if payload.get("arithmetic_verified") is not True:
                raise ValueError(f"DOL initial-claims {role} arithmetic flag mismatch")
            if (
                payload.get("availability_method")
                != "max_explicit_embargo_end_and_pdf_last_modified"
            ):
                raise ValueError("DOL initial-claims availability method mismatch")
            if payload.get("pdf_last_modified_at") != values["pdf_last_modified_at"]:
                raise ValueError(f"DOL initial-claims {role} PDF modification time mismatch")
            if payload.get("release_number") != values["release_number"]:
                raise ValueError(f"DOL initial-claims {role} release number mismatch")
            if payload.get("release_time_eastern") != "08:30:00":
                raise ValueError(f"DOL initial-claims {role} release time mismatch")
            if payload.get("snapshot_semantics") != (
                "advance value reported in this archived release"
            ):
                raise ValueError(f"DOL initial-claims {role} snapshot semantics mismatch")
            checks = {
                "value_persons": values["value"],
                "prior_level_persons": values["prior"],
                "reported_change_persons": values["change"],
                "reported_direction": values["direction"],
                "prior_level_status": values["prior_status"],
                "prior_level_revision_old_persons": values["revision_old"],
                "prior_level_revision_new_persons": values["revision_new"],
                "prior_level_revision_delta_persons": values["revision_delta"],
                "annual_revision_release": values["annual_revision"],
            }
            for field, expected_value in checks.items():
                if payload.get(field) != expected_value:
                    raise ValueError(f"DOL initial-claims {role} {field} mismatch")
            current = _integer_payload(payload, "value_persons", role)
            prior = _integer_payload(payload, "prior_level_persons", role)
            change = _integer_payload(payload, "reported_change_persons", role)
            if current <= 0 or prior <= 0 or max(current, prior, abs(change)) > 100_000_000:
                raise ValueError(f"DOL initial-claims {role} value is outside supported range")
            if prior + change != current:
                raise ValueError(f"DOL initial-claims {role} values do not reconcile")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match DOL initial-claims input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> InitialClaimsBoundaryInputLock:
        """Normalize, validate, and self-hash a DOL initial-claims input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_initial_claims_boundary_input_lock(path: Path) -> InitialClaimsBoundaryInputLock:
    try:
        return InitialClaimsBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid DOL initial-claims input lock: {path}") from error


def build_initial_claims_boundary_replay_spec(
    lock: InitialClaimsBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 initial-claims boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(lock.decision_time, source_ids=[DOL_UI_CLAIMS_SOURCE_ID])
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the DOL initial-claims input lock")
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-dol-initial-claims-release-query",
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
            "The lock contains only two archived advance initial-claims releases.",
            "Each PDF is eligible at the later of its embargo end or Last-Modified time.",
            "The March 19 release applies annual seasonal-factor revisions.",
            "Weekly administrative claims are volatile and subject to following-week revision.",
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
        range_width=metrics["claims_range_width_persons"],
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
                "This aggregate initial-claims boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no individual claimant, employer network, "
                "position, order, execution, portfolio, or allocation input is invented."
            ),
        },
        limitations=("Static rendering does not validate the claims-range heuristic.",),
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
            "Four actual engines ran over two locked DOL initial-claims release facts available "
            "before the decision time. Reported claims remain reported; the persistence-or-one-"
            "known-increase range remains inferred with no assigned probability; TrialCourt "
            "rejects retrospective promotion. The March 21 claims release is held only in a "
            "disjoint post-decision event lock. This is not a forecast, calibrated interval, "
            "pandemic or labor-market causal model, trading signal, production deployment, or "
            "external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are latest-value persistence or one repetition of the single known "
            "weekly increase; they carry no probability or coverage guarantee.",
            "The March 21 claims release is excluded from every decision input and artifact.",
            "The March 19 annual seasonal-factor revision prevents a stationary-sample claim.",
            "Two aggregate releases do not identify people, employers, causes, or policy effects.",
            "No position, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: InitialClaimsBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    march07 = _claims_value(records_by_role["march07_claims"])
    march14 = _claims_value(records_by_role["march14_claims"])
    known_increase = march14 - march07
    lower = march14
    upper = march14 + known_increase
    width = upper - lower
    if known_increase <= 0 or width <= 0:
        raise ValueError("two known DOL releases must establish a positive weekly increase")
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-claims-range",
        target_id=_ENTITY_ID,
        variable="next_reported_seasonally_adjusted_initial_claims_persons",
        unit="persons",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use latest-value persistence as the lower endpoint and one repetition of the only "
            "known March 7-to-March 14 increase as the upper endpoint."
        ),
        limitations=(
            "One known weekly increase defines a transparent stress envelope, not a forecast "
            "or confidence interval.",
            "The March 19 annual seasonal-factor revision limits adjacent-snapshot comparability.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-claims-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate persistence and one repetition of the known weekly increase using only "
            "DOL releases available at the historical decision boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, pandemic, or policy "
            "interpretation.",
            "The March 21 claims release is excluded and evaluated only afterward.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, "next_reported_seasonally_adjusted_initial_claims_persons")
    initial_state = {state_key: float(march14)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "march07_claims_persons": march07,
        "march14_claims_persons": march14,
        "known_weekly_increase_persons": known_increase,
        "claims_lower_persons": lower,
        "claims_upper_persons": upper,
        "claims_range_width_persons": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.claims-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-initial-claims-continuation-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_claims": metrics,
            "naive_baseline": {
                "next_reported_seasonally_adjusted_initial_claims_persons": march14,
                "definition": "persistence of the latest known DOL initial-claims value",
            },
            "bound_construction": {
                "lower_claims_persons": lower,
                "upper_claims_persons": upper,
                "range_width_persons": width,
                "known_weekly_increase_persons": known_increase,
                "endpoint_method": "latest_persistence_or_repeat_known_weekly_increase",
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
            "The upper endpoint mechanically repeats one known weekly increase.",
            "The March 21 claims release is absent from the bound construction.",
            "The range is not a claims probability, pandemic model, or causal claim.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: InitialClaimsBoundaryInputLock,
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
        trial_id=f"{lock.artifact_prefix}-retrospective-claims-screen",
        hypothesis=(
            "A retrospectively constructed one-increase initial-claims boundary qualifies for "
            "research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "Weekly initial claims are administrative labor-market counts, but two release "
            "snapshots and one later outcome cannot establish predictive validity or causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-increase continuation range width in persons",
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
        output_manifest_sha256=_hash({"claims_range_width_persons": range_width}),
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
            "known-weekly-increase": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No March 21 claims event fact is present in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective DOL initial-claims attempt must fail closed")
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
            claim_id="claim-reported-initial-claims-releases",
            statement=(
                "Archived DOL releases report seasonally adjusted initial claims of "
                f"{metrics['march07_claims_persons']:,} persons for the week ending March 7 and "
                f"{metrics['march14_claims_persons']:,} persons for the week ending March 14, "
                "2020."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are aggregate advance administrative counts, not forecasts or causal "
                "estimates."
            ),
            limitations=(
                "The pack includes only two release snapshots across an annual revision.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-initial-claims-continuation-range",
            statement=(
                "The next-release stress endpoints are persistence or one repeat of the known "
                f"{metrics['known_weekly_increase_persons']:,}-person increase: "
                f"[{metrics['claims_lower_persons']:,}, "
                f"{metrics['claims_upper_persons']:,}] persons."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability or coverage guarantee.",
            limitations=(
                "The March 21 claims value was not used to set the interval.",
                "The range has no causal, pandemic, or policy interpretation.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement="TrialCourt rejected the retrospective one-increase claims attempt.",
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
            boundary="The sentinels are not orders, capacity, capital, or returns.",
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
    lock: InitialClaimsBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _claims_value(record: BitemporalRecord) -> int:
    value = record.payload["value_persons"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("DOL initial claims must be integer persons")
    return value


def _integer_payload(payload: dict[str, Any], field: str, role: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"DOL initial-claims {role} {field} must be an integer")
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
