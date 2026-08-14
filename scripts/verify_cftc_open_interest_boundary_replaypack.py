#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the CFTC TFF ReplayPack."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy
import pydantic
import pypdf
import scipy

from finreplay import __version__
from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import CompiledReplayPack, EngineName, ReplayStudio
from finreplay.scenarios import (
    CFTC_TFF_SCHEDULE_SOURCE_ID,
    OfficialEventLock,
    build_cftc_open_interest_boundary_replay_spec,
    load_cftc_open_interest_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
INPUT_RESPONSE_SHA256S = {
    "3488b3fb375fcee6b53d8e3dffc4f5c0b1f5e35e83e9cb4d881475a5c88bcc3b",
    "6d9be78582d398274618bd19114b08479c4fb4c35367b05193818003460bf5da",
    "9e795b8609b595b004211c1df8af3a06936d002582f2a1274812e148d368335a",
    "a4ffcf3bb82606d167b3492c826f2b03ced9df2a88e292bb9213fa78c464ecea",
    "a9695fe93031cc81f7ff22a6b5c12b1f6d9599b972248e9a65ce8634eaab34fa",
}
SUPPORTING_RECEIPT_SHA256 = "ea85ba99ecf5a7d77871e066673d55b0bfde2ebd1aff9e4e86472e366f87da9c"
EVENT_PAYLOAD_SHA256 = "a5be9af3007f082fc2473085d6eda05d56b5df6e25a341d67592c98b8a6d321c"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-lock", type=Path, required=True)
    parser.add_argument("--event-lock", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--write-receipt", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    studio = ReplayStudio()
    committed_receipt = studio.verify(args.pack)
    compiled = CompiledReplayPack.model_validate_json(
        (args.pack / "report.json").read_text(encoding="utf-8")
    )
    lock = load_cftc_open_interest_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(args.event_lock.read_text(encoding="utf-8"))
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match CFTC TFF input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match CFTC TFF input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match CFTC TFF input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-cftc-tff-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_cftc_open_interest_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_cftc_open_interest_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        second_root = studio.build(second_spec, temporary_root / "second").root
        first_files = _file_map(first_root)
        second_files = _file_map(second_root)
        committed_files = _file_map(args.pack)
        first_zip = studio.archive(first_root, temporary_root / "first.zip")
        second_zip = studio.archive(second_root, temporary_root / "second.zip")
        byte_identical_rebuilds = first_files == second_files == committed_files
        byte_identical_archives = first_zip.read_bytes() == second_zip.read_bytes()
    elapsed = time.perf_counter() - started

    artifacts = {artifact.engine: artifact for artifact in compiled.spec.artifacts}
    timevault = artifacts[EngineName.TIMEVAULT].payload
    shock = artifacts[EngineName.SHOCKCOMPILER].payload
    trial = artifacts[EngineName.TRIALCOURT].payload
    if len(event_lock.records) != 1:
        raise SystemExit("CFTC TFF event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_level = int(event_record.payload["open_interest_contracts"])
    event_change = int(event_record.payload["reported_change_from_prior_week_contracts"])
    variable = "next_ust_2y_tff_open_interest_contracts"
    baseline = int(shock["naive_baseline"][variable])
    lower = int(shock["bound_construction"]["lower_level_contracts"])
    upper = int(shock["bound_construction"]["upper_level_contracts"])
    known_decline = int(shock["bound_construction"]["known_decline_contracts"])
    upper_breach = event_level - upper
    assertions = {
        "all_artifacts_reproduced": all(
            artifact.status.value == "reproduced" for artifact in artifacts.values()
        ),
        "byte_identical_directory_rebuilds": byte_identical_rebuilds,
        "byte_identical_zip_rebuilds": byte_identical_archives,
        "compiled_pack_matches_rebuild": (
            compiled == studio.compile(first_spec) == studio.compile(second_spec)
        ),
        "cross_engine_trace_is_stable": (
            committed_receipt.trace_id
            == studio.compile(first_spec).trace_id
            == studio.compile(second_spec).trace_id
        ),
        "distinct_locked_inputs_equal_two": compiled.spec.distinct_input_records == 2,
        "five_official_response_hashes_are_bound": (
            set(lock.source_response_sha256s)
            == set(compiled.source_hashes)
            == INPUT_RESPONSE_SHA256S
        ),
        "idempotent_supporting_receipt_is_bound": (
            lock.supporting_receipt_sha256 == SUPPORTING_RECEIPT_SHA256
            and timevault["supporting_receipt_sha256"] == SUPPORTING_RECEIPT_SHA256
        ),
        "locked_report_rows_are_exact": {
            (
                record.payload.get("report_date"),
                record.payload.get("open_interest_contracts"),
                record.payload.get("reported_change_from_prior_week_contracts"),
                record.interval.available_at,
                record.interval.availability_confidence,
                record.source.sha256,
                record.source.vintage_as_of,
            )
            for record in lock.records
        }
        == {
            (
                "2026-07-14",
                4_465_199,
                4_262,
                datetime(2026, 7, 17, 19, 30, tzinfo=UTC),
                0.98,
                "6d9be78582d398274618bd19114b08479c4fb4c35367b05193818003460bf5da",
                datetime(2026, 7, 31, 19, 30, tzinfo=UTC),
            ),
            (
                "2026-07-21",
                4_335_075,
                -130_124,
                datetime(2026, 7, 24, 19, 30, tzinfo=UTC),
                0.98,
                "6d9be78582d398274618bd19114b08479c4fb4c35367b05193818003460bf5da",
                datetime(2026, 7, 31, 19, 30, tzinfo=UTC),
            ),
        },
        "api_annual_crosscheck_is_explicit": (
            timevault["api_annual_crosscheck_verified"] is True
            and timevault["source_response_file_count"] == 5
        ),
        "scheduled_timing_uncertainty_is_explicit": (
            timevault["schedule_self_describes_as_tentative"] is True
            and timevault["actual_row_publication_log_available"] is False
            and all(record.interval.availability_confidence == 0.98 for record in lock.records)
        ),
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_one_known_decline": (
            lower == 4_204_951
            and upper == 4_335_075
            and known_decline == 130_124
            and shock["bound_construction"]["range_width_contracts"] == 130_124
            and shock["bound_construction"]["total_open_interest_only"] is True
            and shock["bound_construction"]["endpoint_method"]
            == "latest_level_persistence_or_repeat_one_known_decline"
        ),
        "naive_baseline_is_july21_persistence": baseline == 4_335_075,
        "auxiliary_source_fields_are_not_used": (
            shock["bound_construction"]["category_positions_used"] is False
            and shock["bound_construction"]["trader_counts_used"] is False
            and shock["bound_construction"]["contract_face_value_used"] is False
            and timevault["source_auxiliary_positions_used_as_range_input"] is False
            and timevault["contract_face_value_notional_conversion_performed"] is False
        ),
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (event_record_ids & set(compiled.source_record_ids)),
        "post_decision_event_is_exact_cftc_fact": (
            event_record.source.source_id == CFTC_TFF_SCHEDULE_SOURCE_ID
            and event_record.source.sha256
            == "6d9be78582d398274618bd19114b08479c4fb4c35367b05193818003460bf5da"
            and event_record.entity_id == "cftc_contract:042601"
            and event_record.interval.published_at == datetime(2026, 7, 31, 19, 30, tzinfo=UTC)
            and event_record.interval.available_at == datetime(2026, 7, 31, 19, 30, tzinfo=UTC)
            and event_record.interval.availability_confidence == 0.98
            and event_record.interval.valid_from == datetime(2026, 7, 28, tzinfo=UTC)
            and event_record.payload.get("report_date") == "2026-07-28"
            and event_record.payload.get("metric") == "open_interest_all_futures_only"
            and event_record.payload.get("unit") == "Futures Contracts"
            and event_record.payload.get("open_interest_contracts") == 4_406_588
            and event_record.payload.get("reported_change_from_prior_week_contracts") == 71_513
            and event_record.payload.get("api_annual_crosscheck_verified") is True
            and event_record.payload.get("schedule_self_describes_as_tentative") is True
            and event_record.payload.get("actual_row_publication_log_available") is False
            and event_record.payload.get("contract_face_value_notional_conversion_performed")
            is False
            and _hash(event_record.payload) == EVENT_PAYLOAD_SHA256
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_level_is_above_declared_range": event_level > upper,
        "post_event_upper_bound_breach_equals_71513_contracts": upper_breach == 71_513,
        "post_event_reported_change_equals_71513_contracts": event_change == 71_513,
        "fixed_range_is_not_widened_after_event": (
            shock["bound_construction"]["upper_level_contracts"] == 4_335_075
            and event_level
            not in {
                shock["bound_construction"]["lower_level_contracts"],
                shock["bound_construction"]["upper_level_contracts"],
            }
        ),
        "classification_and_measurement_boundaries_are_explicit": all(
            record.payload.get("classification_and_intent_caveats_validated") is True
            and record.payload.get("contract_face_value_notional_conversion_performed") is False
            and record.payload.get("actual_row_publication_log_available") is False
            for record in (*lock.records, *event_lock.records)
        ),
        "simulation_remains_visible": (
            compiled.contains_simulation
            and compiled.evidence_totals.get(EvidenceClass.SIMULATED, 0) > 0
        ),
        "trialcourt_rejects_retrospective_attempt": (
            trial["decision"]["disposition"] == TrialDisposition.REJECT.value
            and len(trial["decision"]["findings"]) == 6
            and trial["manifest"]["rejected_decisions"] == 1
        ),
    }
    if not all(assertions.values()):
        failed = sorted(name for name, passed in assertions.items() if not passed)
        raise SystemExit(f"CFTC TFF assertions failed: {failed}")

    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines "
            "rebuild the committed directory and deterministic ZIP bytes from two locked "
            "pre-decision CFTC Futures Only TFF rows and five exact official response hashes. "
            "It verifies API/annual-file agreement, an official scheduled 3:30 p.m. Eastern "
            "boundary explicitly limited to 0.98 confidence, a July 21 persistence baseline, "
            "and a no-probability 4,204,951-to-4,335,075-contract stress range. The separately "
            "locked July 28 event is disjoint and, as evaluation only, is 71,513 contracts "
            "above the fixed upper endpoint; the range is not widened after the fact. CFTC "
            "classification, trader-count, spreading, face-value, and intent boundaries remain "
            "explicit. This does not prove actual publication to the second, forecast skill, "
            "calibrated coverage, direction, intent, volume, executions, notional, P&L, market "
            "impact, causality, investment performance, external validation, deployment, or "
            "user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "supporting_receipt_sha256": lock.supporting_receipt_sha256,
        "post_event_evaluation": {
            "reported_july28_open_interest_contracts": event_level,
            "reported_july28_change_contracts": event_change,
            "july21_persistence_baseline_contracts": baseline,
            "known_july14_to_july21_decline_contracts": known_decline,
            "declared_lower_level_contracts": lower,
            "declared_upper_level_contracts": upper,
            "upper_bound_breach_contracts": upper_breach,
            "range_breached": True,
            "range_widened_after_event": False,
            "probability_assigned": False,
            "source_auxiliary_fields_used": False,
            "used_as_decision_input": False,
        },
        "pack_sha256": committed_receipt.pack_sha256,
        "pack_receipt_sha256": committed_receipt.receipt_sha256,
        "trace_id": committed_receipt.trace_id,
        "code_commit": compiled.spec.code_commit,
        "artifact_sha256": {
            engine.value: artifacts[engine].artifact_sha256 for engine in sorted(RELEVANT_ENGINES)
        },
        "file_sha256": {
            relative_path: hashlib.sha256(content).hexdigest()
            for relative_path, content in sorted(committed_files.items())
        },
    }
    semantic_sha256 = _hash(semantic)
    payload = {
        **semantic,
        "semantic_sha256": semantic_sha256,
        "runtime": {
            "measured_at": datetime.now(UTC).isoformat(),
            "two_rebuild_elapsed_seconds": elapsed,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "finreplay": __version__,
            "duckdb": duckdb.__version__,
            "numpy": numpy.__version__,
            "pydantic": pydantic.__version__,
            "pypdf": pypdf.__version__,
            "scipy": scipy.__version__,
            "runner_commit": _git_commit(),
            "runner_dirty": bool(_git_status()),
        },
    }
    payload["receipt_sha256"] = _hash(payload)
    if args.write_receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    elif args.receipt.exists():
        _verify_stored_receipt(args.receipt, semantic, semantic_sha256)
    else:
        raise SystemExit("CFTC TFF receipt is missing; use --write-receipt once")
    print(
        f"verified=true assertions={len(assertions)} engines={len(artifacts)} "
        f"input_records={compiled.spec.distinct_input_records} "
        f"elapsed_seconds={elapsed:.6f} semantic_sha256={semantic_sha256} "
        f"receipt_sha256={payload['receipt_sha256']}"
    )


def _verify_stored_receipt(
    path: Path,
    expected_semantic: dict[str, Any],
    expected_semantic_sha256: str,
) -> None:
    stored = json.loads(path.read_text(encoding="utf-8"))
    receipt_sha256 = stored.pop("receipt_sha256", None)
    if receipt_sha256 != _hash(stored):
        raise SystemExit("stored CFTC TFF receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored CFTC TFF semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored CFTC TFF semantic receipt differs from fresh rebuild")


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


if __name__ == "__main__":
    main()
