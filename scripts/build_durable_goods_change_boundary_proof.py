#!/usr/bin/env python3
"""Build and verify the eight-gate proof for the Census M3 durable-goods boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from finreplay.engines import CompiledReplayPack, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    load_durable_goods_change_boundary_input_lock,
    seal_scenario_proof,
    verify_scenario_proof,
)

REQUIRED_ASSERTIONS = (
    "all_artifacts_reproduced",
    "byte_identical_directory_rebuilds",
    "byte_identical_zip_rebuilds",
    "compiled_pack_matches_rebuild",
    "cross_engine_trace_is_stable",
    "declared_range_reuses_only_known_initial_increase",
    "distinct_locked_inputs_equal_two",
    "future_event_excluded_from_bound_construction",
    "historical_source_sets_eligible",
    "locked_initial_release_pair_is_exact",
    "naive_baseline_is_february_initial_change_persistence",
    "no_probability_is_assigned",
    "official_confidence_interval_is_not_used",
    "only_relevant_engines_present",
    "post_decision_event_is_disjoint",
    "post_decision_event_is_exact_census_m3_fact",
    "post_decision_event_timing_is_later",
    "post_decision_revisions_remain_later_snapshot_only",
    "post_event_lower_bound_breach_equals_1560_basis_points",
    "release_time_and_current_byte_boundary_are_explicit",
    "reported_post_event_change_is_below_declared_range",
    "simulation_remains_visible",
    "trialcourt_rejects_retrospective_attempt",
    "two_pdf_hashes_are_bound",
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
    build_script = root / "scripts/build_durable_goods_change_boundary_replaypack.py"
    verify_script = root / "scripts/verify_durable_goods_change_boundary_replaypack.py"

    lock = load_durable_goods_change_boundary_input_lock(input_path)
    event_lock = OfficialEventLock.model_validate_json(event_path.read_text(encoding="utf-8"))
    receipt = ReplayStudio().verify(pack_path)
    compiled = CompiledReplayPack.model_validate_json(
        (pack_path / "report.json").read_text(encoding="utf-8")
    )
    if (compiled.spec.scenario_id, compiled.spec.scenario_version, compiled.spec.replay_id) != (
        lock.scenario_id,
        lock.scenario_version,
        lock.replay_id,
    ):
        raise SystemExit("ReplayPack identity does not match Census M3 durable-goods input lock")
    prefix = lock.artifact_prefix
    record_ids = tuple(sorted(record.record_id for record in lock.records))
    event_record_ids = tuple(sorted(record.record_id for record in event_lock.records))
    range_artifact = f"{prefix}.shockcompiler.new-orders-change-range"
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
                "verification/supporting/census-m3-durable-goods/latest-summary.json"
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
                        "The lock contains aggregate monthly changes reported in archived Census "
                        "M3 releases, not firm, product, contract, transaction, price-adjusted, "
                        "causal, or future-event observations. The March change is present only "
                        "in the disjoint event lock."
                    )
                },
            },
            "naive_baselines": [
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": (
                        "/naive_baseline/next_total_durable_goods_new_orders_change_basis_points"
                    ),
                    "expected_value": 120,
                    "description": (
                        "The decision-time baseline persists the February first-report total "
                        "durable-goods new-orders change of 120 basis points."
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
                    "payload_pointer": ("/bound_construction/official_confidence_interval_used"),
                    "expected_value": False,
                    "description": (
                        "No confidence interval is represented as the FinReplay range; M3 is not "
                        "a probability sample, so report-level sampling intervals are unavailable."
                    ),
                },
            ],
            "deliberate_failure_modes": [
                {
                    "artifact_id": trial_artifact,
                    "payload_pointer": "/decision/disposition",
                    "expected_value": "reject",
                    "description": (
                        "TrialCourt must reject the retrospective one-initial-increase attempt "
                        "rather than promote it as a validated forecast."
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
                "This proof counts one internally reproduced March 2020 Census M3 durable-goods "
                "boundary because two pre-decision first-report total new-orders monthly changes, "
                "two exact current official PDF hashes, exact 8:30 a.m. America/New_York report "
                "timing, a separately locked April 24 March event, immutable locks, separated "
                "truth labels, an explicit February persistence baseline, a no-probability "
                "persistence-or-one-known-increase program, deliberate TrialCourt failure, "
                "deterministic outputs, limitations, and a clean-worktree double rebuild all "
                "verify. Every selected PDF has post-release modification metadata, so current "
                "hashes are not represented as release-time byte identity. The March change is "
                "excluded from ReplayPack inputs and used only for a labelled post-event check "
                "that preserves its 1,560-basis-point lower-bound breach rather than widening the "
                "range. The April report's January and February snapshot values never overwrite "
                "the first-report inputs. M3 is not a probability sample, and the reported series "
                "is seasonally adjusted but not adjusted for price changes. The report's COVID-19 "
                "text concerns publication standards and does not establish causality or "
                "unaffected measurement. This proof does not establish forecast skill, calibrated "
                "coverage, statistical significance, price-adjusted output, manufacturing, "
                "inflation, pandemic, policy, sector, regional, or firm causality, external "
                "validation, deployment, investment performance, or user impact."
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
