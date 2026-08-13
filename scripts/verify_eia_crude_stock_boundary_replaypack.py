#!/usr/bin/env python3
"""Fresh-run relevant engines and byte-compare the April 2020 EIA stock ReplayPack."""

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
    EIA_WPSR_SOURCE_ID,
    OfficialEventLock,
    build_eia_crude_stock_boundary_replay_spec,
    load_eia_crude_stock_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
EVENT_CSV_SHA256 = "20f6d312f4e2459863ebe8ce5f3d7fd09ed2932cd6b4cf0bd6ff72d39e7f1d60"
EVENT_PDF_SHA256 = "ab757d2d952fdc82426bea5606c5229335984fefab97f330ee4a83cf039ad658"


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
    lock = load_eia_crude_stock_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(args.event_lock.read_text(encoding="utf-8"))
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match EIA crude-stock input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match EIA crude-stock input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match EIA crude-stock input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-eia-crude-stock-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_eia_crude_stock_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_eia_crude_stock_boundary_replay_spec(
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
        raise SystemExit("EIA crude-stock event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_stock = int(event_record.payload["value_thousand_barrels"])
    baseline_stock = int(
        shock["naive_baseline"][
            "next_reported_commercial_crude_stocks_thousand_barrels"
        ]
    )
    lower = int(shock["bound_construction"]["lower_stock_thousand_barrels"])
    upper = int(shock["bound_construction"]["upper_stock_thousand_barrels"])
    breach = event_stock - upper
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
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "naive_baseline_is_latest_known_eia_stock_persistence": baseline_stock == 503_618,
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (
            event_record_ids & set(compiled.source_record_ids)
        ),
        "post_decision_event_is_exact_archived_eia_wpsr_fact": (
            event_record.source.source_id == EIA_WPSR_SOURCE_ID
            and event_record.source.sha256 == EVENT_CSV_SHA256
            and event_record.entity_id
            == "eia_series:weekly_us_commercial_crude_stocks_excluding_spr"
            and event_record.payload.get("release_date") == "2020-04-22"
            and event_record.payload.get("week_ending") == "2020-04-17"
            and event_record.payload.get("prior_week_ending") == "2020-04-10"
            and event_record.payload.get("metric")
            == "commercial_crude_stocks_excluding_spr"
            and event_record.payload.get("unit") == "Thousand Barrels"
            and event_record.payload.get("table") == "Weekly Petroleum Status Report Table 4"
            and event_record.payload.get("arithmetic_verified") is True
            and event_record.payload.get("release_pdf_sha256") == EVENT_PDF_SHA256
            and event_record.payload.get("prior_value_thousand_barrels") == 503_618
            and event_record.payload.get("reported_difference_thousand_barrels") == 15_022
            and event_stock == 518_640
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_stock_breaches_declared_range": event_stock > upper,
        "post_event_upper_bound_breach_equals_15022_thousand_barrels": breach == 15_022,
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
        raise SystemExit(f"EIA crude-stock assertions failed: {failed}")
    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines rebuild "
            "the committed directory and deterministic ZIP bytes from two locked pre-decision "
            "archived EIA WPSR commercial-crude-stock facts. It verifies the separately locked "
            "April 17 stock is disjoint and, as an evaluation only, breaches the previously "
            "declared no-probability range by 15,022 thousand barrels. The range is not widened "
            "after the fact. This does not prove forecast skill, calibrated coverage, storage-"
            "capacity limits, pandemic or oil-market causality, policy effectiveness, deployment, "
            "external review, or user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "post_event_evaluation": {
            "reported_april17_stock_thousand_barrels": event_stock,
            "latest_known_persistence_baseline_thousand_barrels": baseline_stock,
            "declared_lower_stock_thousand_barrels": lower,
            "declared_upper_stock_thousand_barrels": upper,
            "upper_bound_breach_thousand_barrels": breach,
            "range_breached": True,
            "probability_assigned": False,
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
        raise SystemExit("EIA crude-stock receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored EIA crude-stock receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored EIA crude-stock semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored EIA crude-stock semantic receipt differs from fresh rebuild")


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
