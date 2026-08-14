#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the EIA WNGSR ReplayPack."""

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
import xlrd

from finreplay import __version__
from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import CompiledReplayPack, EngineName, ReplayStudio
from finreplay.scenarios import (
    EIA_WNGSR_SOURCE_ID,
    OfficialEventLock,
    build_working_gas_stock_boundary_replay_spec,
    load_working_gas_stock_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
SOURCE_SHA256S = {
    "ee7c703c6d30176d0253b879aa4c8c6dc0178b411c36d73036d89aeff412dd3c",
    "7973c8f5721c1addb2f8df496134aa0697a98f1f4eb9b075223f19f12f513b18",
    "de3123137bf3d5055181aa709e522caec0afe301a1077fca79a886ee5249536b",
}
EVENT_PAYLOAD_SHA256 = "920dc8ff96d03bbf03787b186d5759256fe86325626cbb08531f60282a4c8061"


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
    lock = load_working_gas_stock_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(
        args.event_lock.read_text(encoding="utf-8")
    )
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match WNGSR input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match WNGSR input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match WNGSR input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-eia-wngsr-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_working_gas_stock_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_working_gas_stock_boundary_replay_spec(
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
    shock = artifacts[EngineName.SHOCKCOMPILER].payload
    timevault = artifacts[EngineName.TIMEVAULT].payload
    trial = artifacts[EngineName.TRIALCOURT].payload
    if len(event_lock.records) != 1:
        raise SystemExit("WNGSR event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_level = int(event_record.payload["value_bcf"])
    event_record_ids = {event_record.record_id}
    variable = "next_lower_48_working_gas_stock_bcf"
    baseline = int(shock["naive_baseline"][variable])
    lower = int(shock["bound_construction"]["lower_stock_bcf"])
    upper = int(shock["bound_construction"]["upper_stock_bcf"])
    known_decline = int(shock["bound_construction"]["known_decline_bcf"])
    lower_breach = lower - event_level
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
        "three_official_response_hashes_are_bound": (
            set(lock.source_response_sha256s)
            == set(compiled.source_hashes)
            == SOURCE_SHA256S
        ),
        "locked_original_release_pair_is_exact": {
            (
                record.payload.get("release_date"),
                record.payload.get("week_ending"),
                record.payload.get("value_bcf"),
                record.payload.get("prior_value_bcf"),
                record.payload.get("reported_net_change_bcf"),
                record.payload.get("coefficient_of_variation_percent_lower_48"),
                record.payload.get("net_change_standard_error_bcf_lower_48"),
                record.interval.available_at,
            )
            for record in lock.records
        }
        == {
            (
                "2020-03-12",
                "2020-03-06",
                2_043,
                2_091,
                -48,
                "0.5",
                "0.6",
                datetime(2020, 3, 12, 14, 30, tzinfo=UTC),
            ),
            (
                "2020-03-19",
                "2020-03-13",
                2_034,
                2_043,
                -9,
                "0.5",
                "0.8",
                datetime(2020, 3, 19, 14, 30, tzinfo=UTC),
            ),
        },
        "original_value_recovery_is_explicit": (
            timevault["original_value_recovery_verified"] is True
        ),
        "current_history_cross_check_is_explicit": (
            timevault["current_history_cross_check_verified"] is True
        ),
        "release_time_and_source_count_are_explicit": (
            timevault["release_time_rule"]
            == "10:30 America/New_York on verified non-holiday Thursdays"
            and timevault["source_evidence_file_count"] == 3
        ),
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_one_known_decline": (
            lower == 2_025
            and upper == 2_034
            and known_decline == 9
            and shock["bound_construction"]["range_width_bcf"] == 9
            and shock["bound_construction"]["original_vintage_values_only"] is True
            and shock["bound_construction"]["endpoint_method"]
            == "latest_stock_persistence_or_repeat_one_known_decline"
        ),
        "naive_baseline_is_march13_stock_persistence": baseline == 2_034,
        "source_statistics_are_not_used": (
            shock["bound_construction"]["source_statistical_measures_used"] is False
            and timevault["source_statistical_measures_used_as_range_input"] is False
        ),
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (
            event_record_ids & set(compiled.source_record_ids)
        ),
        "post_decision_event_is_exact_wngsr_fact": (
            event_record.source.source_id == EIA_WNGSR_SOURCE_ID
            and event_record.entity_id == "eia_series:wngsr_working_gas_lower_48"
            and str(event_record.source.url) == "https://ir.eia.gov/ngs/revisions.xls"
            and event_record.interval.published_at
            == datetime(2020, 3, 26, 14, 30, tzinfo=UTC)
            and event_record.interval.available_at
            == datetime(2020, 3, 26, 14, 30, tzinfo=UTC)
            and event_record.interval.valid_from
            == datetime(2020, 3, 20, 14, 0, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-03-26"
            and event_record.payload.get("week_ending") == "2020-03-20"
            and event_record.payload.get("value_bcf") == 2_005
            and event_record.payload.get("prior_value_bcf") == 2_034
            and event_record.payload.get("reported_net_change_bcf") == -29
            and event_record.payload.get("coefficient_of_variation_percent_lower_48") == "0.5"
            and event_record.payload.get("net_change_standard_error_bcf_lower_48") == "0.8"
            and event_record.payload.get("five_region_rounding_difference_bcf") == 0
            and event_record.payload.get("current_history_matches_original_estimate") is True
            and event_record.payload.get("statistical_measures_define_finreplay_range") is False
            and event_record.payload.get("history_workbook_sha256") in SOURCE_SHA256S
            and event_record.payload.get("performance_evaluation_sha256") in SOURCE_SHA256S
            and _hash(event_record.payload) == EVENT_PAYLOAD_SHA256
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_level_is_below_declared_range": event_level < lower,
        "post_event_lower_bound_breach_equals_20_bcf": lower_breach == 20,
        "reported_stock_and_sampling_boundaries_are_explicit": all(
            record.payload.get("unit") == "Billion Cubic Feet"
            and record.payload.get("source_form") == "EIA-912"
            and record.payload.get("coefficient_of_variation_percent_lower_48") is not None
            and record.payload.get("net_change_standard_error_bcf_lower_48") is not None
            and record.payload.get("statistical_measures_define_finreplay_range") is False
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
        raise SystemExit(f"WNGSR assertions failed: {failed}")

    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines "
            "rebuild the committed directory and deterministic ZIP bytes from two locked "
            "pre-decision original EIA WNGSR records and three official response hashes. It "
            "verifies original-value recovery, current-history agreement, exact release timing, "
            "a March 13 persistence baseline, and a no-probability 2,025-to-2,034 Bcf range. "
            "The separately locked March 20 event is disjoint and, as evaluation only, is 20 "
            "Bcf below the fixed lower endpoint; the range is not widened after the fact. "
            "Source sampling measures remain metadata. This does not prove forecast skill, "
            "calibrated coverage, facility or flow measurements, storage capacity, price or "
            "market response, pandemic or policy causality, external validation, deployment, "
            "investment performance, or user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "post_event_evaluation": {
            "reported_march20_working_gas_bcf": event_level,
            "march13_persistence_baseline_bcf": baseline,
            "known_decline_bcf": known_decline,
            "declared_lower_stock_bcf": lower,
            "declared_upper_stock_bcf": upper,
            "lower_bound_breach_bcf": lower_breach,
            "reported_event_net_change_bcf": -29,
            "range_breached": True,
            "probability_assigned": False,
            "source_statistical_measures_used": False,
            "used_as_decision_input": False,
        },
        "pack_sha256": committed_receipt.pack_sha256,
        "pack_receipt_sha256": committed_receipt.receipt_sha256,
        "trace_id": committed_receipt.trace_id,
        "code_commit": compiled.spec.code_commit,
        "artifact_sha256": {
            engine.value: artifacts[engine].artifact_sha256
            for engine in sorted(RELEVANT_ENGINES)
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
            "xlrd": xlrd.__version__,
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
        raise SystemExit("WNGSR receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored WNGSR receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored WNGSR semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored WNGSR semantic receipt differs from fresh rebuild")


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
