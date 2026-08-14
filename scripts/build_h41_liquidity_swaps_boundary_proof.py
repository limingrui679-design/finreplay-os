#!/usr/bin/env python3
"""Build and verify the eight-gate proof for the March 2020 H.4.1 boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from finreplay.engines import CompiledReplayPack, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    load_h41_liquidity_swaps_boundary_input_lock,
    seal_scenario_proof,
    verify_scenario_proof,
)

REQUIRED_ASSERTIONS = (
    "all_artifacts_reproduced",
    "auxiliary_and_market_fields_are_not_used",
    "byte_identical_directory_rebuilds",
    "byte_identical_zip_rebuilds",
    "compiled_pack_matches_rebuild",
    "cross_engine_trace_is_stable",
    "declared_range_reuses_only_one_known_increase",
    "distinct_locked_inputs_equal_two",
    "fixed_range_is_not_changed_after_event",
    "four_official_html_ascii_hashes_are_bound",
    "future_event_excluded_from_bound_construction",
    "historical_source_sets_eligible",
    "idempotent_supporting_receipt_is_bound",
    "inside_result_is_not_promoted_to_forecast_success",
    "locked_release_pair_is_exact",
    "measurement_boundary_is_explicit",
    "naive_baseline_is_march25_persistence",
    "no_probability_is_assigned",
    "official_input_release_times_are_exact",
    "only_relevant_engines_present",
    "paired_html_ascii_crosscheck_is_explicit",
    "post_decision_event_is_disjoint",
    "post_decision_event_is_exact_h41_fact",
    "post_decision_event_timing_is_conservative_and_later",
    "post_event_lower_distance_equals_142493_million",
    "post_event_upper_headroom_equals_63513_million",
    "reported_post_event_balance_is_inside_declared_range",
    "simulation_remains_visible",
    "trialcourt_rejects_retrospective_attempt",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--input-lock", type=Path, required=True)
    parser.add_argument("--event-lock", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--rebuild-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repository_root.expanduser().resolve()
    input_path = _resolve(root, args.input_lock)
    event_path = _resolve(root, args.event_lock)
    pack_path = _resolve(root, args.pack)
    rebuild_path = _resolve(root, args.rebuild_receipt)
    output_path = _resolve_output(root, args.output)
    build_script = root / "scripts/build_h41_liquidity_swaps_boundary_replaypack.py"
    verify_script = root / "scripts/verify_h41_liquidity_swaps_boundary_replaypack.py"

    lock = load_h41_liquidity_swaps_boundary_input_lock(input_path)
    event_lock = OfficialEventLock.model_validate_json(
        event_path.read_text(encoding="utf-8")
    )
    if (
        event_lock.scenario_id,
        event_lock.scenario_version,
        event_lock.decision_time,
    ) != (lock.scenario_id, lock.scenario_version, lock.decision_time):
        raise SystemExit("event-lock identity does not match H.4.1 input lock")
    receipt = ReplayStudio().verify(pack_path)
    compiled = CompiledReplayPack.model_validate_json(
        (pack_path / "report.json").read_text(encoding="utf-8")
    )
    if (compiled.spec.scenario_id, compiled.spec.scenario_version, compiled.spec.replay_id) != (
        lock.scenario_id,
        lock.scenario_version,
        lock.replay_id,
    ):
        raise SystemExit("ReplayPack identity does not match H.4.1 input lock")
    prefix = lock.artifact_prefix
    record_ids = tuple(sorted(record.record_id for record in lock.records))
    event_record_ids = tuple(sorted(record.record_id for record in event_lock.records))
    range_artifact = f"{prefix}.shockcompiler.balance-range"
    trial_artifact = f"{prefix}.trialcourt.retrospective-gate"
    proof = seal_scenario_proof(
        {
            "schema_version": "1.1.0",
            "scenario_id": lock.scenario_id,
            "scenario_version": lock.scenario_version,
            "replay_id": lock.replay_id,
            "pack_directory": _relative(root, pack_path),
            "expected_pack_sha256": compiled.pack_sha256,
            "expected_trace_id": compiled.trace_id,
            "expected_input_manifest_sha256": compiled.input_manifest_sha256,
            "official_adapter_inventory": (
                "verification/supporting/fed-h41-liquidity-swaps/latest-summary.json"
            ),
            "input_locks": [
                {
                    "path": _relative(root, input_path),
                    "sha256": _file_hash(input_path),
                    "lock_sha256": lock.lock_sha256,
                    "record_ids": record_ids,
                }
            ],
            "event_locks": [
                {
                    "path": _relative(root, event_path),
                    "sha256": _file_hash(event_path),
                    "lock_sha256": event_lock.lock_sha256,
                    "record_ids": event_record_ids,
                }
            ],
            "build_script": {
                "path": _relative(root, build_script),
                "sha256": _file_hash(build_script),
            },
            "verify_script": {
                "path": _relative(root, verify_script),
                "sha256": _file_hash(verify_script),
            },
            "rebuild_receipt": {
                "path": _relative(root, rebuild_path),
                "sha256": _file_hash(rebuild_path),
            },
            "timing_record_ids": record_ids,
            "input_labels": {
                "observed_record_ids": [],
                "reported_record_ids": record_ids,
                "extracted_artifact_ids": [f"{prefix}.replaystudio.render"],
                "inferred_artifact_ids": sorted((range_artifact, trial_artifact)),
                "bounded_artifact_ids": [range_artifact],
                "simulated_artifact_ids": [trial_artifact],
                "absence_reasons": {
                    "observed": (
                        "The lock contains aggregate Wednesday liquidity-swap balances "
                        "reported in archived Federal Reserve releases, not observations of "
                        "central-bank counterparties, institutions, transactions, current-"
                        "market exposure, losses, P&L, policy effectiveness, or user activity. "
                        "The April 1 balance exists only in the disjoint event lock."
                    )
                },
            },
            "naive_baselines": [
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": (
                        "/naive_baseline/"
                        "next_wednesday_liquidity_swaps_outstanding_million_dollars"
                    ),
                    "expected_value": 206_051,
                    "description": (
                        "The decision-time baseline persists the March 25 Wednesday "
                        "liquidity-swap balance of $206,051 million."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/probability_assigned",
                    "expected_value": False,
                    "description": (
                        "The persistence-or-one-known-increase endpoints carry no probability, "
                        "confidence, or coverage claim."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/weekly_average_used",
                    "expected_value": False,
                    "description": (
                        "Weekly-average fields remain source context and set neither endpoint."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/year_change_used",
                    "expected_value": False,
                    "description": (
                        "Year-ago change fields remain source context and set neither endpoint."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": (
                        "/bound_construction/current_market_revaluation_performed"
                    ),
                    "expected_value": False,
                    "description": (
                        "No current-market revaluation is inferred from the H.4.1 balance."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/future_event_used",
                    "expected_value": False,
                    "description": (
                        "The later April 1 balance never enters range construction."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/wednesday_balance_only",
                    "expected_value": True,
                    "description": (
                        "Only the two reported Wednesday aggregate balances define endpoints."
                    ),
                },
            ],
            "deliberate_failure_modes": [
                {
                    "artifact_id": trial_artifact,
                    "payload_pointer": "/decision/disposition",
                    "expected_value": "reject",
                    "description": (
                        "TrialCourt must reject the retrospective one-increase attempt rather "
                        "than promote it as a validated forecast."
                    ),
                },
                {
                    "artifact_id": trial_artifact,
                    "payload_pointer": "/manifest/rejected_decisions",
                    "expected_value": 1,
                    "description": "The immutable trial ledger retains one rejected decision.",
                },
            ],
            "required_rebuild_assertions": REQUIRED_ASSERTIONS,
            "claim_boundary": (
                "This proof counts one internally reproduced March 2020 Federal Reserve H.4.1 "
                "liquidity-swap balance boundary because two pre-decision Wednesday aggregate "
                "balances, four exact official HTML/ASCII input-response hashes, an idempotent "
                "six-response supporting receipt, paired-format cross-checks, exact official "
                "stated March release times, immutable locks, separated truth labels, a March "
                "25 persistence baseline, a no-probability persistence-or-one-known-increase "
                "program, deliberate TrialCourt failure, deterministic outputs, limitations, "
                "and a clean-checkout double rebuild all verify. The April 1 balance of "
                "$348,544 million is excluded from ReplayPack inputs, conservatively eligible "
                "only at the following New York midnight because its archived release pair "
                "states no exact time, and used only for a labelled post-event check. It lies "
                "$142,493 million above the fixed $206,051-million lower endpoint and $63,513 "
                "million below the fixed $412,057-million upper endpoint. That inside result "
                "does not become forecast success and does not alter the endpoints after the "
                "fact. Weekly averages and year-ago changes remain metadata and set no "
                "endpoint. The release's exchange-rate convention is preserved and is not "
                "current-market revaluation. This proof does not establish a Federal Reserve "
                "forecast, calibrated coverage, institution or transaction behavior, current-"
                "market exposure, counterparty loss, P&L, policy effectiveness, systemic-"
                "stress causality, investment performance, external validation, deployment, "
                "or user impact."
            ),
        }
    )
    content = (
        json.dumps(
            proof.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()
    if output_path.exists() and output_path.read_bytes() != content:
        raise SystemExit("scenario proof destination contains different bytes")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)
    verified = verify_scenario_proof(output_path, repository_root=root)
    if verified.pack_sha256 != receipt.pack_sha256:
        raise SystemExit("new proof does not bind the verified pack receipt")
    print(
        f"verified=true scenario={verified.scenario_id} "
        f"proof_sha256={verified.proof_sha256} "
        f"pack_sha256={verified.pack_sha256} output={output_path}"
    )


def _resolve(root: Path, value: Path) -> Path:
    path = value if value.is_absolute() else root / value
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(root) or not resolved.exists():
        raise SystemExit(f"input path must exist inside repository: {value}")
    return resolved


def _resolve_output(root: Path, value: Path) -> Path:
    path = value if value.is_absolute() else root / value
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(root):
        raise SystemExit(f"output path must remain inside repository: {value}")
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
