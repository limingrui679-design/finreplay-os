#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the Census C30 ReplayPack."""

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
    CENSUS_C30_SOURCE_ID,
    OfficialEventLock,
    build_construction_spending_boundary_replay_spec,
    load_construction_spending_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
INPUT_RESPONSE_SHA256S = {
    "73d0e0ec0216d74255ebcafb316a2081a91b80ef76a34e07f6b31c79d57f9918",
    "a224c4f710f41c610725fe58c88bbf7263a02bfcaaeeab425cc2697cd7461f4d",
    "c212b816fce0823d3e15b01c35d306253bb86280581a3a7d61421ba614dc25bb",
    "566f2267ff69d815ce4bf1ffac6206775d0e3696ea79102352444e051e405579",
}
EVENT_PDF_SHA256 = "f2564dc3940392a43f1dd31e5c5747598f77250522761818f741b2235c8bdfa2"
EVENT_XLSX_SHA256 = "ca96ad398386aae52fb61a9546c4a55d02a82f12d170f63e70307f0f224bac7a"


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
    lock = load_construction_spending_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(
        args.event_lock.read_text(encoding="utf-8")
    )
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match C30 input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match C30 input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match C30 input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-census-c30-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_construction_spending_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_construction_spending_boundary_replay_spec(
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
        raise SystemExit("C30 event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_level = int(event_record.payload["value_million_dollars"])
    variable = "next_total_construction_saar_level_million_dollars"
    baseline = int(shock["naive_baseline"][variable])
    lower = int(shock["bound_construction"]["lower_level_million_dollars"])
    upper = int(shock["bound_construction"]["upper_level_million_dollars"])
    known_decline = int(
        shock["bound_construction"]["known_initial_decline_million_dollars"]
    )
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
        "four_input_response_hashes_are_bound": (
            set(lock.source_response_sha256s)
            == set(compiled.source_hashes)
            == INPUT_RESPONSE_SHA256S
        ),
        "locked_initial_release_pair_is_exact": {
            (
                record.payload.get("release_date"),
                record.payload.get("reference_month"),
                record.payload.get("value_million_dollars"),
                record.payload.get("reported_current_month_change_percent"),
                record.payload.get(
                    "reported_prior_month_revised_total_saar_million_dollars"
                ),
                record.source.sha256,
                record.payload.get("release_xlsx_sha256"),
            )
            for record in lock.records
        }
        == {
            (
                "2020-03-02",
                "2020-01",
                1_369_223,
                "1.8",
                1_345_467,
                "73d0e0ec0216d74255ebcafb316a2081a91b80ef76a34e07f6b31c79d57f9918",
                "a224c4f710f41c610725fe58c88bbf7263a02bfcaaeeab425cc2697cd7461f4d",
            ),
            (
                "2020-04-01",
                "2020-02",
                1_366_697,
                "-1.3",
                1_384_486,
                "c212b816fce0823d3e15b01c35d306253bb86280581a3a7d61421ba614dc25bb",
                "566f2267ff69d815ce4bf1ffac6206775d0e3696ea79102352444e051e405579",
            ),
        },
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_known_initial_decline": (
            lower == 1_364_171
            and upper == 1_366_697
            and known_decline == 2_526
            and shock["bound_construction"]["range_width_million_dollars"] == 2_526
            and shock["bound_construction"]["endpoint_method"]
            == "latest_preliminary_level_persistence_or_repeat_known_initial_decline"
        ),
        "naive_baseline_is_february_preliminary_persistence": baseline == 1_366_697,
        "initial_level_basis_is_not_official_monthly_change": (
            shock["bound_construction"][
                "basis_is_initial_release_levels_not_official_monthly_change"
            ]
            is True
        ),
        "official_sampling_interval_is_not_used": (
            shock["bound_construction"]["official_sampling_confidence_interval_used"]
            is False
        ),
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (
            event_record_ids & set(compiled.source_record_ids)
        ),
        "post_decision_event_is_exact_paired_census_c30_fact": (
            event_record.source.source_id == CENSUS_C30_SOURCE_ID
            and event_record.source.sha256 == EVENT_PDF_SHA256
            and event_record.entity_id == "census_c30:total_construction_value_put_in_place"
            and str(event_record.source.url)
            == "https://www.census.gov/construction/c30/pdf/pr202003.pdf"
            and event_record.interval.published_at
            == datetime(2020, 5, 1, 14, 0, tzinfo=UTC)
            and event_record.interval.available_at
            == datetime(2020, 5, 1, 14, 0, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-05-01"
            and event_record.payload.get("reference_month") == "2020-03"
            and event_record.payload.get("release_number") == "CB20-68"
            and event_record.payload.get("metric")
            == "total_construction_saar_level_million_dollars"
            and event_record.payload.get("unit")
            == "Millions of Dollars at Seasonally Adjusted Annual Rate"
            and event_record.payload.get("value_million_dollars") == 1_360_512
            and event_record.payload.get("reported_current_month_change_percent") == "0.9"
            and event_record.payload.get("reported_current_month_margin_90_percent") == "0.8"
            and event_record.payload.get("reported_year_over_year_change_percent") == "4.7"
            and event_record.payload.get("reported_private_saar_million_dollars")
            == 1_012_543
            and event_record.payload.get("reported_public_saar_million_dollars") == 347_969
            and event_record.payload.get(
                "table2_year_to_date_current_million_dollars"
            )
            == 297_021
            and event_record.payload.get("table3_total_monthly_estimate_cv_percent")
            == "0.7"
            and event_record.payload.get(
                "table3_total_month_to_month_change_standard_error_percent"
            )
            == "0.5"
            and event_record.payload.get("release_pdf_sha256") == EVENT_PDF_SHA256
            and event_record.payload.get("release_xlsx_sha256") == EVENT_XLSX_SHA256
            and event_record.payload.get("release_pdf_pages") == 6
            and event_record.payload.get("release_xlsx_sheet_names")
            == ["Table1", "Table2", "Table3"]
            and event_record.payload.get("release_timezone_abbreviation") == "EDT"
            and event_record.payload.get("availability_method")
            == "exact_time_in_pdf_and_values_crosschecked_to_xlsx"
            and event_record.payload.get("pdf_xlsx_crosscheck_verified") is True
            and event_record.payload.get("covid_publication_standard_statement_present")
            is True
            and event_record.payload.get("future_imputation_revision_notice_present") is True
            and event_level == 1_360_512
        ),
        "post_decision_revisions_remain_later_snapshot_only": (
            event_record.payload.get(
                "release_snapshot_total_construction_saar_million_dollars"
            )
            == {
                "2020-01": 1_382_963,
                "2020-02": 1_348_386,
                "2020-03": 1_360_512,
            }
            and event_record.payload.get(
                "release_snapshot_revision_delta_million_dollars"
            )
            == {"2020-01": -1_523, "2020-02": -18_311, "2020-03": None}
            and all(record.payload.get("release_date") != "2020-05-01" for record in lock.records)
            and next(
                record
                for record in lock.records
                if record.payload.get("reference_month") == "2020-02"
            ).payload["value_million_dollars"]
            == 1_366_697
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_level_is_below_declared_range": event_level < lower,
        "post_event_lower_bound_breach_equals_3659_million_dollars": (
            lower_breach == 3_659
        ),
        "positive_official_monthly_change_uses_revised_denominator": (
            event_record.payload.get("reported_current_month_change_percent") == "0.9"
            and event_record.payload.get(
                "reported_prior_month_revised_total_saar_million_dollars"
            )
            == 1_348_386
            and event_level > 1_348_386
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
        raise SystemExit(f"construction-spending assertions failed: {failed}")
    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines "
            "rebuild the committed directory and deterministic ZIP bytes from two locked "
            "pre-decision Census C30 initial-release Table 1 levels and four paired PDF/XLSX "
            "response hashes. It verifies the separately locked March 2020 level is disjoint "
            "and, as an evaluation only, falls 3,659 million dollars SAAR below the fixed "
            "no-probability range. The range is not widened after the fact, and the May "
            "release's January and February revisions do not overwrite the initial-release "
            "inputs. The 2,526-million-dollar input step is a difference between two initial "
            "current-month levels, not the official monthly change against a revised prior. "
            "The event's official +0.9 percent change is consistent with its revised February "
            "denominator and does not invalidate the separate initial-level evaluation. Census "
            "90-percent sampling intervals are not range inputs. This does not prove forecast "
            "skill, calibrated coverage, real volume, construction, inflation, pandemic, "
            "policy, regional, project, or firm causality, investment performance, deployment, "
            "external review, or user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "post_event_evaluation": {
            "reported_march_level_million_dollars": event_level,
            "february_persistence_baseline_million_dollars": baseline,
            "known_initial_decline_million_dollars": known_decline,
            "declared_lower_level_million_dollars": lower,
            "declared_upper_level_million_dollars": upper,
            "lower_bound_breach_million_dollars": lower_breach,
            "event_february_initial_level_million_dollars": 1_366_697,
            "event_february_revised_level_million_dollars": 1_348_386,
            "event_official_monthly_change_percent": "0.9",
            "range_breached": True,
            "probability_assigned": False,
            "official_sampling_confidence_interval_used": False,
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
        raise SystemExit("construction-spending receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored construction-spending receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored construction-spending semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit(
            "stored construction-spending semantic receipt differs from fresh rebuild"
        )


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
