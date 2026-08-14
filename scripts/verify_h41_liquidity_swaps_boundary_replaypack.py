#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the Federal Reserve H.4.1 ReplayPack."""

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
    H41_LIQUIDITY_SWAPS_SOURCE_ID,
    OfficialEventLock,
    build_h41_liquidity_swaps_boundary_replay_spec,
    load_h41_liquidity_swaps_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
INPUT_RESPONSE_SHA256S = {
    "d08360db4285e0db87257f5f72b6e6eff91e3f937e9da00de0bbffb62dc0a515",
    "b5dc44df02874ba2f4d112a95a04449c924e5da68ee977dcd3fae1ca812bf571",
    "a25a62443e7ee3bbda990ec2ef095624e1873c237819a81d1d17c6c7a2aef77e",
    "77157f38df055c43d46fb850d0534a5fd4836449df8067ed87612890f69b8819",
}
SUPPORTING_RECEIPT_SHA256 = (
    "312ef4c75191536fc8241076af9f42d7e55c90db8f47fdb91a38b11cab1b9580"
)
EVENT_SEMANTIC_SHA256 = (
    "9eb8775b0be1c637c1d58c73f1545cf97716ba313e02412fdd1e0f722dce183b"
)
EVENT_PAYLOAD_SHA256 = (
    "37ab8205aadc5124c0bd3c98f5106405ee67d5c7017e0b903cba5070c62a32cb"
)
MEASUREMENT_BOUNDARY = (
    "Dollar value of foreign currency held under swap agreements, valued at the "
    "exchange rate used when acquired and to be used when returned to the foreign "
    "central bank."
)


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
    lock = load_h41_liquidity_swaps_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(
        args.event_lock.read_text(encoding="utf-8")
    )
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match H.4.1 input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match H.4.1 input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match H.4.1 input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-fed-h41-swaps-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_h41_liquidity_swaps_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_h41_liquidity_swaps_boundary_replay_spec(
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
        raise SystemExit("H.4.1 event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_balance = int(event_record.payload["value_millions"])
    variable = "next_wednesday_liquidity_swaps_outstanding_million_dollars"
    baseline = int(shock["naive_baseline"][variable])
    lower = int(shock["bound_construction"]["lower_level_million_dollars"])
    upper = int(shock["bound_construction"]["upper_level_million_dollars"])
    known_increase = int(shock["bound_construction"]["known_increase_million_dollars"])
    lower_distance = event_balance - lower
    upper_headroom = upper - event_balance
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
        "four_official_html_ascii_hashes_are_bound": (
            set(lock.source_response_sha256s)
            == set(compiled.source_hashes)
            == INPUT_RESPONSE_SHA256S
        ),
        "idempotent_supporting_receipt_is_bound": (
            lock.supporting_receipt_sha256 == SUPPORTING_RECEIPT_SHA256
            and timevault["supporting_receipt_sha256"] == SUPPORTING_RECEIPT_SHA256
        ),
        "locked_release_pair_is_exact": {
            (
                record.payload.get("release_date"),
                record.payload.get("week_ending"),
                record.payload.get("value_millions"),
                record.payload.get("weekly_average_millions"),
                record.payload.get("weekly_average_change_from_prior_week_millions"),
                record.payload.get("weekly_average_change_from_year_ago_millions"),
                record.interval.available_at,
                record.source.sha256,
                record.payload.get("release_semantic_sha256"),
            )
            for record in lock.records
        }
        == {
            (
                "2020-03-19",
                "2020-03-18",
                45,
                45,
                -13,
                -23,
                datetime(2020, 3, 19, 20, 30, tzinfo=UTC),
                "8261da1e27e2ed08ab3671af4b94c394108e7809a256638d0a7332f8ed60519b",
                "8261da1e27e2ed08ab3671af4b94c394108e7809a256638d0a7332f8ed60519b",
            ),
            (
                "2020-03-26",
                "2020-03-25",
                206_051,
                168_814,
                168_769,
                168_748,
                datetime(2020, 3, 26, 20, 30, tzinfo=UTC),
                "90221fc89c30bf797806200eb6bc725f976ca314d5f9a098c587143d2fc6d540",
                "90221fc89c30bf797806200eb6bc725f976ca314d5f9a098c587143d2fc6d540",
            ),
        },
        "paired_html_ascii_crosscheck_is_explicit": (
            timevault["html_ascii_crosscheck_verified"] is True
            and timevault["source_response_file_count"] == 4
            and all(
                record.payload.get("html_ascii_crosscheck_verified") is True
                for record in lock.records
            )
        ),
        "official_input_release_times_are_exact": all(
            record.interval.availability_confidence == 1.0
            and record.payload.get("availability_method")
            == "exact_official_stated_time_crosschecked_html_ascii"
            and record.payload.get("official_stated_release_at") is not None
            for record in lock.records
        ),
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_one_known_increase": (
            lower == 206_051
            and upper == 412_057
            and known_increase == 206_006
            and shock["bound_construction"]["range_width_million_dollars"] == 206_006
            and shock["bound_construction"]["wednesday_balance_only"] is True
            and shock["bound_construction"]["endpoint_method"]
            == "latest_level_persistence_or_repeat_one_known_increase"
        ),
        "naive_baseline_is_march25_persistence": baseline == 206_051,
        "auxiliary_and_market_fields_are_not_used": (
            shock["bound_construction"]["weekly_average_used"] is False
            and shock["bound_construction"]["year_change_used"] is False
            and shock["bound_construction"]["current_market_revaluation_performed"] is False
            and timevault["weekly_average_fields_used_as_range_input"] is False
        ),
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (
            event_record_ids & set(compiled.source_record_ids)
        ),
        "post_decision_event_is_exact_h41_fact": (
            event_record.source.source_id == H41_LIQUIDITY_SWAPS_SOURCE_ID
            and event_record.source.sha256 == EVENT_SEMANTIC_SHA256
            and event_record.entity_id
            == "federal_reserve_facility:central_bank_liquidity_swaps"
            and str(event_record.source.url)
            == "https://www.federalreserve.gov/releases/h41/20200402/h41.htm"
            and event_record.interval.published_at
            == datetime(2020, 4, 3, 4, 0, tzinfo=UTC)
            and event_record.interval.available_at
            == datetime(2020, 4, 3, 4, 0, tzinfo=UTC)
            and event_record.interval.availability_confidence == 1.0
            and event_record.interval.valid_from == datetime(2020, 4, 1, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-04-02"
            and event_record.payload.get("week_ending") == "2020-04-01"
            and event_record.payload.get("metric") == "wednesday_outstanding"
            and event_record.payload.get("unit") == "Millions of Dollars"
            and event_record.payload.get("value_millions") == 348_544
            and event_record.payload.get("weekly_average_millions") == 327_787
            and event_record.payload.get("weekly_average_change_from_prior_week_millions")
            == 158_973
            and event_record.payload.get("weekly_average_change_from_year_ago_millions")
            == 326_422
            and event_record.payload.get("release_semantic_sha256")
            == EVENT_SEMANTIC_SHA256
            and event_record.payload.get("html_ascii_crosscheck_verified") is True
            and event_record.payload.get("official_stated_release_at") is None
            and event_record.payload.get("actual_server_publication_log_available") is False
            and event_record.payload.get("availability_method")
            == "release_date_following_new_york_midnight_html_ascii"
            and _hash(event_record.payload) == EVENT_PAYLOAD_SHA256
        ),
        "post_decision_event_timing_is_conservative_and_later": (
            event_record.interval.available_at > lock.decision_time
            and event_record.payload.get("conservative_available_at")
            == "2020-04-03T04:00:00+00:00"
        ),
        "reported_post_event_balance_is_inside_declared_range": (
            lower <= event_balance <= upper
        ),
        "post_event_lower_distance_equals_142493_million": lower_distance == 142_493,
        "post_event_upper_headroom_equals_63513_million": upper_headroom == 63_513,
        "fixed_range_is_not_changed_after_event": (
            shock["bound_construction"]["lower_level_million_dollars"] == 206_051
            and shock["bound_construction"]["upper_level_million_dollars"] == 412_057
            and event_balance
            not in {
                shock["bound_construction"]["lower_level_million_dollars"],
                shock["bound_construction"]["upper_level_million_dollars"],
            }
        ),
        "inside_result_is_not_promoted_to_forecast_success": (
            "without being relabelled as forecast success" in event_lock.claim_boundary
            and "not a Federal Reserve forecast" in compiled.spec.claim_boundary
        ),
        "measurement_boundary_is_explicit": all(
            record.payload.get("measurement_boundary") == MEASUREMENT_BOUNDARY
            and record.payload.get("html_ascii_crosscheck_verified") is True
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
        raise SystemExit(f"H.4.1 assertions failed: {failed}")

    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines "
            "rebuild the committed directory and deterministic ZIP bytes from two locked "
            "pre-decision Federal Reserve H.4.1 Table 1 records and four exact official "
            "HTML/ASCII input hashes. It binds the idempotent six-response supporting receipt, "
            "paired-format cross-checks, exact official stated March release times, a March "
            "25 persistence baseline, and a no-probability $206,051-to-$412,057-million "
            "stress range. The separately locked April 1 balance is disjoint, is conservatively "
            "eligible only at the following New York midnight, and lies $142,493 million above "
            "the lower endpoint and $63,513 million below the upper endpoint. That inside result "
            "does not become forecast success and the range is not changed after the fact. The "
            "H.4.1 exchange-rate measurement convention is retained. This does not prove an "
            "official or calibrated interval, current-market exposure, institution or "
            "transaction behavior, counterparty loss, P&L, policy effectiveness, systemic-"
            "stress causality, investment performance, external validation, deployment, or "
            "user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "supporting_receipt_sha256": lock.supporting_receipt_sha256,
        "post_event_evaluation": {
            "reported_april1_balance_million_dollars": event_balance,
            "march25_persistence_baseline_million_dollars": baseline,
            "known_march18_to_march25_increase_million_dollars": known_increase,
            "declared_lower_level_million_dollars": lower,
            "declared_upper_level_million_dollars": upper,
            "distance_above_lower_million_dollars": lower_distance,
            "headroom_below_upper_million_dollars": upper_headroom,
            "inside_declared_range": True,
            "forecast_success_claimed": False,
            "range_changed_after_event": False,
            "probability_assigned": False,
            "weekly_average_used_as_range_input": False,
            "current_market_revaluation_performed": False,
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
        raise SystemExit("H.4.1 receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored H.4.1 receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored H.4.1 semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored H.4.1 semantic receipt differs from fresh rebuild")


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
