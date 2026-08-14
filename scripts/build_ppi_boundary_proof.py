#!/usr/bin/env python3
"""Build and verify the eight-gate proof for the March 2020 BLS PPI boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from finreplay.engines import CompiledReplayPack, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    load_ppi_boundary_input_lock,
    seal_scenario_proof,
    verify_scenario_proof,
)

REQUIRED_ASSERTIONS = (
    "adjacent_february_value_is_unchanged",
    "all_artifacts_reproduced",
    "byte_identical_directory_rebuilds",
    "byte_identical_zip_rebuilds",
    "compiled_pack_matches_rebuild",
    "covid_methodology_text_is_retained_without_causal_claim",
    "cross_engine_trace_is_stable",
    "declared_range_reuses_only_one_known_increase",
    "distinct_locked_inputs_equal_two",
    "four_official_html_pdf_hashes_are_bound",
    "future_event_excluded_from_bound_construction",
    "historical_source_sets_eligible",
    "locked_release_pair_is_exact",
    "naive_baseline_is_march_change_persistence",
    "no_probability_is_assigned",
    "only_relevant_engines_present",
    "paired_html_pdf_crosscheck_is_explicit",
    "post_decision_event_is_disjoint",
    "post_decision_event_is_exact_ppi_fact",
    "post_decision_event_timing_is_later",
    "post_decision_prior_march_value_remains_unchanged",
    "post_event_lower_bound_breach_equals_110_basis_points",
    "ppi_measurement_and_revision_boundaries_are_explicit",
    "reported_post_event_change_is_below_declared_range",
    "simulation_remains_visible",
    "source_auxiliary_measures_are_not_used",
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
    build_script = root / "scripts/build_ppi_boundary_replaypack.py"
    verify_script = root / "scripts/verify_ppi_boundary_replaypack.py"

    lock = load_ppi_boundary_input_lock(input_path)
    event_lock = OfficialEventLock.model_validate_json(
        event_path.read_text(encoding="utf-8")
    )
    if (
        event_lock.scenario_id,
        event_lock.scenario_version,
        event_lock.decision_time,
    ) != (lock.scenario_id, lock.scenario_version, lock.decision_time):
        raise SystemExit("event-lock identity does not match BLS PPI input lock")
    receipt = ReplayStudio().verify(pack_path)
    compiled = CompiledReplayPack.model_validate_json(
        (pack_path / "report.json").read_text(encoding="utf-8")
    )
    if (compiled.spec.scenario_id, compiled.spec.scenario_version, compiled.spec.replay_id) != (
        lock.scenario_id,
        lock.scenario_version,
        lock.replay_id,
    ):
        raise SystemExit("ReplayPack identity does not match BLS PPI input lock")
    prefix = lock.artifact_prefix
    record_ids = tuple(sorted(record.record_id for record in lock.records))
    event_record_ids = tuple(sorted(record.record_id for record in event_lock.records))
    range_artifact = f"{prefix}.shockcompiler.final-demand-change-range"
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
            "official_adapter_inventory": "verification/supporting/bls-ppi/latest-summary.json",
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
                        "The lock contains aggregate seller-price changes reported in archived "
                        "BLS releases, not producer, establishment, product, transaction, "
                        "quantity, revenue, profit, consumer-price, causal, or future-event "
                        "observations. The April change exists only in the disjoint event lock."
                    )
                },
            },
            "naive_baselines": [
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": (
                        "/naive_baseline/next_final_demand_monthly_change_basis_points"
                    ),
                    "expected_value": -20,
                    "description": (
                        "The decision-time baseline persists the March final-demand monthly "
                        "change of -20 basis points."
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
                    "payload_pointer": (
                        "/bound_construction/source_auxiliary_measures_used"
                    ),
                    "expected_value": False,
                    "description": (
                        "Unadjusted indexes, 12-month changes, and release methodology text "
                        "remain source metadata and set neither endpoint."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/original_release_values_only",
                    "expected_value": True,
                    "description": (
                        "Both numeric inputs are headline values in archived BLS releases "
                        "available by the April 9 decision boundary."
                    ),
                },
                {
                    "artifact_id": range_artifact,
                    "payload_pointer": "/bound_construction/future_event_used",
                    "expected_value": False,
                    "description": (
                        "The later -130-basis-point April change never enters range construction."
                    ),
                },
            ],
            "deliberate_failure_modes": [
                {
                    "artifact_id": trial_artifact,
                    "payload_pointer": "/decision/disposition",
                    "expected_value": "reject",
                    "description": (
                        "TrialCourt must reject the retrospective one-change attempt rather "
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
                "This proof counts one internally reproduced March 2020 BLS PPI boundary "
                "because two pre-decision final-demand release records, four exact official "
                "HTML/PDF hashes, exact 8:30 a.m. EDT embargo timing, paired-format and adjacent-"
                "release cross-checks, a separately locked May 13 April event, immutable locks, "
                "separated truth labels, an explicit March-change persistence baseline, a no-"
                "probability persistence-or-one-known-increase program, deliberate TrialCourt "
                "failure, deterministic outputs, limitations, and a clean-checkout double "
                "rebuild all verify. The April change of -130 basis points is excluded from "
                "ReplayPack inputs and used only for a labelled post-event check that preserves "
                "its 110-basis-point lower-bound breach rather than widening the -20-to-20 "
                "basis-point range. The March release preserves February at -60 basis points, "
                "and the May release preserves March at -20 basis points; those zero revisions "
                "do not convert the range into a forecast. PPI measures average changes in "
                "prices received by domestic producers from the seller perspective and is "
                "subject to revision four months after original publication. Unadjusted "
                "indexes, 12-month changes, and COVID-19 methodology text remain metadata and "
                "set no endpoint or causal conclusion. This proof does not establish a BLS "
                "forecast, calibrated coverage, consumer prices, product or establishment "
                "behavior, quantity, revenue, profit, transactions, price-setting, pass-through, "
                "pandemic or market causality, external validation, deployment, investment "
                "performance, or user impact."
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
