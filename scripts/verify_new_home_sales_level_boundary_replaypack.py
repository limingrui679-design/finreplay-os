#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the Census/HUD NRS ReplayPack."""

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
    CENSUS_NRS_SOURCE_ID,
    OfficialEventLock,
    build_new_home_sales_level_boundary_replay_spec,
    load_new_home_sales_level_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
INPUT_EVIDENCE_SHA256S = {
    "ba86558efb14745ddf6c56684c9023444397941a0c49bed406e1d6eda6dcca3b",
    "9a47e1fd70c0830394a9681ec0bc1881e1d0522c105ff9aeff60dd01c98c3fb8",
}
EVENT_PDF_SHA256 = "c3d0d06001540a5dbdca154eb6c61139b8a8aaa9b9ec205bcca4fc67ee30575a"


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
    lock = load_new_home_sales_level_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(args.event_lock.read_text(encoding="utf-8"))
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match NRS input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match NRS input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match NRS input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-census-nrs-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_new_home_sales_level_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_new_home_sales_level_boundary_replay_spec(
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
        raise SystemExit("NRS event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_level = int(event_record.payload["value_units"])
    variable = "next_new_single_family_houses_sold_level_units_saar"
    baseline = int(shock["naive_baseline"][variable])
    lower = int(shock["bound_construction"]["lower_level_units_saar"])
    upper = int(shock["bound_construction"]["upper_level_units_saar"])
    known_decline = int(shock["bound_construction"]["known_decision_snapshot_decline_units_saar"])
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
        "two_official_pdf_hashes_are_bound": (
            set(lock.source_response_sha256s)
            == set(compiled.source_hashes)
            == INPUT_EVIDENCE_SHA256S
        ),
        "locked_release_pair_is_exact": {
            (
                record.payload.get("release_date"),
                record.payload.get("reference_month"),
                record.payload.get("value_units"),
                record.payload.get("prior_month_value_in_previous_release_units"),
                record.payload.get("prior_month_revised_value_units"),
                record.payload.get("prior_month_revision_delta_units"),
                record.payload.get("release_timezone_abbreviation"),
                record.payload.get("reported_monthly_margin_90_percent"),
                record.payload.get("reported_monthly_ci_includes_zero"),
                record.source.sha256,
            )
            for record in lock.records
        }
        == {
            (
                "2020-02-26",
                "2020-01",
                764_000,
                None,
                708_000,
                None,
                "EST",
                "17.8",
                True,
                "ba86558efb14745ddf6c56684c9023444397941a0c49bed406e1d6eda6dcca3b",
            ),
            (
                "2020-03-24",
                "2020-02",
                765_000,
                764_000,
                800_000,
                36_000,
                "EDT",
                "14.8",
                True,
                "9a47e1fd70c0830394a9681ec0bc1881e1d0522c105ff9aeff60dd01c98c3fb8",
            ),
        },
        "decision_snapshot_uses_revised_january_not_stale_initial": (
            shock["known_decision_snapshot_levels"]
            == {
                "january_initial_release_sales_units_saar": 764_000,
                "decision_snapshot_revised_january_sales_units_saar": 800_000,
                "january_revision_delta_known_at_decision_units_saar": 36_000,
                "february_initial_sales_units_saar": 765_000,
                "known_decision_snapshot_decline_units_saar": 35_000,
                "lower_level_units_saar": 730_000,
                "upper_level_units_saar": 765_000,
                "range_width_units_saar": 35_000,
            }
            and shock["bound_construction"][
                "january_initial_release_used_as_numeric_endpoint_input"
            ]
            is False
        ),
        "release_time_and_source_count_are_explicit": (
            artifacts[EngineName.TIMEVAULT].payload["release_time_rule"]
            == "10:00 America/New_York from each dated NRS PDF"
            and artifacts[EngineName.TIMEVAULT].payload["source_evidence_file_count"] == 2
        ),
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_same_release_snapshot_decline": (
            lower == 730_000
            and upper == 765_000
            and known_decline == 35_000
            and shock["bound_construction"]["range_width_units_saar"] == 35_000
            and shock["bound_construction"]["basis_is_single_february_release_snapshot"] is True
            and shock["bound_construction"]["endpoint_method"]
            == "latest_initial_level_persistence_or_repeat_same_release_snapshot_decline"
        ),
        "naive_baseline_is_february_initial_level_persistence": baseline == 765_000,
        "official_sampling_interval_is_not_used": (
            shock["bound_construction"]["official_sampling_interval_used"] is False
        ),
        "no_probability_is_assigned": shock["bound_construction"]["probability_assigned"] is False,
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (event_record_ids & set(compiled.source_record_ids)),
        "post_decision_event_is_exact_nrs_fact": (
            event_record.source.source_id == CENSUS_NRS_SOURCE_ID
            and event_record.source.sha256 == EVENT_PDF_SHA256
            and event_record.entity_id == "census_hud_nrs:new_single_family_houses_sold_us"
            and str(event_record.source.url)
            == "https://www.census.gov/construction/nrs/pdf/newressales_202003.pdf"
            and event_record.interval.published_at == datetime(2020, 4, 23, 14, 0, tzinfo=UTC)
            and event_record.interval.available_at == datetime(2020, 4, 23, 14, 0, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-04-23"
            and event_record.payload.get("reference_month") == "2020-03"
            and event_record.payload.get("metric") == "new_single_family_houses_sold_sa_annual_rate"
            and event_record.payload.get("unit") == "Houses at Seasonally Adjusted Annual Rate"
            and event_record.payload.get("value_units") == 627_000
            and event_record.payload.get("release_pdf_sha256") == EVENT_PDF_SHA256
            and event_record.payload.get("release_pdf_pages") == 5
            and event_record.payload.get("release_timezone_abbreviation") == "EDT"
            and event_record.payload.get("availability_method") == "exact_time_in_pdf"
            and event_record.payload.get("pdf_table_snapshot_verified") is True
            and event_record.payload.get("reported_monthly_change_percent") == "-15.4"
            and event_record.payload.get("reported_monthly_margin_90_percent") == "14.8"
            and event_record.payload.get("reported_monthly_ci_includes_zero") is False
            and event_record.payload.get("reported_monthly_change_significant_at_90_percent")
            is True
            and event_record.payload.get("sale_definition_boundary")
            == "deposit taken or sales agreement signed; may precede permit issuance"
            and event_record.payload.get("covid_publication_standard_statement_present") is True
            and event_level == 627_000
        ),
        "post_decision_revision_remains_later_snapshot_only": (
            event_record.payload.get("prior_month_value_in_previous_release_units") == 765_000
            and event_record.payload.get("prior_month_revised_value_units") == 741_000
            and event_record.payload.get("prior_month_revision_delta_units") == -24_000
            and all(record.payload.get("release_date") != "2020-04-23" for record in lock.records)
            and next(
                record
                for record in lock.records
                if record.payload.get("reference_month") == "2020-02"
            ).payload["value_units"]
            == 765_000
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_level_is_below_declared_range": event_level < lower,
        "post_event_lower_bound_breach_equals_103000_units_saar": lower_breach == 103_000,
        "annualized_sale_and_sampling_boundaries_are_explicit": all(
            record.payload.get("unit") == "Houses at Seasonally Adjusted Annual Rate"
            and record.payload.get("pdf_table_snapshot_verified") is True
            and record.payload.get("reported_monthly_margin_90_percent") is not None
            and str(record.payload.get("sale_definition_boundary", "")).startswith("deposit taken")
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
        raise SystemExit(f"NRS assertions failed: {failed}")

    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines "
            "rebuild the committed directory and deterministic ZIP bytes from two locked "
            "pre-decision Census/HUD NRS records and two exact official PDF hashes. It verifies "
            "that range construction uses revised January 800,000 and initial February 765,000 "
            "SAAR values inside the single March 24 decision snapshot, while January's 764,000 "
            "initial release remains revision lineage only. The separately locked March level "
            "is disjoint and, as evaluation only, is 103,000 units SAAR below the fixed "
            "no-probability lower endpoint; the range is not widened after the fact, and the "
            "April release's -24,000 February revision does not overwrite the decision input. "
            "The official 90-percent sampling interval remains source metadata and the COVID "
            "text proves only a publication-standards statement. This does not prove forecast "
            "skill, calibrated coverage, actual monthly transaction counts, property, builder, "
            "buyer, mortgage or closing outcomes, housing-market, price, pandemic or policy "
            "causality, investment performance, deployment, external review, or user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "post_event_evaluation": {
            "reported_march_sales_units_saar": event_level,
            "february_persistence_baseline_units_saar": baseline,
            "decision_snapshot_revised_january_sales_units_saar": 800_000,
            "known_decision_snapshot_decline_units_saar": known_decline,
            "declared_lower_level_units_saar": lower,
            "declared_upper_level_units_saar": upper,
            "lower_bound_breach_units_saar": lower_breach,
            "event_february_initial_sales_units_saar": 765_000,
            "event_february_revised_sales_units_saar": 741_000,
            "event_february_revision_delta_units_saar": -24_000,
            "range_breached": True,
            "probability_assigned": False,
            "official_sampling_interval_used": False,
            "actual_monthly_transaction_count_claimed": False,
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
        raise SystemExit("NRS receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored NRS receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored NRS semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored NRS semantic receipt differs from fresh rebuild")


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
