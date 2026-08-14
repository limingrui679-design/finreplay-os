#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the Census M3 durable-goods ReplayPack."""

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
    CENSUS_DURABLE_GOODS_SOURCE_ID,
    OfficialEventLock,
    build_durable_goods_change_boundary_replay_spec,
    load_durable_goods_change_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
INPUT_EVIDENCE_SHA256S = {
    "b58f95a053d07c367f550e4acb0a941cb338869b12ba01d2d9cbd032c4ad38b4",
    "84be58245193913f73c80400b6209328a5d0e3be6daac3c064b47500ac1fbf00",
}
EVENT_PDF_SHA256 = "ffafe420861628e384cbd49e0558157cf7e4b03a608cdcf599d4912e55a816a2"


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
    lock = load_durable_goods_change_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(args.event_lock.read_text(encoding="utf-8"))
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match Census M3 durable-goods input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit(
            "event-lock scenario_version does not match Census M3 durable-goods input lock"
        )
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit(
            "event-lock decision_time does not match Census M3 durable-goods input lock"
        )

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-census-m3-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_durable_goods_change_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_durable_goods_change_boundary_replay_spec(
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
        raise SystemExit("Census M3 durable-goods event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_change = int(event_record.payload["value_basis_points"])
    variable = "next_total_durable_goods_new_orders_change_basis_points"
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
        "two_pdf_hashes_are_bound": (
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
                record.payload.get("value_million_dollars"),
                record.payload.get("release_timezone_abbreviation"),
                record.payload.get("report_pdf_metadata_modified_after_release"),
                record.payload.get("current_pdf_byte_identity_at_release_claimed"),
                record.source.sha256,
            )
            for record in lock.records
        }
        == {
            (
                "2020-02-27",
                "2020-01",
                -20,
                "-0.2",
                246_199,
                "EST",
                True,
                False,
                "b58f95a053d07c367f550e4acb0a941cb338869b12ba01d2d9cbd032c4ad38b4",
            ),
            (
                "2020-03-25",
                "2020-02",
                120,
                "1.2",
                249_409,
                "EDT",
                True,
                False,
                "84be58245193913f73c80400b6209328a5d0e3be6daac3c064b47500ac1fbf00",
            ),
        },
        "release_time_and_current_byte_boundary_are_explicit": (
            artifacts[EngineName.TIMEVAULT].payload["release_time_rule"]
            == "08:30 America/New_York from each dated report"
            and artifacts[EngineName.TIMEVAULT].payload[
                "current_pdf_byte_identity_at_release_claimed"
            ]
            is False
        ),
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_known_initial_increase": (
            lower == 120
            and upper == 260
            and known_increase == 140
            and shock["bound_construction"]["range_width_basis_points"] == 140
            and shock["bound_construction"]["endpoint_method"]
            == "latest_initial_change_persistence_or_repeat_known_initial_increase"
        ),
        "naive_baseline_is_february_initial_change_persistence": baseline == 120,
        "official_confidence_interval_is_not_used": (
            shock["bound_construction"]["official_confidence_interval_used"] is False
        ),
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (event_record_ids & set(compiled.source_record_ids)),
        "post_decision_event_is_exact_census_m3_fact": (
            event_record.source.source_id == CENSUS_DURABLE_GOODS_SOURCE_ID
            and event_record.source.sha256 == EVENT_PDF_SHA256
            and event_record.entity_id == "census_m3:total_durable_goods_new_orders"
            and str(event_record.source.url)
            == (
                "https://www.census.gov/manufacturing/m3/historical_data/"
                "pressreleases/adv/2020/mar20adv.pdf"
            )
            and event_record.interval.published_at == datetime(2020, 4, 24, 12, 30, tzinfo=UTC)
            and event_record.interval.available_at == datetime(2020, 4, 24, 12, 30, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-04-24"
            and event_record.payload.get("reference_month") == "2020-03"
            and event_record.payload.get("metric")
            == "total_durable_goods_new_orders_monthly_change_basis_points"
            and event_record.payload.get("unit")
            == "Basis Points of Month-over-Month New Orders Change"
            and event_record.payload.get("value_basis_points") == -1_440
            and event_record.payload.get("value_percent") == "-14.4"
            and event_record.payload.get("value_million_dollars") == 213_184
            and event_record.payload.get("report_pdf_sha256") == EVENT_PDF_SHA256
            and event_record.payload.get("report_pdf_pages") == 7
            and event_record.payload.get("release_timezone_abbreviation") == "EDT"
            and event_record.payload.get("availability_method")
            == "exact_time_in_report_for_semantic_facts_current_pdf_bytes_retrieval_only"
            and event_record.payload.get("pdf_table_snapshot_verified") is True
            and event_record.payload.get("probability_sample") is False
            and event_record.payload.get("confidence_intervals_computable") is False
            and event_record.payload.get("adjusted_for_price_changes") is False
            and event_record.payload.get("covid_publication_standard_statement_present") is True
            and event_record.payload.get("current_pdf_byte_identity_at_release_claimed") is False
            and event_record.payload.get("report_pdf_metadata_modified_after_release") is True
            and event_record.payload.get("report_pdf_metadata_modification_date")
            == "D:20200527104843-04'00'"
            and event_change == -1_440
        ),
        "post_decision_revisions_remain_later_snapshot_only": (
            event_record.payload.get("release_snapshot_change_basis_points")
            == {"2020-01": 10, "2020-02": 110, "2020-03": -1_440}
            and event_record.payload.get("release_snapshot_previous_change_basis_points")
            == {"2020-01": 10, "2020-02": 120, "2020-03": None}
            and event_record.payload.get("release_snapshot_revision_delta_basis_points")
            == {"2020-01": 0, "2020-02": -10, "2020-03": None}
            and event_record.payload.get("release_snapshot_new_orders_million_dollars")
            == {"2020-01": 246_558, "2020-02": 249_167, "2020-03": 213_184}
            and all(record.payload.get("release_date") != "2020-04-24" for record in lock.records)
            and next(
                record
                for record in lock.records
                if record.payload.get("reference_month") == "2020-02"
            ).payload["value_basis_points"]
            == 120
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_change_is_below_declared_range": event_change < lower,
        "post_event_lower_bound_breach_equals_1560_basis_points": lower_breach == 1_560,
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
        raise SystemExit(f"Census M3 durable-goods assertions failed: {failed}")
    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines "
            "rebuild the committed directory and deterministic ZIP bytes from two locked "
            "pre-decision Census M3 durable-goods first-report total new-orders monthly changes "
            "and two exact current official PDF hashes. All current PDFs have post-release "
            "modification metadata, so no release-time byte identity is claimed. It verifies "
            "the separately locked March change is disjoint and, as an evaluation only, falls "
            "1,560 basis points below the fixed no-probability range. The range is not widened "
            "after the fact, and the April report's January and February snapshot values do not "
            "overwrite the first-report inputs. The COVID-19 text proves only a publication-"
            "standards statement. This does not prove forecast skill, calibrated coverage, "
            "statistical significance, price-adjusted output, a contemporaneous COVID effect, "
            "manufacturing, inflation, pandemic, policy, sector, regional, or firm causality, "
            "investment performance, deployment, external review, or user impact."
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
            "event_march_level_million_dollars": 213_184,
            "event_february_initial_change_basis_points": 120,
            "event_february_revised_change_basis_points": 110,
            "range_breached": True,
            "probability_assigned": False,
            "official_confidence_interval_used": False,
            "current_pdf_byte_identity_at_release_claimed": False,
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
        raise SystemExit("Census M3 durable-goods receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored Census M3 durable-goods receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored Census M3 durable-goods semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit(
            "stored Census M3 durable-goods semantic receipt differs from fresh rebuild"
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
