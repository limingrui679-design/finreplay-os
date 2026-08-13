"""Configurable seven-engine bank filing boundary replays over locked SEC facts."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from finreplay.contracts import (
    ArtifactStatus,
    BitemporalRecord,
    CostModel,
    EvidenceClass,
    ScenarioMode,
    TemporalCoverage,
    TrialDisposition,
    TrialSpec,
)
from finreplay.engines import (
    AllocationProblem,
    AssetCandidate,
    CapitalAllocator,
    EngineName,
    ExecutionLab,
    ExecutionPolicy,
    ExecutionPrecision,
    MarketEdge,
    MarketNode,
    MarketObservation,
    MarketTwin,
    NodeKind,
    OrderKind,
    OrderSide,
    OrderSpec,
    ReplayArtifact,
    ReplayClaim,
    ReplayPackSpec,
    RiskScenario,
    ShockCompiler,
    ShockOperation,
    ShockParameter,
    ShockProgram,
    TemporalEvidence,
    TimeVault,
    TrialAttempt,
    TrialCourt,
)

SEC_COMPANYFACTS_SOURCE_ID = "sec.xbrl.companyfacts"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BankFactConcepts(_StrictModel):
    """The seven filing concepts assigned to stable analytical roles."""

    assets: str = Field(min_length=2, max_length=200)
    deposits: str = Field(min_length=2, max_length=200)
    equity: str = Field(min_length=2, max_length=200)
    htm_value: str = Field(min_length=2, max_length=200)
    htm_loss: str = Field(min_length=2, max_length=200)
    afs_value: str = Field(min_length=2, max_length=200)
    afs_loss: str = Field(min_length=2, max_length=200)

    @model_validator(mode="after")
    def validate_unique_concepts(self) -> BankFactConcepts:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("bank fact concepts must be unique")
        return self


class BankBoundaryInputLock(_StrictModel):
    """Content-addressed configuration and seven immutable SEC decision inputs."""

    schema_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    issuer_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    issuer_label: str = Field(min_length=2, max_length=200)
    decision_time: datetime
    balance_date: datetime
    build_epoch: datetime
    selected_accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    concepts: BankFactConcepts
    source_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: tuple[BitemporalRecord, ...] = Field(min_length=7, max_length=7)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> BankBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.balance_date, "balance_date")
        _require_aware(self.build_epoch, "build_epoch")
        if self.balance_date > self.decision_time:
            raise ValueError("bank balance_date cannot follow decision_time")
        if self.build_epoch < self.decision_time:
            raise ValueError("bank build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("bank locked record IDs must be unique and sorted")
        expected_concepts = set(self.concepts.model_dump().values())
        actual_concepts = {str(record.payload.get("concept")) for record in self.records}
        if actual_concepts != expected_concepts:
            raise ValueError("bank lock must contain each configured concept exactly once")
        expected_end = self.balance_date.date().isoformat()
        for record in self.records:
            if record.source.source_id != SEC_COMPANYFACTS_SOURCE_ID:
                raise ValueError("bank lock accepts only SEC Company Facts")
            if record.source.sha256 != self.source_response_sha256:
                raise ValueError("bank lock source hashes must match source_response_sha256")
            if record.source.temporal_coverage is not TemporalCoverage.IMMUTABLE_EVENT:
                raise ValueError("bank inputs must use immutable-event evidence")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("bank XBRL inputs must remain reported evidence")
            if record.interval.available_at > self.decision_time:
                raise ValueError("bank lock cannot include facts unavailable at decision_time")
            if record.payload.get("accn") != self.selected_accession:
                raise ValueError("bank lock records must share the selected accession")
            if record.payload.get("end") != expected_end:
                raise ValueError("bank lock records must share the configured balance date")
            if record.payload.get("unit") != "USD" or float(record.payload.get("val", 0)) <= 0:
                raise ValueError("bank locked facts must be positive USD values")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match bank input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> BankBoundaryInputLock:
        """Normalize, validate, and seal an input lock payload."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_bank_boundary_input_lock(path: Path) -> BankBoundaryInputLock:
    try:
        return BankBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid bank boundary input lock: {path}") from error


def build_bank_boundary_replay_spec(
    lock: BankBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run all seven engines for one content-addressed bank boundary scenario."""

    records = lock.records
    records_by_role = _records_by_role(lock)
    source_record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    maximum_available_at = max(record.interval.available_at for record in records)

    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                valid_at=lock.balance_date,
                source_ids=[SEC_COMPANYFACTS_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != source_record_ids:
        raise ValueError("TimeVault did not reproduce the bank input-lock record set")

    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="bitemporal-query",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.REPORTED: len(selected)},
        source_set_historical_replay_eligible=True,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "append": asdict(append_receipt),
            "manifest": {
                **asdict(manifest),
                "generated_at": _canonical_datetime(manifest.generated_at),
            },
            "selected_record_ids": list(source_record_ids),
            "max_available_at": _canonical_datetime(maximum_available_at),
            "decision_time": _canonical_datetime(lock.decision_time),
            "valid_at": _canonical_datetime(lock.balance_date),
        },
        limitations=(
            "The input lock contains seven selected filing facts, not the complete SEC response.",
            "Historical eligibility follows exact EDGAR acceptance time and immutable accession "
            "facts.",
        ),
    )
    trial_artifact = _run_trialcourt(
        lock=lock,
        records_by_role=records_by_role,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream=timevault_artifact.artifact_id,
        code_commit=code_commit,
    )
    market_artifact, market_snapshot_hash, modeled_issuer_loss = _run_markettwin(
        lock=lock,
        records_by_role=records_by_role,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream=timevault_artifact.artifact_id,
    )
    shock_artifact, htm_loss_ratio = _run_shockcompiler(
        lock=lock,
        records_by_role=records_by_role,
        source_hashes=source_hashes,
        upstream=(timevault_artifact.artifact_id, market_artifact.artifact_id),
    )
    execution_artifact, execution_capacity_usd, transaction_cost_bps = _run_executionlab(
        lock=lock,
        records_by_role=records_by_role,
        htm_loss_ratio=htm_loss_ratio,
        upstream=shock_artifact.artifact_id,
    )
    allocation_artifact = _run_capitalallocator(
        lock=lock,
        records_by_role=records_by_role,
        htm_loss_ratio=htm_loss_ratio,
        modeled_issuer_loss=modeled_issuer_loss,
        execution_capacity_usd=execution_capacity_usd,
        transaction_cost_bps=transaction_cost_bps,
        source_hashes=source_hashes,
        upstream=(
            execution_artifact.artifact_id,
            shock_artifact.artifact_id,
            trial_artifact.artifact_id,
        ),
    )
    replaystudio_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.replaystudio.render",
        engine=EngineName.REPLAYSTUDIO,
        artifact_kind="static-report-contract",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.EXTRACTED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=tuple(
            artifact.artifact_id
            for artifact in (
                allocation_artifact,
                execution_artifact,
                market_artifact,
                shock_artifact,
                timevault_artifact,
                trial_artifact,
            )
        ),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "renderer": "ReplayStudio",
            "portable_file_count_excluding_manifest": 5,
            "market_snapshot_sha256": market_snapshot_hash,
            "truth_labels_visible": True,
        },
        limitations=(
            "Static report generation does not independently validate upstream economic methods.",
        ),
    )
    artifacts = (
        timevault_artifact,
        trial_artifact,
        market_artifact,
        shock_artifact,
        execution_artifact,
        allocation_artifact,
        replaystudio_artifact,
    )
    compressed_input_bytes = len(
        gzip.compress(_canonical_json(lock.model_dump(mode="json")).encode(), mtime=0)
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
        claims=_claims(
            lock=lock,
            records_by_role=records_by_role,
            artifacts={artifact.engine: artifact for artifact in artifacts},
            htm_loss_ratio=htm_loss_ratio,
        ),
        require_all_engines=True,
        distinct_input_records=len(records),
        derived_records=(
            int(trial_artifact.payload["manifest"]["entries"])
            + int(market_artifact.payload["manifest"]["node_versions"])
            + int(market_artifact.payload["manifest"]["edge_versions"])
            + len(shock_artifact.payload["compiled"]["trials"])
            + 3
        ),
        compressed_input_bytes=compressed_input_bytes,
        elapsed_seconds=0.0,
        claim_boundary=(
            f"Seven actual engine implementations ran over seven locked SEC filing facts for "
            f"{lock.issuer_label}. Filer-reported values remain reported; ratios, network "
            "propagation, and TrialCourt disposition are model-derived; execution and allocation "
            "inputs are explicitly simulated. The separate post-decision official event lock is "
            "not a ReplayPack input. This is a retrospective boundary replay, not causal failure "
            "attribution, a historical trading signal, investment advice, client work, production "
            "deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "Post-decision event evidence is excluded from the decision input manifest.",
            "No historical quote, order-book, venue, or volume data is represented; ExecutionLab "
            "uses a normalized simulated reference-only boundary.",
            "TrialCourt rejects the retrospective non-inferential attempt rather than granting "
            "strategy eligibility.",
            "CapitalAllocator emits a declared model boundary and no order or recommendation.",
            "A fixed build epoch and zero pack elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_trialcourt(
    *,
    lock: BankBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    upstream: str,
    code_commit: str,
) -> ReplayArtifact:
    htm_ratio = _ratio(records_by_role["htm_loss"], records_by_role["htm_value"])
    afs_ratio = _ratio(records_by_role["afs_loss"], records_by_role["afs_value"])
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=5)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-screen",
        hypothesis=(
            f"A retrospectively constructed {lock.issuer_label} securities-loss screen qualifies "
            "for research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "Reported securities valuation gaps can interact with funding pressure, but a "
            "retrospective single-event reconstruction cannot establish predictive validity."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=5,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="reported HTM minus AFS loss-ratio difference",
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
        output_manifest_sha256=_hash({"htm_loss_ratio": htm_ratio, "afs_loss_ratio": afs_ratio}),
        decision_time=lock.decision_time,
        max_input_available_at=max(
            record.interval.available_at for record in records_by_role.values()
        ),
        training_end=lock.balance_date.date(),
        evaluation_start=holdout_start,
        evaluation_end=holdout_start + timedelta(days=4),
        sample_size=2,
        metric_value=htm_ratio - afs_ratio,
        p_value=1.0,
        gross_return_bps=0.0,
        one_way_turnover=0.0,
        short_fraction=0.0,
        requested_capital_usd=1.0,
        median_daily_volume_usd=1.0,
        regime_metric_values={"single-balance-sheet-slice": htm_ratio - afs_ratio},
        notes=(
            "Retrospective boundary attempt; p=1 records that no inferential test was performed.",
            "One-dollar operational fields are non-trading schema sentinels, not capacity "
            "evidence.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective bank TrialCourt attempt must fail closed")
    return ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.trialcourt.retrospective-gate",
        engine=EngineName.TRIALCOURT,
        artifact_kind="adversarial-decision",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={
            EvidenceClass.REPORTED: 4,
            EvidenceClass.INFERRED: 1,
            EvidenceClass.SIMULATED: 1,
        },
        source_set_historical_replay_eligible=True,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
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
            "The rejected attempt is a retrospective method boundary, not a validated strategy.",
            "P-value and operational sentinel fields deliberately carry no performance claim.",
        ),
    )


def _run_markettwin(
    *,
    lock: BankBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, str, float]:
    assets = records_by_role["assets"]
    equity = records_by_role["equity"]
    deposits = records_by_role["deposits"]
    htm = records_by_role["htm_value"]
    htm_loss = records_by_role["htm_loss"]
    afs = records_by_role["afs_value"]
    afs_loss = records_by_role["afs_loss"]
    available_at = max(record.interval.available_at for record in records_by_role.values())
    temporal = TemporalEvidence(valid_from=lock.balance_date, available_at=available_at)
    issuer_node = f"issuer:{lock.issuer_slug}"
    htm_node = f"security:{lock.issuer_slug}-htm-portfolio"
    afs_node = f"security:{lock.issuer_slug}-afs-portfolio"
    nodes = (
        MarketNode(
            node_id=issuer_node,
            label=lock.issuer_label,
            kind=NodeKind.ISSUER,
            loss_absorption_usd=float(equity.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            temporal=temporal,
            source=equity.source,
            attributes={
                "assets_usd": int(assets.payload["val"]),
                "deposits_usd": int(deposits.payload["val"]),
                "sec_accession": lock.selected_accession,
            },
        ),
        MarketNode(
            node_id=htm_node,
            label=f"{lock.issuer_label} held-to-maturity securities portfolio",
            kind=NodeKind.SECURITY,
            loss_absorption_usd=float(htm.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            temporal=temporal,
            source=htm.source,
            attributes={
                "carrying_value_usd": int(htm.payload["val"]),
                "unrecognized_loss_usd": int(htm_loss.payload["val"]),
            },
        ),
        MarketNode(
            node_id=afs_node,
            label=f"{lock.issuer_label} available-for-sale securities portfolio",
            kind=NodeKind.SECURITY,
            loss_absorption_usd=float(afs.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            temporal=temporal,
            source=afs.source,
            attributes={
                "reported_value_usd": int(afs.payload["val"]),
                "gross_unrealized_loss_usd": int(afs_loss.payload["val"]),
            },
        ),
    )
    edges = (
        MarketEdge(
            edge_id=f"{lock.issuer_slug}-issuer-holds-htm",
            source_node=htm_node,
            target_node=issuer_node,
            relation="reported portfolio exposure",
            exposure_lower_usd=float(htm.payload["val"]),
            exposure_upper_usd=float(htm.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            confidence=1.0,
            temporal=temporal,
            source=htm.source,
        ),
        MarketEdge(
            edge_id=f"{lock.issuer_slug}-issuer-holds-afs",
            source_node=afs_node,
            target_node=issuer_node,
            relation="reported portfolio exposure",
            exposure_lower_usd=float(afs.payload["val"]),
            exposure_upper_usd=float(afs.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            confidence=1.0,
            temporal=temporal,
            source=afs.source,
        ),
    )
    with MarketTwin() as twin:
        append_receipt = twin.append(nodes=nodes, edges=edges)
        snapshot = twin.snapshot(decision_time=lock.decision_time, valid_at=lock.balance_date)
        contagion = twin.propagate(
            snapshot,
            initial_shocks={htm_node: _ratio(htm_loss, htm)},
        )
        manifest = twin.manifest()
    issuer_loss = contagion.upper_loss_fraction[issuer_node]
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.markettwin.loss-envelope",
        engine=EngineName.MARKETTWIN,
        artifact_kind="temporal-network-envelope",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.REPORTED: 5, EvidenceClass.INFERRED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "append_receipt": asdict(append_receipt),
            "manifest": asdict(manifest),
            "snapshot": snapshot.model_dump(mode="json"),
            "contagion": contagion.model_dump(mode="json"),
        },
        limitations=(
            "The three-node graph includes only relationships represented by the locked facts.",
            "Loss propagation is a mechanical bounded channel, not causal failure attribution.",
        ),
    )
    return artifact, snapshot.graph_sha256, issuer_loss


def _run_shockcompiler(
    *,
    lock: BankBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: tuple[str, ...],
) -> tuple[ReplayArtifact, float]:
    htm = records_by_role["htm_value"]
    htm_loss = records_by_role["htm_loss"]
    ratio = _ratio(htm_loss, htm)
    target_id = f"security:{lock.issuer_slug}-htm-portfolio"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-bounded-htm-loss-realization",
        target_id=target_id,
        variable="loss_fraction",
        unit="fraction",
        operation=ShockOperation.SET,
        lower=0.0,
        upper=ratio,
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=tuple(sorted((htm.record_id, htm_loss.record_id))),
        sources=(htm.source,),
        derivation=(
            "Bounds realization between zero and filer-reported unrecognized HTM loss divided "
            "by filer-reported HTM value."
        ),
        limitations=(
            "The bound omits liquidation timing, hedges, tax effects, funding support, and prices.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-bounded-htm-realization",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate both endpoints of a loss-realization interval without assigning an "
            "unsupported midpoint or probability."
        ),
        global_limitations=(
            "This shock program supplies a model input and does not forecast an outcome.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    applied_upper = ShockCompiler.apply(
        {(target_id, "loss_fraction"): 0.0},
        compiled.trials[-1],
    )
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.htm-bound",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-shock-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=parameter.source_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=upstream,
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "program": program.model_dump(mode="json"),
            "compiled": compiled.model_dump(mode="json"),
            "applied_upper": {
                f"{target}|{variable}": value for (target, variable), value in applied_upper.items()
            },
        },
        limitations=(
            "The upper endpoint is an inferred accounting bound, not an observed realized loss.",
        ),
    )
    return artifact, ratio


def _run_executionlab(
    *,
    lock: BankBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    htm_loss_ratio: float,
    upstream: str,
) -> tuple[ReplayArtifact, float, float]:
    htm_value = float(records_by_role["htm_value"].payload["val"])
    interval_end = lock.decision_time.replace(hour=23, minute=59, second=59)
    if interval_end <= lock.decision_time:
        interval_end = lock.decision_time + timedelta(hours=1)
    latency_ms = 1_000
    time_in_force_ms = max(
        1,
        int((interval_end - lock.decision_time).total_seconds() * 1_000) - latency_ms,
    )
    instrument_id = f"synthetic:{lock.issuer_slug}-htm-usd-unit"
    order = OrderSpec(
        order_id=f"{lock.artifact_prefix}-simulated-htm-liquidation",
        instrument_id=instrument_id,
        side=OrderSide.SELL,
        kind=OrderKind.MARKET,
        quantity=htm_value * htm_loss_ratio,
        decision_at=lock.decision_time,
        latency_ms=latency_ms,
        time_in_force_ms=time_in_force_ms,
    )
    observation = MarketObservation(
        observation_id=f"{lock.artifact_prefix}:simulated:reference-only",
        instrument_id=instrument_id,
        precision=ExecutionPrecision.REFERENCE_ONLY,
        interval_start=lock.decision_time,
        interval_end=interval_end,
        available_at=lock.decision_time,
        reference_price=1.0,
        estimated_daily_volume=htm_value,
        evidence_class=EvidenceClass.SIMULATED,
        source_record_ids=(),
        sources=(),
        limitations=(
            "One synthetic unit equals one reported portfolio-value dollar; it is not a quote.",
            "Estimated daily volume equals reported portfolio value solely as a stress "
            "normalization.",
        ),
    )
    policy = ExecutionPolicy(
        cost_model=_cost_model(),
        impact_lower_multiplier=0.5,
        impact_upper_multiplier=2.0,
        fallback_half_spread_upper_bps=100.0,
        fallback_impact_upper_bps=250.0,
        fallback_daily_capacity_fraction=0.25,
    )
    envelope = ExecutionLab().estimate(
        order=order,
        observation=observation,
        policy=policy,
        evaluated_at=interval_end,
    )
    transaction_cost_bps = float(envelope.slippage_bps_upper or 0.0)
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.executionlab.normalized-envelope",
        engine=EngineName.EXECUTIONLAB,
        artifact_kind="reference-only-execution-envelope",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.SIMULATED: 1},
        source_set_historical_replay_eligible=False,
        source_record_ids=(),
        source_hashes=(),
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "order": order.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "envelope": envelope.model_dump(mode="json"),
        },
        limitations=(
            "No historical quote, trade, order-book, venue, or volume evidence is represented.",
            "The envelope is a normalized simulated boundary, not an executable trade.",
        ),
    )
    return artifact, envelope.fill_quantity_upper, transaction_cost_bps


def _run_capitalallocator(
    *,
    lock: BankBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    htm_loss_ratio: float,
    modeled_issuer_loss: float,
    execution_capacity_usd: float,
    transaction_cost_bps: float,
    source_hashes: tuple[str, ...],
    upstream: tuple[str, ...],
) -> ReplayArtifact:
    htm = records_by_role["htm_value"]
    htm_loss = records_by_role["htm_loss"]
    source_ids = tuple(sorted((htm.record_id, htm_loss.record_id)))
    source = (htm.source,)
    total_capital = 10_000_000_000.0
    asset_id = f"asset:{lock.issuer_slug}-htm-model-exposure"
    asset = AssetCandidate(
        asset_id=asset_id,
        label=f"Synthetic {lock.issuer_label} HTM model exposure",
        expected_return_lower=-min(1.0, modeled_issuer_loss),
        expected_return_upper=0.0,
        current_weight=0.0,
        min_weight=0.0,
        max_weight=min(1.0, execution_capacity_usd / total_capital),
        capacity_usd=execution_capacity_usd,
        transaction_cost_bps=transaction_cost_bps,
        evidence_class=EvidenceClass.INFERRED,
        available_at=lock.decision_time,
        source_record_ids=source_ids,
        sources=source,
        derivation=(
            "Return lower bound is negative MarketTwin issuer loss; the upper bound is zero; "
            "capacity and costs come from the simulated ExecutionLab boundary."
        ),
        limitations=(
            "This synthetic model exposure is not a listed security, forecast, or recommendation.",
        ),
    )
    scenario = RiskScenario(
        scenario_id=f"scenario:{lock.issuer_slug}-htm-upper-bound",
        loss_fraction_by_asset={asset_id: htm_loss_ratio},
        evidence_class=EvidenceClass.INFERRED,
        available_at=lock.decision_time,
        source_record_ids=source_ids,
        sources=source,
        derivation=(
            "Scenario loss is the upper endpoint of unrecognized HTM loss divided by reported "
            "HTM value."
        ),
        limitations=("Accounting bound; not a probability-weighted market forecast.",),
    )
    problem = AllocationProblem(
        problem_id=f"problem:{lock.issuer_slug}-boundary-allocation",
        decision_time=lock.decision_time,
        total_capital_usd=total_capital,
        assets=(asset,),
        risk_scenarios=(scenario,),
        current_cash_weight=1.0,
        cash_return=0.0,
        cash_min_weight=0.0,
        cash_max_weight=1.0,
        max_one_way_turnover=1.0,
        loss_aversion=1.0,
        uncertainty_aversion=0.25,
        limitations=(
            "Research decision boundary only; no live capital, security, order, or investment "
            "advice.",
        ),
    )
    result = CapitalAllocator().solve(problem)
    if result.cash_weight is None or result.cash_weight < 1.0 - 1e-8:
        raise ValueError("bank boundary allocation must preserve the all-cash solution")
    return ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.capitalallocator.robust-boundary",
        engine=EngineName.CAPITALALLOCATOR,
        artifact_kind="robust-allocation-boundary",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 1, EvidenceClass.SIMULATED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=source_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=upstream,
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "problem": problem.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        },
        limitations=(
            "The all-cash result follows declared non-positive return bounds and is not a "
            "recommendation.",
            "Execution capacity and costs are simulated; accounting ratios are inferred from "
            "reported facts.",
        ),
    )


def _claims(
    *,
    lock: BankBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    artifacts: dict[EngineName, ReplayArtifact],
    htm_loss_ratio: float,
) -> tuple[ReplayClaim, ...]:
    assets = int(records_by_role["assets"].payload["val"])
    deposits = int(records_by_role["deposits"].payload["val"])
    htm = int(records_by_role["htm_value"].payload["val"])
    htm_loss = int(records_by_role["htm_loss"].payload["val"])
    return (
        ReplayClaim(
            claim_id="claim-reported-balance-sheet",
            statement=(
                f"The locked {lock.issuer_label} SEC filing facts report assets of ${assets:,}, "
                f"deposits of ${deposits:,}, HTM value of ${htm:,}, and HTM unrecognized loss "
                f"of ${htm_loss:,} at the selected balance date."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(artifacts[EngineName.TIMEVAULT].artifact_id,),
            boundary="These are filer-reported XBRL facts, not regulator findings.",
            limitations=("The pack includes a selected fact subset, not the complete filing.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-htm-bound",
            statement=(
                f"The modeled HTM loss-realization interval is 0 to {htm_loss_ratio:.12f}, "
                "the arithmetic ratio of two reported filing facts."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(artifacts[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The upper endpoint is an accounting-derived bound, not realized loss.",
            limitations=("No probability, timing, hedge, tax, or price path is assigned.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement=(
                "TrialCourt rejected the retrospective attempt after retaining all attack findings."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(artifacts[EngineName.TRIALCOURT].artifact_id,),
            boundary="Rejection is an internal method result, not external strategy review.",
            limitations=("The attempt makes no valid inferential or return claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-decision-boundary",
            statement=(
                "The normalized execution envelope and robust allocation are explicit "
                "simulations; the allocator retained the all-cash model solution."
            ),
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=tuple(
                sorted(
                    (
                        artifacts[EngineName.CAPITALALLOCATOR].artifact_id,
                        artifacts[EngineName.EXECUTIONLAB].artifact_id,
                    )
                )
            ),
            boundary="This is neither an executable order nor investment advice.",
            limitations=("No historical microstructure or real portfolio is represented.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-seven-engine-pack",
            statement="The ReplayPack contains one content-addressed artifact from each engine.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(artifacts[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _records_by_role(lock: BankBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_concept = {str(record.payload["concept"]): record for record in lock.records}
    return {role: by_concept[concept] for role, concept in lock.concepts.model_dump().items()}


def _ratio(numerator: BitemporalRecord, denominator: BitemporalRecord) -> float:
    return float(numerator.payload["val"]) / float(denominator.payload["val"])


def _cost_model() -> CostModel:
    return CostModel(
        commission_bps=1.0,
        half_spread_bps=10.0,
        market_impact_bps=25.0,
        borrow_bps_annual=100.0,
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
