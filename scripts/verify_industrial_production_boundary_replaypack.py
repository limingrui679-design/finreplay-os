#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the Federal Reserve G.17 ReplayPack."""

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
    FED_G17_SOURCE_ID,
    OfficialEventLock,
    build_industrial_production_boundary_replay_spec,
    load_industrial_production_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
EVENT_HTML_FACT_SHA256 = "d3275c766873dfa8cc1c3e4a17f9d3018cf13c6390119ceecb7c8b90a274c0a3"
EVENT_PDF_SHA256 = "b3d2cfdb733546959552b7cd637311ccea0db0b8d6514db5c523cfdacb61af05"


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
    lock = load_industrial_production_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(args.event_lock.read_text(encoding="utf-8"))
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match G.17 input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match G.17 input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match G.17 input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-fed-g17-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_industrial_production_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_industrial_production_boundary_replay_spec(
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
        raise SystemExit("G.17 event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_change = int(event_record.payload["value_basis_points"])
    baseline_change = int(shock["naive_baseline"]["next_monthly_change_basis_points"])
    lower = int(shock["bound_construction"]["lower_change_basis_points"])
    upper = int(shock["bound_construction"]["upper_change_basis_points"])
    known_increase = int(shock["bound_construction"]["known_increase_basis_points"])
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
        "locked_pre_decision_release_pair_is_exact": {
            (
                record.payload.get("reference_month"),
                record.payload.get("value_basis_points"),
                record.source.sha256,
                record.payload.get("release_html_fact_sha256"),
            )
            for record in lock.records
        }
        == {
            (
                "2020-01",
                -30,
                "8388882993acfc4215eb045d4d925cc313807fecf3a3a36694776dbb2f5e2a46",
                "d0c65fdb77aefeb259d1dc7490d1296725514eb469daee264ceaa68d2cf03fbb",
            ),
            (
                "2020-02",
                60,
                "6c388e9c67329d42fc624a9c5e0b930d11b68cbb68e1a53b582f72e126add25e",
                "ea2598ae3a5e1dba2625022eb30257ca17cdb3916ea75f322cb8dae6c668d902",
            ),
        },
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_known_monthly_increase": (
            lower == 60
            and upper == 150
            and known_increase == 90
            and shock["bound_construction"]["range_width_basis_points"] == 90
            and shock["bound_construction"]["endpoint_method"]
            == "latest_persistence_or_repeat_known_monthly_increase"
        ),
        "naive_baseline_is_latest_known_monthly_change_persistence": baseline_change == 60,
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (event_record_ids & set(compiled.source_record_ids)),
        "post_decision_event_is_exact_archived_fed_g17_fact": (
            event_record.source.source_id == FED_G17_SOURCE_ID
            and event_record.source.sha256 == EVENT_PDF_SHA256
            and event_record.entity_id == "federal_reserve_g17:total_industrial_production"
            and str(event_record.source.url)
            == "https://www.federalreserve.gov/releases/g17/20200415/g17.pdf"
            and event_record.interval.published_at == datetime(2020, 4, 15, 13, 15, tzinfo=UTC)
            and event_record.interval.available_at == datetime(2020, 4, 15, 13, 15, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-04-15"
            and event_record.payload.get("reference_month") == "2020-03"
            and event_record.payload.get("release_series") == "G.17 (419)"
            and event_record.payload.get("metric") == "total_industrial_production_monthly_change"
            and event_record.payload.get("unit") == "Basis Points"
            and event_record.payload.get("reported_monthly_change_percent") == "-5.4"
            and event_record.payload.get("total_index_2012_equals_100") == "103.7"
            and event_record.payload.get("capacity_utilization_percent") == "72.7"
            and event_record.payload.get("manufacturing_monthly_change_percent") == "-6.3"
            and event_record.payload.get("mining_monthly_change_percent") == "-2.0"
            and event_record.payload.get("utilities_monthly_change_percent") == "-3.9"
            and event_record.payload.get("year_over_year_change_percent") == "-5.5"
            and event_record.payload.get("release_html_fact_sha256") == EVENT_HTML_FACT_SHA256
            and event_record.payload.get("release_html_url")
            == "https://www.federalreserve.gov/releases/g17/20200415/default.htm"
            and event_record.payload.get("release_pdf_sha256") == EVENT_PDF_SHA256
            and event_record.payload.get("release_pdf_pages") == 19
            and event_record.payload.get("release_timezone_abbreviation") == "EDT"
            and event_record.payload.get("availability_method")
            == "exact_time_in_pdf_and_release_date_in_both_forms"
            and event_record.payload.get("html_pdf_crosscheck_verified") is True
            and event_record.payload.get("summary_table_snapshot_verified") is True
            and event_change == -540
        ),
        "post_decision_february_revision_remains_later_snapshot_only": (
            event_record.payload.get("prior_month_change_in_current_release_basis_points") == 50
            and event_record.payload.get("prior_month_change_in_previous_release_basis_points")
            == 60
            and event_record.payload.get("prior_month_revision_delta_basis_points") == -10
            and all(record.payload.get("release_date") != "2020-04-15" for record in lock.records)
            and next(
                record
                for record in lock.records
                if record.payload.get("reference_month") == "2020-02"
            ).payload["value_basis_points"]
            == 60
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_change_is_below_declared_range": event_change < lower,
        "post_event_lower_bound_breach_equals_600_basis_points": lower_breach == 600,
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
        raise SystemExit(f"G.17 industrial-production assertions failed: {failed}")
    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines rebuild "
            "the committed directory and deterministic ZIP bytes from two locked pre-decision "
            "archived Federal Reserve G.17 release facts. It verifies the separately locked "
            "March 2020 change is disjoint and, as an evaluation only, falls 600 basis points "
            "below the previously declared no-probability range. The range is not widened after "
            "the fact, and the April release's revision of February from 0.6 to 0.5 percent does "
            "not overwrite the decision snapshot. This does not prove forecast skill, calibrated "
            "coverage, industrial or pandemic causality, investment performance, deployment, "
            "external review, or user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "post_event_evaluation": {
            "reported_march_change_basis_points": event_change,
            "latest_known_persistence_baseline_basis_points": baseline_change,
            "known_monthly_increase_basis_points": known_increase,
            "declared_lower_change_basis_points": lower,
            "declared_upper_change_basis_points": upper,
            "lower_bound_breach_basis_points": lower_breach,
            "event_february_previous_snapshot_basis_points": 60,
            "event_february_revised_basis_points": 50,
            "range_breached": True,
            "probability_assigned": False,
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
        raise SystemExit("G.17 receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored G.17 receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored G.17 semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored G.17 semantic receipt differs from fresh rebuild")


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
