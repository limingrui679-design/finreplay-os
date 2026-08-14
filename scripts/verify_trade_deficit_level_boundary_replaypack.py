#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the joint Census/BEA FT-900 ReplayPack."""

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
    CENSUS_BEA_FT900_SOURCE_ID,
    OfficialEventLock,
    build_trade_deficit_level_boundary_replay_spec,
    load_trade_deficit_level_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
INPUT_EVIDENCE_SHA256S = {
    "b1cfa18560bc0bbb4c325d5b49bdba078407d6d247197ce1edc2d6ae30be61bf",
    "e64a8fb9028b84789ae930db99aa67e3fb0918da7e729349f7b0907bf62193f7",
    "5c32f19b5b556d479de8a7cd228bda3348e5b1ceec8dfd9d327d6a783847bb7c",
    "7527ba2aab574733774950ac68480d95d6f4286ddc630fca8198844503941e98",
}
EVENT_PDF_SHA256 = "e78fd48355753e763a569142743a19d9273d82bc10d229740029b7ed2a114ef7"
EVENT_ZIP_SHA256 = "99d434fd762df6b942805bc2c9014003840db52a8b5203bfd2588a74e9dd5cf1"


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
    lock = load_trade_deficit_level_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(args.event_lock.read_text(encoding="utf-8"))
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match FT-900 input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match FT-900 input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match FT-900 input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-census-ft900-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_trade_deficit_level_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_trade_deficit_level_boundary_replay_spec(
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
    trial = artifacts[EngineName.TRIALCOURT].payload
    if len(event_lock.records) != 1:
        raise SystemExit("FT-900 event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_level = int(event_record.payload["value_million_dollars"])
    variable = "next_goods_services_deficit_level_million_dollars"
    baseline = int(shock["naive_baseline"][variable])
    lower = int(shock["bound_construction"]["lower_level_million_dollars"])
    upper = int(shock["bound_construction"]["upper_level_million_dollars"])
    known_decline = int(
        shock["bound_construction"]["known_decision_snapshot_decline_million_dollars"]
    )
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
        "four_paired_pdf_xls_hashes_are_bound": (
            set(lock.source_response_sha256s)
            == set(compiled.source_hashes)
            == INPUT_EVIDENCE_SHA256S
        ),
        "locked_release_pair_is_exact": {
            (
                record.payload.get("release_date"),
                record.payload.get("reference_month"),
                record.payload.get("value_million_dollars"),
                record.payload.get("prior_month_previous_release_deficit_million_dollars"),
                record.payload.get("prior_month_revised_deficit_million_dollars"),
                record.payload.get("prior_month_revision_delta_million_dollars"),
                record.payload.get("release_timezone_abbreviation"),
                record.payload.get("current_archive_byte_identity_at_release_claimed"),
                record.source.sha256,
                record.payload.get("release_xls_zip_sha256"),
            )
            for record in lock.records
        }
        == {
            (
                "2020-03-06",
                "2020-01",
                45_338,
                48_880,
                48_613,
                -267,
                "EST",
                False,
                "b1cfa18560bc0bbb4c325d5b49bdba078407d6d247197ce1edc2d6ae30be61bf",
                "e64a8fb9028b84789ae930db99aa67e3fb0918da7e729349f7b0907bf62193f7",
            ),
            (
                "2020-04-02",
                "2020-02",
                39_932,
                45_338,
                45_482,
                144,
                "EDT",
                False,
                "5c32f19b5b556d479de8a7cd228bda3348e5b1ceec8dfd9d327d6a783847bb7c",
                "7527ba2aab574733774950ac68480d95d6f4286ddc630fca8198844503941e98",
            ),
        },
        "decision_snapshot_uses_revised_january_not_stale_initial": (
            shock["known_decision_snapshot_levels"]
            == {
                "january_initial_release_deficit_million_dollars": 45_338,
                "decision_snapshot_revised_january_deficit_million_dollars": 45_482,
                "january_revision_delta_known_at_decision_million_dollars": 144,
                "february_initial_deficit_million_dollars": 39_932,
                "known_decision_snapshot_decline_million_dollars": 5_550,
                "lower_level_million_dollars": 34_382,
                "upper_level_million_dollars": 39_932,
                "range_width_million_dollars": 5_550,
            }
            and shock["bound_construction"][
                "january_initial_release_used_as_numeric_endpoint_input"
            ]
            is False
        ),
        "release_time_and_current_byte_boundary_are_explicit": (
            artifacts[EngineName.TIMEVAULT].payload["release_time_rule"]
            == "08:30 America/New_York from each dated FT-900 PDF"
            and artifacts[EngineName.TIMEVAULT].payload[
                "current_archive_byte_identity_at_release_claimed"
            ]
            is False
        ),
        "paired_pdf_xls_crosscheck_is_explicit": (
            artifacts[EngineName.TIMEVAULT].payload["paired_pdf_xls_crosscheck_verified"] is True
            and artifacts[EngineName.TIMEVAULT].payload["source_evidence_file_count"] == 4
        ),
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_same_release_snapshot_decline": (
            lower == 34_382
            and upper == 39_932
            and known_decline == 5_550
            and shock["bound_construction"]["range_width_million_dollars"] == 5_550
            and shock["bound_construction"]["basis_is_single_february_release_snapshot"] is True
            and shock["bound_construction"]["endpoint_method"]
            == "latest_initial_level_persistence_or_repeat_same_release_snapshot_decline"
        ),
        "naive_baseline_is_february_initial_level_persistence": baseline == 39_932,
        "official_confidence_interval_is_not_used": (
            shock["bound_construction"]["official_confidence_interval_used"] is False
        ),
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (event_record_ids & set(compiled.source_record_ids)),
        "post_decision_event_is_exact_ft900_fact": (
            event_record.source.source_id == CENSUS_BEA_FT900_SOURCE_ID
            and event_record.source.sha256 == EVENT_PDF_SHA256
            and event_record.payload.get("release_xls_zip_sha256") == EVENT_ZIP_SHA256
            and event_record.entity_id == "census_bea_ft900:us_goods_services_deficit"
            and str(event_record.source.url)
            == "https://www.census.gov/foreign-trade/Press-Release/ft900/ft900_2003.pdf"
            and event_record.payload.get("release_xls_zip_url")
            == "https://www.census.gov/foreign-trade/Press-Release/ft900/ft900xls_2003.zip"
            and event_record.interval.published_at == datetime(2020, 5, 5, 12, 30, tzinfo=UTC)
            and event_record.interval.available_at == datetime(2020, 5, 5, 12, 30, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-05-05"
            and event_record.payload.get("reference_month") == "2020-03"
            and event_record.payload.get("metric") == "goods_services_deficit_level_million_dollars"
            and event_record.payload.get("unit")
            == "Million U.S. Dollars of Seasonally Adjusted Deficit"
            and event_record.payload.get("value_million_dollars") == 44_415
            and event_record.payload.get("signed_balance_million_dollars") == -44_415
            and event_record.payload.get("release_pdf_sha256") == EVENT_PDF_SHA256
            and event_record.payload.get("release_pdf_pages") == 62
            and event_record.payload.get("release_xls_zip_member_count") == 31
            and event_record.payload.get("release_timezone_abbreviation") == "EDT"
            and event_record.payload.get("availability_method")
            == "exact_time_in_pdf_values_crosschecked_to_xls_zip"
            and event_record.payload.get("pdf_xls_crosscheck_verified") is True
            and event_record.payload.get("goods_data_complete_enumeration_of_cbp_documents") is True
            and event_record.payload.get("goods_data_subject_to_sampling_error") is False
            and event_record.payload.get("nonsampling_errors_possible") is True
            and event_record.payload.get(
                "headline_statistical_significance_applicable_or_measurable"
            )
            is False
            and event_record.payload.get("adjusted_for_price_changes") is False
            and event_record.payload.get("covid_publication_standard_statement_present") is True
            and event_record.payload.get("current_archive_byte_identity_at_release_claimed")
            is False
            and event_level == 44_415
        ),
        "post_decision_revisions_remain_later_snapshot_only": (
            event_record.payload.get("release_snapshot_deficit_million_dollars")
            == {"2020-01": 45_482, "2020-02": 39_810, "2020-03": 44_415}
            and event_record.payload.get("release_snapshot_previous_deficit_million_dollars")
            == {"2020-01": 45_482, "2020-02": 39_932, "2020-03": None}
            and event_record.payload.get("release_snapshot_revision_delta_million_dollars")
            == {"2020-01": 0, "2020-02": -122, "2020-03": None}
            and all(record.payload.get("release_date") != "2020-05-05" for record in lock.records)
            and next(
                record
                for record in lock.records
                if record.payload.get("reference_month") == "2020-02"
            ).payload["value_million_dollars"]
            == 39_932
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_level_is_above_declared_range": event_level > upper,
        "post_event_upper_bound_breach_equals_4483_million_dollars": upper_breach == 4_483,
        "seasonal_nominal_and_error_boundaries_are_explicit": all(
            record.payload.get("seasonally_adjusted") is True
            and record.payload.get("adjusted_for_price_changes") is False
            and record.payload.get("goods_data_subject_to_sampling_error") is False
            and record.payload.get("nonsampling_errors_possible") is True
            and record.payload.get("headline_statistical_significance_applicable_or_measurable")
            is False
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
        raise SystemExit(f"FT-900 assertions failed: {failed}")

    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines "
            "rebuild the committed directory and deterministic ZIP bytes from two locked "
            "pre-decision joint Census/BEA FT-900 release records and four exact paired "
            "official PDF/XLS ZIP hashes. It verifies that range construction uses the revised "
            "45,482 January and initial 39,932 February deficit levels inside the single April 2 "
            "decision snapshot, while the 45,338 January initial release remains revision "
            "lineage only. Current hashes are not represented as release-time byte identity. "
            "The separately locked March deficit is disjoint and, as an evaluation only, is "
            "4,483 million dollars above the fixed no-probability upper endpoint; the range is "
            "not widened after the fact, and the May release's -122 February revision does not "
            "overwrite the decision input. The COVID text proves only a publication-standards "
            "statement. This does not prove forecast skill, calibrated coverage, statistical "
            "significance, price-adjusted trade volume, a contemporaneous COVID or trade-policy "
            "effect, trade, price, pandemic, policy, sector, firm, or macroeconomic causality, "
            "investment performance, deployment, external review, or user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "post_event_evaluation": {
            "reported_march_deficit_million_dollars": event_level,
            "february_persistence_baseline_million_dollars": baseline,
            "decision_snapshot_revised_january_deficit_million_dollars": 45_482,
            "known_decision_snapshot_decline_million_dollars": known_decline,
            "declared_lower_level_million_dollars": lower,
            "declared_upper_level_million_dollars": upper,
            "upper_bound_breach_million_dollars": upper_breach,
            "event_february_initial_deficit_million_dollars": 39_932,
            "event_february_revised_deficit_million_dollars": 39_810,
            "event_february_revision_delta_million_dollars": -122,
            "event_january_deficit_million_dollars": 45_482,
            "range_breached": True,
            "probability_assigned": False,
            "official_confidence_interval_used": False,
            "adjusted_for_price_changes": False,
            "current_archive_byte_identity_at_release_claimed": False,
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
        raise SystemExit("FT-900 receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored FT-900 receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored FT-900 semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored FT-900 semantic receipt differs from fresh rebuild")


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
