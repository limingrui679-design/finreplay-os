"""Deterministic seven-engine SVB boundary replay over locked official SEC facts."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

SVB_DECISION_TIME = datetime(2023, 3, 8, 18, tzinfo=UTC)
SVB_BALANCE_DATE = datetime(2022, 12, 31, tzinfo=UTC)
REPLAY_BUILD_EPOCH = datetime(2026, 8, 13, 2, tzinfo=UTC)
SEC_SOURCE_ID = "sec.xbrl.companyfacts"
SEC_ACCESSION = "0000719739-23-000021"
SEC_FRAME = "CY2022Q4I"
REQUIRED_CONCEPTS = frozenset(
    {
        "Assets",
        "StockholdersEquity",
        "HeldToMaturitySecurities",
        "HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss",
        "AvailableForSaleSecuritiesDebtSecurities",
        "AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedLossBeforeTax",
        "Deposits",
    }
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SVBInputLock(_StrictModel):
    """Minimal content-addressed SEC fact set used by the SVB engine flow."""

    schema_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    scenario_id: str = "svb-2023-boundary"
    scenario_version: str = "1.0.0"
    decision_time: datetime
    balance_date: datetime
    selected_frame: str
    selected_accession: str
    source_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: tuple[BitemporalRecord, ...] = Field(min_length=7, max_length=7)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self) -> SVBInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.balance_date, "balance_date")
        if self.decision_time != SVB_DECISION_TIME:
            raise ValueError("SVB lock decision_time changed")
        if self.balance_date != SVB_BALANCE_DATE:
            raise ValueError("SVB lock balance_date changed")
        if self.selected_frame != SEC_FRAME or self.selected_accession != SEC_ACCESSION:
            raise ValueError("SVB lock must use the declared SEC frame and accession")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("SVB locked record IDs must be unique and sorted")
        concepts = {str(record.payload.get("concept")) for record in self.records}
        if concepts != REQUIRED_CONCEPTS:
            raise ValueError("SVB lock must contain every required concept exactly once")
        for record in self.records:
            if record.source.source_id != SEC_SOURCE_ID:
                raise ValueError("SVB lock accepts only the SEC Company Facts source")
            if record.source.sha256 != self.source_response_sha256:
                raise ValueError("SVB lock source hashes must match source_response_sha256")
            if record.source.temporal_coverage is not TemporalCoverage.IMMUTABLE_EVENT:
                raise ValueError("SVB decision inputs must be immutable-event evidence")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("SVB locked XBRL facts must remain reported evidence")
            if record.interval.available_at > self.decision_time:
                raise ValueError("SVB lock cannot include facts unavailable at decision_time")
            if record.payload.get("frame") != self.selected_frame:
                raise ValueError("SVB locked fact frame changed")
            if record.payload.get("accn") != self.selected_accession:
                raise ValueError("SVB locked fact accession changed")
            if record.payload.get("unit") != "USD" or float(record.payload.get("val", 0)) <= 0:
                raise ValueError("SVB locked facts must be positive USD values")
        if _hash(_input_lock_payload(self)) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match SVB input-lock content")
        return self

    @classmethod
    def create(cls, records: tuple[BitemporalRecord, ...]) -> SVBInputLock:
        ordered = tuple(sorted(records, key=lambda record: record.record_id))
        if not ordered:
            raise ValueError("SVB input lock requires records")
        source_hashes = {record.source.sha256 for record in ordered}
        if len(source_hashes) != 1:
            raise ValueError("SVB input lock requires one source-response hash")
        values: dict[str, Any] = {
            "schema_version": "1.0.0",
            "scenario_id": "svb-2023-boundary",
            "scenario_version": "1.0.0",
            "decision_time": _canonical_datetime(SVB_DECISION_TIME),
            "balance_date": _canonical_datetime(SVB_BALANCE_DATE),
            "selected_frame": SEC_FRAME,
            "selected_accession": SEC_ACCESSION,
            "source_response_sha256": next(iter(source_hashes)),
            "records": [record.model_dump(mode="json") for record in ordered],
            "claim_boundary": (
                "These seven records are a minimal extracted lock of filer-reported SEC XBRL "
                "facts accepted before the replay decision time. The lock preserves record IDs, "
                "accession, availability timestamps, source URL, response hash, and evidence "
                "class. It is not the complete SEC response, a regulator finding, an observed "
                "trading signal, a causal explanation of the bank failure, or proof that the "
                "current SEC endpoint can reproduce the exact 2026 response bytes forever."
            ),
        }
        return cls.model_validate({**values, "lock_sha256": _hash(values)})


def load_svb_input_lock(path: Path) -> SVBInputLock:
    try:
        return SVBInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid SVB input lock: {path}") from error


def build_svb_replay_spec(lock: SVBInputLock, *, code_commit: str) -> ReplayPackSpec:
    """Run all six analytical engines and return the seventh engine's pack specification."""

    records = lock.records
    records_by_concept = _records_by_concept(records)
    source_record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    max_available_at = max(record.interval.available_at for record in records)

    with TimeVault() as vault:
        vault_append = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                SVB_DECISION_TIME,
                valid_at=SVB_BALANCE_DATE,
                source_ids=[SEC_SOURCE_ID],
            )
        )
        vault_manifest = vault.manifest(generated_at=REPLAY_BUILD_EPOCH)
    if tuple(record.record_id for record in selected) != source_record_ids:
        raise ValueError("TimeVault replay did not reproduce the locked SEC record set")

    timevault_artifact = ReplayArtifact.create(
        artifact_id="svb.timevault.query",
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
            "append": asdict(vault_append),
            "manifest": {
                **asdict(vault_manifest),
                "generated_at": _canonical_datetime(vault_manifest.generated_at),
            },
            "selected_record_ids": list(source_record_ids),
            "max_available_at": _canonical_datetime(max_available_at),
            "decision_time": _canonical_datetime(SVB_DECISION_TIME),
            "valid_at": _canonical_datetime(SVB_BALANCE_DATE),
        },
        limitations=(
            "The committed input lock contains seven extracted facts, not the complete SEC "
            "response.",
            "Historical eligibility follows SEC filing acceptance timestamps and immutable "
            "accession facts.",
        ),
    )

    trial_artifact = _run_trialcourt(
        lock=lock,
        records_by_concept=records_by_concept,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream=timevault_artifact.artifact_id,
        code_commit=code_commit,
    )
    market_artifact, market_snapshot_hash, modeled_issuer_loss = _run_markettwin(
        records_by_concept=records_by_concept,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream=timevault_artifact.artifact_id,
    )
    shock_artifact, htm_loss_ratio = _run_shockcompiler(
        records_by_concept=records_by_concept,
        source_hashes=source_hashes,
        upstream=(timevault_artifact.artifact_id, market_artifact.artifact_id),
    )
    execution_artifact, execution_capacity_usd, transaction_cost_bps = _run_executionlab(
        records_by_concept=records_by_concept,
        htm_loss_ratio=htm_loss_ratio,
        upstream=shock_artifact.artifact_id,
    )
    allocation_artifact = _run_capitalallocator(
        records_by_concept=records_by_concept,
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
        artifact_id="svb.replaystudio.render",
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
        replay_id="svb-2023-seven-engine-v1",
        scenario_id=lock.scenario_id,
        scenario_version=lock.scenario_version,
        title="SVB 2023 point-in-time boundary replay",
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=SVB_DECISION_TIME,
        created_at=REPLAY_BUILD_EPOCH,
        code_commit=code_commit,
        status=ArtifactStatus.REPRODUCED,
        artifacts=artifacts,
        claims=_claims(
            records_by_concept=records_by_concept,
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
            + 3  # Execution envelope, allocation result, and ReplayStudio artifact.
        ),
        compressed_input_bytes=compressed_input_bytes,
        elapsed_seconds=0.0,
        claim_boundary=(
            "Seven actual engine implementations ran over a locked seven-record SEC fact set. "
            "Filer-reported balance-sheet values remain reported; ratios, network propagation, "
            "and the TrialCourt disposition are model-derived; execution and capital-allocation "
            "inputs are explicitly simulated research boundaries. This is a retrospective "
            "historical boundary replay, not a live 2023 system, causal failure attribution, "
            "trading performance, investment advice, client work, production deployment, or "
            "external validation. Runtime is excluded from deterministic pack identity and is "
            "recorded separately by the replay verifier."
        ),
        limitations=(
            lock.claim_boundary,
            "FDIC and Treasury current-table snapshots are excluded from the 2023 decision input.",
            "No historical quote, order-book, or venue data was available; ExecutionLab uses a "
            "normalized simulated reference-only boundary.",
            "TrialCourt correctly rejects the retrospective, non-inferential attempt rather "
            "than granting strategy eligibility.",
            "CapitalAllocator operates on declared simulated/inferred bounds and emits no order "
            "or recommendation.",
            "A fixed build epoch and zero pack elapsed time support byte reproducibility; "
            "measured runtime lives in a separate receipt.",
        ),
    )


def _run_trialcourt(
    *,
    lock: SVBInputLock,
    records_by_concept: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    upstream: str,
    code_commit: str,
) -> ReplayArtifact:
    htm_ratio = _ratio(
        records_by_concept["HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss"],
        records_by_concept["HeldToMaturitySecurities"],
    )
    afs_ratio = _ratio(
        records_by_concept["AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedLossBeforeTax"],
        records_by_concept["AvailableForSaleSecuritiesDebtSecurities"],
    )
    cost_model = _cost_model()
    spec = TrialSpec(
        trial_id="svb-retrospective-screen",
        hypothesis=(
            "A retrospectively constructed SVB securities-loss screen qualifies for research "
            "eligibility after all preregistration and adversarial gates."
        ),
        economic_mechanism=(
            "Reported securities valuation gaps can interact with funding pressure, but a "
            "retrospective single-event reconstruction cannot establish predictive validity."
        ),
        preregistered_at=REPLAY_BUILD_EPOCH,
        holdout_start=date(2023, 3, 9),
        holdout_end=date(2023, 3, 14),
        purge_days=5,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="reported HTM minus AFS loss-ratio difference",
        expected_direction="positive",
        cost_model=cost_model,
        disposition=TrialDisposition.REVISE,
    )
    attempt = TrialAttempt(
        attempt_id="svb-retrospective-attempt-1",
        trial_id=spec.trial_id,
        attempt_number=1,
        completed_at=REPLAY_BUILD_EPOCH,
        code_commit=code_commit,
        config_sha256=_hash(spec.model_dump(mode="json")),
        input_manifest_sha256=lock.lock_sha256,
        output_manifest_sha256=_hash({"htm_loss_ratio": htm_ratio, "afs_loss_ratio": afs_ratio}),
        decision_time=SVB_DECISION_TIME,
        max_input_available_at=max(
            record.interval.available_at for record in records_by_concept.values()
        ),
        training_end=date(2022, 12, 31),
        evaluation_start=date(2023, 3, 9),
        evaluation_end=date(2023, 3, 13),
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
            "One-dollar operational fields are non-trading schema sentinels and not capacity "
            "evidence.",
        ),
    )
    with TrialCourt(clock=lambda: REPLAY_BUILD_EPOCH) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective SVB TrialCourt attempt must fail closed")
    return ReplayArtifact.create(
        artifact_id="svb.trialcourt.retrospective-gate",
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
    records_by_concept: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, str, float]:
    assets = records_by_concept["Assets"]
    equity = records_by_concept["StockholdersEquity"]
    deposits = records_by_concept["Deposits"]
    htm = records_by_concept["HeldToMaturitySecurities"]
    htm_loss = records_by_concept["HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss"]
    afs = records_by_concept["AvailableForSaleSecuritiesDebtSecurities"]
    afs_loss = records_by_concept[
        "AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedLossBeforeTax"
    ]
    available_at = max(record.interval.available_at for record in records_by_concept.values())
    temporal = TemporalEvidence(valid_from=SVB_BALANCE_DATE, available_at=available_at)
    nodes = (
        MarketNode(
            node_id="issuer:svb-financial-group",
            label="SVB Financial Group",
            kind=NodeKind.ISSUER,
            loss_absorption_usd=float(equity.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            temporal=temporal,
            source=equity.source,
            attributes={
                "assets_usd": int(assets.payload["val"]),
                "deposits_usd": int(deposits.payload["val"]),
                "sec_accession": SEC_ACCESSION,
            },
        ),
        MarketNode(
            node_id="security:svb-htm-portfolio",
            label="SVB held-to-maturity securities portfolio",
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
            node_id="security:svb-afs-portfolio",
            label="SVB available-for-sale securities portfolio",
            kind=NodeKind.SECURITY,
            loss_absorption_usd=float(afs.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            temporal=temporal,
            source=afs.source,
            attributes={
                "fair_value_usd": int(afs.payload["val"]),
                "gross_unrealized_loss_usd": int(afs_loss.payload["val"]),
            },
        ),
    )
    edges = (
        MarketEdge(
            edge_id="svb-issuer-holds-htm",
            source_node="security:svb-htm-portfolio",
            target_node="issuer:svb-financial-group",
            relation="reported portfolio exposure",
            exposure_lower_usd=float(htm.payload["val"]),
            exposure_upper_usd=float(htm.payload["val"]),
            evidence_class=EvidenceClass.REPORTED,
            confidence=1.0,
            temporal=temporal,
            source=htm.source,
        ),
        MarketEdge(
            edge_id="svb-issuer-holds-afs",
            source_node="security:svb-afs-portfolio",
            target_node="issuer:svb-financial-group",
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
        snapshot = twin.snapshot(decision_time=SVB_DECISION_TIME, valid_at=SVB_BALANCE_DATE)
        contagion = twin.propagate(
            snapshot,
            initial_shocks={
                "security:svb-htm-portfolio": _ratio(htm_loss, htm),
            },
        )
        manifest = twin.manifest()
    issuer_loss = contagion.upper_loss_fraction["issuer:svb-financial-group"]
    artifact = ReplayArtifact.create(
        artifact_id="svb.markettwin.loss-envelope",
        engine=EngineName.MARKETTWIN,
        artifact_kind="temporal-network-envelope",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.REPORTED: 5, EvidenceClass.INFERRED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "append_receipt": asdict(append_receipt),
            "manifest": asdict(manifest),
            "snapshot": snapshot.model_dump(mode="json"),
            "contagion": contagion.model_dump(mode="json"),
        },
        limitations=(
            "The three-node two-edge graph includes only balance-sheet relationships directly "
            "represented by the locked facts.",
            "Loss propagation is a mechanical bounded channel, not causal failure attribution.",
        ),
    )
    return artifact, snapshot.graph_sha256, issuer_loss


def _run_shockcompiler(
    *,
    records_by_concept: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: tuple[str, ...],
) -> tuple[ReplayArtifact, float]:
    htm = records_by_concept["HeldToMaturitySecurities"]
    htm_loss = records_by_concept["HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss"]
    ratio = _ratio(htm_loss, htm)
    parameter = ShockParameter(
        parameter_id="bounded-htm-loss-realization",
        target_id="security:svb-htm-portfolio",
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
            "Bounds modeled realization between zero and filer-reported unrecognized HTM loss "
            "divided by filer-reported HTM carrying value."
        ),
        limitations=(
            "The bound omits liquidation timing, hedges, tax effects, funding support, and prices.",
        ),
    )
    program = ShockProgram(
        program_id="svb-bounded-htm-realization",
        scenario_id="svb-2023-boundary",
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=SVB_DECISION_TIME,
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
        {("security:svb-htm-portfolio", "loss_fraction"): 0.0},
        compiled.trials[-1],
    )
    artifact = ReplayArtifact.create(
        artifact_id="svb.shockcompiler.htm-bound",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-shock-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=parameter.source_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=upstream,
        payload={
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
    records_by_concept: dict[str, BitemporalRecord],
    htm_loss_ratio: float,
    upstream: str,
) -> tuple[ReplayArtifact, float, float]:
    htm_value = float(records_by_concept["HeldToMaturitySecurities"].payload["val"])
    interval_end = SVB_DECISION_TIME.replace(hour=23, minute=59, second=59)
    order = OrderSpec(
        order_id="svb-simulated-htm-liquidation",
        instrument_id="synthetic:svb-htm-usd-unit",
        side=OrderSide.SELL,
        kind=OrderKind.MARKET,
        quantity=htm_value * htm_loss_ratio,
        decision_at=SVB_DECISION_TIME,
        latency_ms=1_000,
        time_in_force_ms=21_598_000,
    )
    observation = MarketObservation(
        observation_id="svb:simulated:reference-only",
        instrument_id=order.instrument_id,
        precision=ExecutionPrecision.REFERENCE_ONLY,
        interval_start=SVB_DECISION_TIME,
        interval_end=interval_end,
        available_at=SVB_DECISION_TIME,
        reference_price=1.0,
        estimated_daily_volume=htm_value,
        evidence_class=EvidenceClass.SIMULATED,
        source_record_ids=(),
        sources=(),
        limitations=(
            "One synthetic unit equals one reported carrying-value dollar; this is not a "
            "security quote.",
            "Estimated daily volume equals the portfolio carrying value solely as a declared "
            "stress normalization.",
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
        artifact_id="svb.executionlab.normalized-envelope",
        engine=EngineName.EXECUTIONLAB,
        artifact_kind="reference-only-execution-envelope",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.SIMULATED: 1},
        source_set_historical_replay_eligible=False,
        source_record_ids=(),
        source_hashes=(),
        upstream_artifact_ids=(upstream,),
        payload={
            "order": order.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "envelope": envelope.model_dump(mode="json"),
        },
        limitations=(
            "No historical quote, trade, order-book, venue, or volume evidence is represented.",
            "The envelope is a normalized simulated capacity/cost boundary, not an executable "
            "trade.",
        ),
    )
    return artifact, envelope.fill_quantity_upper, transaction_cost_bps


def _run_capitalallocator(
    *,
    records_by_concept: dict[str, BitemporalRecord],
    htm_loss_ratio: float,
    modeled_issuer_loss: float,
    execution_capacity_usd: float,
    transaction_cost_bps: float,
    source_hashes: tuple[str, ...],
    upstream: tuple[str, ...],
) -> ReplayArtifact:
    htm = records_by_concept["HeldToMaturitySecurities"]
    htm_loss = records_by_concept["HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss"]
    source_ids = tuple(sorted((htm.record_id, htm_loss.record_id)))
    source = (htm.source,)
    total_capital = 10_000_000_000.0
    asset = AssetCandidate(
        asset_id="asset:svb-htm-model-exposure",
        label="Synthetic SVB HTM model exposure",
        expected_return_lower=-min(1.0, modeled_issuer_loss),
        expected_return_upper=0.0,
        current_weight=0.0,
        min_weight=0.0,
        max_weight=min(1.0, execution_capacity_usd / total_capital),
        capacity_usd=execution_capacity_usd,
        transaction_cost_bps=transaction_cost_bps,
        evidence_class=EvidenceClass.INFERRED,
        available_at=SVB_DECISION_TIME,
        source_record_ids=source_ids,
        sources=source,
        derivation=(
            "Return lower bound is the negative MarketTwin issuer-loss envelope; the upper bound "
            "is zero, and capacity/costs come from the simulated ExecutionLab boundary."
        ),
        limitations=(
            "This synthetic model exposure is not a listed security, forecast, or recommendation.",
        ),
    )
    scenario = RiskScenario(
        scenario_id="scenario:svb-htm-upper-bound",
        loss_fraction_by_asset={asset.asset_id: htm_loss_ratio},
        evidence_class=EvidenceClass.INFERRED,
        available_at=SVB_DECISION_TIME,
        source_record_ids=source_ids,
        sources=source,
        derivation=(
            "Scenario loss is the upper endpoint of reported unrecognized HTM loss divided by "
            "reported HTM carrying value."
        ),
        limitations=("Accounting bound; not a probability-weighted market forecast.",),
    )
    problem = AllocationProblem(
        problem_id="problem:svb-boundary-allocation",
        decision_time=SVB_DECISION_TIME,
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
        raise ValueError("SVB boundary allocation must preserve the all-cash robust solution")
    return ReplayArtifact.create(
        artifact_id="svb.capitalallocator.robust-boundary",
        engine=EngineName.CAPITALALLOCATOR,
        artifact_kind="robust-allocation-boundary",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 1, EvidenceClass.SIMULATED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=source_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=upstream,
        payload={
            "problem": problem.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        },
        limitations=(
            "The all-cash result follows declared non-positive return bounds and is not a "
            "recommendation.",
            "Execution capacity and costs are simulated, while accounting ratios are inferred "
            "from reported facts.",
        ),
    )


def _claims(
    *,
    records_by_concept: dict[str, BitemporalRecord],
    artifacts: dict[EngineName, ReplayArtifact],
    htm_loss_ratio: float,
) -> tuple[ReplayClaim, ...]:
    assets = int(records_by_concept["Assets"].payload["val"])
    deposits = int(records_by_concept["Deposits"].payload["val"])
    htm = int(records_by_concept["HeldToMaturitySecurities"].payload["val"])
    htm_loss = int(
        records_by_concept["HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss"].payload[
            "val"
        ]
    )
    return (
        ReplayClaim(
            claim_id="claim-reported-balance-sheet",
            statement=(
                f"The locked SEC filing facts report assets of ${assets:,}, deposits of "
                f"${deposits:,}, HTM securities of ${htm:,}, and HTM unrecognized loss of "
                f"${htm_loss:,} for the selected 2022 year-end frame."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(artifacts[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are filer-reported XBRL facts from the locked accession, not regulator "
                "findings."
            ),
            limitations=(
                "The pack includes a selected fact subset rather than the complete filing.",
            ),
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
                "TrialCourt rejected the retrospective attempt after retaining all six attack "
                "findings."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(artifacts[EngineName.TRIALCOURT].artifact_id,),
            boundary="Rejection is an internal method result, not external strategy review.",
            limitations=("The attempt deliberately makes no valid inferential or return claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-decision-boundary",
            statement=(
                "The normalized reference-only execution envelope and robust allocation are "
                "explicit simulations; the allocator retained the all-cash model solution."
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
            statement=(
                "The static ReplayPack contains one content-addressed artifact from each engine."
            ),
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(artifacts[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _records_by_concept(
    records: tuple[BitemporalRecord, ...],
) -> dict[str, BitemporalRecord]:
    return {str(record.payload["concept"]): record for record in records}


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


def _input_lock_payload(lock: SVBInputLock) -> dict[str, Any]:
    return lock.model_dump(mode="json", exclude={"lock_sha256"})


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
