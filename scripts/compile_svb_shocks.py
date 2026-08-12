#!/usr/bin/env python3
"""Compile four evidence-distinct SVB shock modes from stored SEC facts and explicit simulations."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from finreplay.contracts import EvidenceClass, ScenarioMode
from finreplay.engines import (
    ShockCompiler,
    ShockOperation,
    ShockParameter,
    ShockProgram,
    TimeVault,
)

DECISION = datetime(2023, 3, 8, 18, tzinfo=UTC)
BALANCE_DATE = datetime(2022, 12, 31, tzinfo=UTC)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timevault", type=Path, default=Path("data/silver/timevault.duckdb"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/evidence/svb-shock-programs.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        records = vault.records_as_of(
            DECISION,
            valid_at=BALANCE_DATE,
            source_ids=["sec.xbrl.companyfacts"],
        )
    concepts = {
        str(record.payload.get("concept")): record
        for record in records
        if record.payload.get("frame") == "CY2022Q4I"
    }
    required = {
        "HeldToMaturitySecurities",
        "HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss",
        "AvailableForSaleSecuritiesDebtSecurities",
        "AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedLossBeforeTax",
    }
    missing = required - set(concepts)
    if missing:
        raise SystemExit(f"missing required SEC concepts: {sorted(missing)}")
    htm = concepts["HeldToMaturitySecurities"]
    htm_loss = concepts["HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss"]
    afs = concepts["AvailableForSaleSecuritiesDebtSecurities"]
    afs_loss = concepts[
        "AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedLossBeforeTax"
    ]
    htm_ratio = float(htm_loss.payload["val"]) / float(htm.payload["val"])
    afs_ratio = float(afs_loss.payload["val"]) / float(afs.payload["val"])
    observed_parameters = (
        _reported_parameter(
            parameter_id="reported-htm-unrecognized-loss-ratio",
            target_id="security:svb-htm-portfolio",
            value=htm_ratio,
            numerator=htm_loss,
            denominator=htm,
        ),
        _reported_parameter(
            parameter_id="reported-afs-gross-unrealized-loss-ratio",
            target_id="security:svb-afs-portfolio",
            value=afs_ratio,
            numerator=afs_loss,
            denominator=afs,
        ),
    )
    programs = (
        _program(
            "svb-observed-securities-loss-ratios",
            ScenarioMode.OBSERVED_RECONSTRUCTION,
            observed_parameters,
            "Reconstruct two ratios directly reported in the 2022 SVB 10-K.",
        ),
        _program(
            "svb-bounded-htm-realization",
            ScenarioMode.BOUNDED_RECONSTRUCTION,
            (
                ShockParameter(
                    parameter_id="bounded-htm-loss-realization",
                    target_id="security:svb-htm-portfolio",
                    variable="loss_fraction",
                    unit="fraction",
                    operation=ShockOperation.SET,
                    lower=0.0,
                    upper=htm_ratio,
                    grid_points=2,
                    evidence_class=EvidenceClass.INFERRED,
                    source_record_ids=(htm_loss.record_id, htm.record_id),
                    sources=(htm_loss.source, htm.source),
                    derivation=(
                        "Bounds realized loss between zero and the reported unrecognized holding "
                        "loss divided by reported held-to-maturity carrying value."
                    ),
                    limitations=(
                        "The interval does not forecast liquidation timing, hedges, funding "
                        "support, "
                        "tax effects, or security-level price paths.",
                    ),
                ),
            ),
            "Bound possible loss realization without assigning unsupported midpoint precision.",
        ),
        _program(
            "svb-counterfactual-deposit-run",
            ScenarioMode.COUNTERFACTUAL,
            (
                _simulated_parameter(
                    parameter_id="counterfactual-deposit-run",
                    target_id="issuer:svb-financial-group",
                    variable="deposit_run_fraction",
                    lower=0.25,
                    upper=0.25,
                    grid_points=1,
                    derivation=(
                        "Explicit researcher-selected 25 percent deposit-run counterfactual."
                    ),
                ),
            ),
            "Evaluate one explicit counterfactual, not an observed historical outcome.",
        ),
        _program(
            "svb-adversarial-funding-duration-grid",
            ScenarioMode.ADVERSARIAL,
            (
                _simulated_parameter(
                    parameter_id="adversarial-deposit-run",
                    target_id="issuer:svb-financial-group",
                    variable="deposit_run_fraction",
                    lower=0.0,
                    upper=0.75,
                    grid_points=4,
                    derivation=(
                        "Research stress grid over deposit-run fractions from zero to 75 percent."
                    ),
                ),
                _simulated_parameter(
                    parameter_id="adversarial-htm-loss",
                    target_id="security:svb-htm-portfolio",
                    variable="loss_fraction",
                    lower=0.0,
                    upper=0.25,
                    grid_points=3,
                    derivation=(
                        "Research stress grid over HTM loss fractions from zero to 25 percent."
                    ),
                ),
            ),
            "Search a finite 12-vector adversarial funding-duration surface.",
        ),
    )
    compiler = ShockCompiler(max_trials=100)
    compiled = tuple(compiler.compile(program) for program in programs)
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "Observed programs preserve SEC source provenance. Bounded values are inferred "
            "intervals. Counterfactual and adversarial values are explicitly simulated and are "
            "not historical outcomes, forecasts, or investment performance."
        ),
        "decision_time": DECISION.isoformat(),
        "source_record_ids": sorted(
            {
                record_id
                for program in programs
                for item in program.parameters
                for record_id in item.source_record_ids
            }
        ),
        "programs": [program.model_dump(mode="json") for program in programs],
        "compiled": [result.model_dump(mode="json") for result in compiled],
        "mode_trial_counts": {
            result.mode.value: len(result.trials) for result in compiled
        },
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    payload["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    print(
        " ".join(
            f"{result.mode.value}={len(result.trials)}" for result in compiled
        )
        + f" receipt={args.output}"
    )


def _reported_parameter(
    *,
    parameter_id: str,
    target_id: str,
    value: float,
    numerator: Any,
    denominator: Any,
) -> ShockParameter:
    return ShockParameter(
        parameter_id=parameter_id,
        target_id=target_id,
        variable="loss_fraction",
        unit="fraction",
        operation=ShockOperation.SET,
        lower=value,
        upper=value,
        grid_points=1,
        evidence_class=EvidenceClass.REPORTED,
        source_record_ids=(numerator.record_id, denominator.record_id),
        sources=(numerator.source, denominator.source),
        derivation=(
            "Exact arithmetic ratio of two filer-reported SEC XBRL facts from the same accepted "
            "2022 annual filing."
        ),
        limitations=(
            "A reported balance-sheet ratio is descriptive, not realized loss or a causal shock.",
        ),
    )


def _simulated_parameter(
    *,
    parameter_id: str,
    target_id: str,
    variable: str,
    lower: float,
    upper: float,
    grid_points: int,
    derivation: str,
) -> ShockParameter:
    return ShockParameter(
        parameter_id=parameter_id,
        target_id=target_id,
        variable=variable,
        unit="fraction",
        operation=ShockOperation.SET,
        lower=lower,
        upper=upper,
        grid_points=grid_points,
        evidence_class=EvidenceClass.SIMULATED,
        source_record_ids=(),
        sources=(),
        derivation=derivation,
        limitations=(
            "Researcher-selected simulation; not an observed historical value or forecast.",
        ),
    )


def _program(
    program_id: str,
    mode: ScenarioMode,
    parameters: tuple[ShockParameter, ...],
    hypothesis: str,
) -> ShockProgram:
    return ShockProgram(
        program_id=program_id,
        scenario_id="svb-2023-boundary",
        mode=mode,
        decision_time=DECISION,
        parameters=parameters,
        hypothesis=hypothesis,
        global_limitations=(
            "This program isolates shock inputs; downstream dynamics require separate models.",
        ),
    )


if __name__ == "__main__":
    main()
