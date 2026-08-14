#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the FHFA HPI ReplayPack."""

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
    FHFA_HPI_SOURCE_ID,
    OfficialEventLock,
    build_house_price_change_boundary_replay_spec,
    load_house_price_change_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
INPUT_EVIDENCE_SHA256S = {
    "02f589a1d47ef046e87be9391a74f1d6e65fe92cdd552b87ad4144722f67cfba",
    "bc885fac528f66a02a3f0760b81dcace6fe1ef0f0f980aecb5e34c600d239a46",
    "3624bf523c7afa70616e155deb506fe419b756511a0c14a22d1fb3f16b0da993",
}
EVENT_PDF_SHA256 = "9565e868a87df7b80859a62cc1ea3541b1535592d89e1aac1b4982440be5d3c0"


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
    lock = load_house_price_change_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(args.event_lock.read_text(encoding="utf-8"))
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match FHFA HPI input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match FHFA HPI input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match FHFA HPI input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-fhfa-hpi-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_house_price_change_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_house_price_change_boundary_replay_spec(
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
        raise SystemExit("FHFA HPI event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_change = int(event_record.payload["value_basis_points"])
    variable = "next_us_purchase_only_hpi_monthly_change_basis_points"
    baseline = int(shock["naive_baseline"][variable])
    lower = int(shock["bound_construction"]["lower_change_basis_points"])
    upper = int(shock["bound_construction"]["upper_change_basis_points"])
    known_increase = int(shock["bound_construction"]["known_initial_increase_basis_points"])
    lower_breach = lower - event_change
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
        "three_evidence_hashes_are_bound": (
            set(lock.source_evidence_sha256s)
            == set(compiled.source_hashes)
            == INPUT_EVIDENCE_SHA256S
        ),
        "locked_initial_release_pair_is_exact": {
            (
                record.payload.get("release_date"),
                record.payload.get("reference_month"),
                record.payload.get("value_basis_points"),
                record.payload.get("value_percent"),
                record.payload.get("reported_year_over_year_change_basis_points"),
                record.payload.get("report_footer_release_time_label"),
                record.payload.get("report_footer_time_label_differs_from_schedule_wording"),
                record.source.sha256,
            )
            for record in lock.records
        }
        == {
            (
                "2020-03-25",
                "2020-01",
                30,
                "0.3",
                520,
                "9AM EST",
                True,
                "bc885fac528f66a02a3f0760b81dcace6fe1ef0f0f980aecb5e34c600d239a46",
            ),
            (
                "2020-04-22",
                "2020-02",
                70,
                "0.7",
                570,
                "9AM ET",
                False,
                "3624bf523c7afa70616e155deb506fe419b756511a0c14a22d1fb3f16b0da993",
            ),
        },
        "schedule_semantics_are_stable_without_raw_html_claim": (
            artifacts[EngineName.TIMEVAULT].payload["schedule_evidence"]
            == {
                "semantic_sha256": (
                    "02f589a1d47ef046e87be9391a74f1d6e65fe92cdd552b87ad4144722f67cfba"
                ),
                "url": (
                    "https://www.fhfa.gov/news/news-release/"
                    "fhfa-announces-2020-release-dates-for-house-price-index"
                ),
                "release_time_rule": "09:00 America/New_York",
                "raw_html_byte_identity_claimed": False,
            }
        ),
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_known_initial_increase": (
            lower == 70
            and upper == 110
            and known_increase == 40
            and shock["bound_construction"]["range_width_basis_points"] == 40
            and shock["bound_construction"]["endpoint_method"]
            == "latest_initial_change_persistence_or_repeat_known_initial_increase"
        ),
        "naive_baseline_is_february_initial_change_persistence": baseline == 70,
        "official_confidence_interval_is_not_used": (
            shock["bound_construction"]["official_confidence_interval_used"] is False
        ),
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (event_record_ids & set(compiled.source_record_ids)),
        "post_decision_event_is_exact_fhfa_hpi_fact": (
            event_record.source.source_id == FHFA_HPI_SOURCE_ID
            and event_record.source.sha256 == EVENT_PDF_SHA256
            and event_record.entity_id == "fhfa_hpi:us_purchase_only_seasonally_adjusted"
            and str(event_record.source.url)
            == "https://www.fhfa.gov/document/d/hpi/fhfa-house-price-index-report-2020q1"
            and event_record.interval.published_at == datetime(2020, 5, 26, 13, 0, tzinfo=UTC)
            and event_record.interval.available_at == datetime(2020, 5, 26, 13, 0, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-05-26"
            and event_record.payload.get("reference_month") == "2020-03"
            and event_record.payload.get("report_kind") == "quarterly_with_monthly_tables"
            and event_record.payload.get("metric")
            == "us_purchase_only_hpi_monthly_change_basis_points"
            and event_record.payload.get("unit") == "Basis Points of Month-over-Month Price Change"
            and event_record.payload.get("value_basis_points") == 10
            and event_record.payload.get("value_percent") == "0.1"
            and event_record.payload.get("reported_year_over_year_change_basis_points") == 590
            and event_record.payload.get("reported_year_over_year_change_percent") == "5.9"
            and event_record.payload.get("report_pdf_sha256") == EVENT_PDF_SHA256
            and event_record.payload.get("report_pdf_pages") == 28
            and event_record.payload.get("release_timezone_abbreviation") == "EDT"
            and event_record.payload.get("availability_method")
            == "preannounced_2019_schedule_9am_et_and_matching_dated_report"
            and event_record.payload.get("report_table_snapshot_verified") is True
            and event_record.payload.get("report_revision_rows_verified") is True
            and event_record.payload.get("covid_timing_statement_present") is True
            and event_record.payload.get("report_pdf_metadata_modified_after_release") is True
            and event_record.payload.get("report_pdf_metadata_modification_date")
            == "D:20200615174605-04'00'"
            and event_change == 10
        ),
        "post_decision_revisions_remain_later_snapshot_only": (
            event_record.payload.get("release_snapshot_monthly_change_basis_points")
            == {"2020-01": 50, "2020-02": 80, "2020-03": 10}
            and event_record.payload.get("release_snapshot_previous_estimate_basis_points")
            == {"2020-01": 50, "2020-02": 70, "2020-03": None}
            and event_record.payload.get("release_snapshot_revision_delta_basis_points")
            == {"2020-01": 0, "2020-02": 10, "2020-03": None}
            and all(record.payload.get("release_date") != "2020-05-26" for record in lock.records)
            and next(
                record
                for record in lock.records
                if record.payload.get("reference_month") == "2020-02"
            ).payload["value_basis_points"]
            == 70
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_change_is_below_declared_range": event_change < lower,
        "post_event_lower_bound_breach_equals_60_basis_points": lower_breach == 60,
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
        raise SystemExit(f"FHFA HPI assertions failed: {failed}")
    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines "
            "rebuild the committed directory and deterministic ZIP bytes from two locked "
            "pre-decision FHFA HPI first-report national monthly changes, two exact PDF hashes, "
            "and one stable official-schedule semantic hash. It does not claim that today's "
            "schedule HTML bytes are an immutable 2019 snapshot, and it retains the January "
            "report footer's '9AM EST' wording difference. It verifies the separately locked "
            "March change is disjoint and, as an evaluation only, falls 60 basis points below "
            "the fixed no-probability range. The range is not widened after the fact, and the "
            "May report's January and February snapshot values do not overwrite the first-report "
            "inputs. The currently available May report PDF has June 15 modification metadata, "
            "so its exact bytes are not represented as unchanged since May 26. This does not "
            "prove forecast skill, calibrated coverage, universal home prices, property-level "
            "outcomes, a contemporaneous COVID effect, housing, credit, pandemic, policy, "
            "regional, property, or firm causality, investment performance, deployment, external "
            "review, or user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "post_event_evaluation": {
            "reported_march_change_basis_points": event_change,
            "february_persistence_baseline_basis_points": baseline,
            "known_initial_increase_basis_points": known_increase,
            "declared_lower_change_basis_points": lower,
            "declared_upper_change_basis_points": upper,
            "lower_bound_breach_basis_points": lower_breach,
            "event_february_initial_change_basis_points": 70,
            "event_february_revised_change_basis_points": 80,
            "range_breached": True,
            "probability_assigned": False,
            "official_confidence_interval_used": False,
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
        raise SystemExit("FHFA HPI receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored FHFA HPI receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored FHFA HPI semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored FHFA HPI semantic receipt differs from fresh rebuild")


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
