#!/usr/bin/env python3
"""Build the deterministic seven-engine ReplayStudio fixture pack."""

from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from finreplay.contracts import ArtifactStatus, EvidenceClass, ScenarioMode
from finreplay.engines import (
    EngineName,
    ReplayArtifact,
    ReplayClaim,
    ReplayPackSpec,
    ReplayStudio,
)

DECISION = datetime(2023, 3, 8, 15, tzinfo=UTC)
CREATED = datetime(2023, 3, 8, 16, tzinfo=UTC)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/replaypacks/replaystudio-golden"),
    )
    parser.add_argument("--archive", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    studio = ReplayStudio()
    result = studio.build(build_spec(), args.output)
    archive = studio.archive(result.root, args.archive) if args.archive else None
    print(
        f"verified=true idempotent={str(result.idempotent).lower()} "
        f"engines=7 claims=5 trace_id={result.receipt.trace_id} "
        f"receipt_sha256={result.receipt.receipt_sha256} root={result.root}"
    )
    if archive:
        print(f"archive={archive}")


def build_spec() -> ReplayPackSpec:
    timevault = _artifact(
        EngineName.TIMEVAULT,
        EvidenceClass.OBSERVED,
        source_record_id="fixture:timevault:record",
    )
    trialcourt = _artifact(
        EngineName.TRIALCOURT,
        EvidenceClass.REPORTED,
        upstream=(timevault.artifact_id,),
        source_record_id="fixture:trialcourt:preregistration",
    )
    markettwin = _artifact(
        EngineName.MARKETTWIN,
        EvidenceClass.EXTRACTED,
        upstream=(timevault.artifact_id,),
        source_record_id="fixture:markettwin:edge",
    )
    shockcompiler = _artifact(
        EngineName.SHOCKCOMPILER,
        EvidenceClass.INFERRED,
        upstream=(markettwin.artifact_id, trialcourt.artifact_id),
    )
    executionlab = _artifact(
        EngineName.EXECUTIONLAB,
        EvidenceClass.SIMULATED,
        upstream=(shockcompiler.artifact_id,),
    )
    capitalallocator = _artifact(
        EngineName.CAPITALALLOCATOR,
        EvidenceClass.SIMULATED,
        upstream=(executionlab.artifact_id, trialcourt.artifact_id),
    )
    replaystudio = _artifact(
        EngineName.REPLAYSTUDIO,
        EvidenceClass.EXTRACTED,
        upstream=(capitalallocator.artifact_id, shockcompiler.artifact_id),
        source_record_id="fixture:replaystudio:render-contract",
    )
    artifacts = (
        replaystudio,
        capitalallocator,
        executionlab,
        shockcompiler,
        markettwin,
        trialcourt,
        timevault,
    )
    claims = (
        _claim(
            "claim-observed",
            "The TimeVault fixture record was present in the compiled input graph.",
            EvidenceClass.OBSERVED,
            timevault.artifact_id,
        ),
        _claim(
            "claim-reported",
            "The fixture preregistration was retained as a reported TrialCourt input.",
            EvidenceClass.REPORTED,
            trialcourt.artifact_id,
        ),
        _claim(
            "claim-extracted",
            "Fixture graph and render-contract fields were extracted into typed artifacts.",
            EvidenceClass.EXTRACTED,
            markettwin.artifact_id,
        ),
        _claim(
            "claim-inferred",
            "The fixture shock program is explicitly labelled as an inferred model output.",
            EvidenceClass.INFERRED,
            shockcompiler.artifact_id,
        ),
        _claim(
            "claim-simulated",
            "Execution and allocation values in this golden pack are simulated fixtures.",
            EvidenceClass.SIMULATED,
            executionlab.artifact_id,
        ),
    )
    return ReplayPackSpec(
        replay_id="replaystudio-seven-engine-golden",
        scenario_id="replaystudio-render-boundary",
        scenario_version="1.0.0",
        title="ReplayStudio seven-engine deterministic fixture",
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=DECISION,
        created_at=CREATED,
        code_commit="uncommitted",
        status=ArtifactStatus.FIXTURE_VALIDATED,
        artifacts=artifacts,
        claims=claims,
        require_all_engines=True,
        distinct_input_records=4,
        derived_records=7,
        compressed_input_bytes=0,
        elapsed_seconds=0.0,
        claim_boundary=(
            "This golden pack proves deterministic packaging, traceability, static rendering, "
            "and tamper detection over synthetic fixture artifacts only. It does not prove an "
            "historical event reconstruction, source authenticity, external review, production "
            "deployment, user impact, or realized financial performance."
        ),
        limitations=(
            "Every engine artifact is a compact internal fixture rather than an engine-run result.",
            "Fixture source identifiers are not official publisher records.",
            "A zero elapsed time is a deterministic fixture field, not a speed benchmark.",
        ),
    )


def _artifact(
    engine: EngineName,
    evidence_class: EvidenceClass,
    *,
    upstream: tuple[str, ...] = (),
    source_record_id: str | None = None,
) -> ReplayArtifact:
    source_ids = (source_record_id,) if source_record_id else ()
    source_hashes = (
        (hashlib.sha256(source_record_id.encode()).hexdigest(),) if source_record_id else ()
    )
    return ReplayArtifact.create(
        artifact_id=f"artifact-{engine.value}",
        engine=engine,
        artifact_kind="golden-fixture-result",
        status=ArtifactStatus.FIXTURE_VALIDATED,
        evidence_counts={evidence_class: 1},
        source_set_historical_replay_eligible=False,
        source_record_ids=source_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=upstream,
        payload={
            "fixture": True,
            "engine": engine.value,
            "result": f"deterministic-{engine.value}-boundary",
        },
        limitations=(
            "Internal deterministic fixture; not a live engine run or external validation.",
        ),
    )


def _claim(
    claim_id: str,
    statement: str,
    evidence_class: EvidenceClass,
    support_artifact_id: str,
) -> ReplayClaim:
    return ReplayClaim(
        claim_id=claim_id,
        statement=statement,
        evidence_class=evidence_class,
        support_artifact_ids=(support_artifact_id,),
        boundary=(
            "The label describes only the internal fixture relationship represented by this pack."
        ),
        limitations=("This is not an empirical claim about a historical financial event.",),
    )


if __name__ == "__main__":
    main()
