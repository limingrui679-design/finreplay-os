#!/usr/bin/env python3
"""Build and verify the eight-gate proof for the GDP revision ReplayPack."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from finreplay.engines import CompiledReplayPack, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    load_macro_revision_input_lock,
    seal_scenario_proof,
    verify_scenario_proof,
)

REQUIRED_ASSERTIONS = (
    "all_artifacts_reproduced",
    "byte_identical_directory_rebuilds",
    "byte_identical_zip_rebuilds",
    "compiled_pack_matches_rebuild",
    "cross_engine_trace_is_stable",
    "distinct_locked_inputs_equal_four",
    "future_event_excluded_from_bound_construction",
    "historical_source_sets_eligible",
    "naive_baseline_is_zero_revision",
    "no_probability_is_assigned",
    "only_relevant_engines_present",
    "post_decision_event_is_disjoint",
    "post_decision_event_timing_is_later",
    "reported_post_event_revision_inside_declared_envelope",
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
    build_script = root / "scripts/build_macro_revision_replaypack.py"
    verify_script = root / "scripts/verify_macro_revision_replaypack.py"

    lock = load_macro_revision_input_lock(input_path)
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
        raise SystemExit("ReplayPack identity does not match macro revision input lock")
    prefix = lock.artifact_prefix
    record_ids = tuple(sorted(record.record_id for record in lock.records))
    event_record_ids = tuple(sorted(record.record_id for record in event_lock.records))
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
            "official_adapter_inventory": "verification/supporting/alfred/latest-summary.json",
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
                "inferred_artifact_ids": sorted(
                    (
                        f"{prefix}.shockcompiler.revision-bound",
                        f"{prefix}.trialcourt.retrospective-gate",
                    )
                ),
                "bounded_artifact_ids": [f"{prefix}.shockcompiler.revision-bound"],
                "simulated_artifact_ids": [f"{prefix}.trialcourt.retrospective-gate"],
                "absence_reasons": {
                    "observed": (
                        "GDP values are official reported estimates rather than direct physical "
                        "observations of the underlying economy. The later official estimate is "
                        "kept only in the disjoint post-decision event lock."
                    )
                },
            },
            "naive_baselines": [
                {
                    "artifact_id": f"{prefix}.shockcompiler.revision-bound",
                    "payload_pointer": "/naive_baseline/revision_billions",
                    "expected_value": 0.0,
                    "description": (
                        "The explicit historical-decision baseline assumes no revision to the Q4 "
                        "advance estimate."
                    ),
                },
                {
                    "artifact_id": f"{prefix}.shockcompiler.revision-bound",
                    "payload_pointer": "/bound_construction/probability_assigned",
                    "expected_value": False,
                    "description": (
                        "The two inferred endpoints deliberately carry no probability or coverage "
                        "claim."
                    ),
                },
            ],
            "deliberate_failure_modes": [
                {
                    "artifact_id": f"{prefix}.trialcourt.retrospective-gate",
                    "payload_pointer": "/decision/disposition",
                    "expected_value": "reject",
                    "description": (
                        "TrialCourt must reject the retrospective single-quarter attempt rather "
                        "than promote it as a validated forecast."
                    ),
                },
                {
                    "artifact_id": f"{prefix}.trialcourt.retrospective-gate",
                    "payload_pointer": "/manifest/rejected_decisions",
                    "expected_value": 1,
                    "description": (
                        "The immutable trial ledger must retain exactly one rejected decision."
                    ),
                },
            ],
            "required_rebuild_assertions": REQUIRED_ASSERTIONS,
            "claim_boundary": (
                "This proof counts one internally reproduced macro vintage boundary scenario "
                "because four official pre-decision ALFRED facts, deterministic conservative "
                "knowledge timing, a separately locked post-decision official estimate, an "
                "immutable input lock, separated truth labels, an explicit zero-revision naive "
                "baseline, a no-probability bounded revision program, deliberate TrialCourt "
                "failure, deterministic outputs, limitations, and a clean-worktree double rebuild "
                "all verify. The later estimate is excluded from ReplayPack inputs and used only "
                "for a labelled post-event coverage check. This does not establish forecasting "
                "skill, calibrated coverage, causal correctness, external validation, deployment, "
                "policy value, investment performance, or user impact."
            ),
        }
    )
    content = (
        json.dumps(proof.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2)
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
        f"verified=true scenario={verified.scenario_id} proof_sha256={verified.proof_sha256} "
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
