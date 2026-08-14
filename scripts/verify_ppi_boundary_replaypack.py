#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the paired-format BLS PPI ReplayPack."""

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
    BLS_PPI_SOURCE_ID,
    OfficialEventLock,
    build_ppi_boundary_replay_spec,
    load_ppi_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
INPUT_RESPONSE_SHA256S = {
    "515855b318616035f7d4a9d06672f90636f3ec3e424a630a0eb6076167573ca2",
    "392c9ee30d9deae5007a796917f8c332ecbc617e947a61b38562d67fc86c96b2",
    "318dafbdf942ea9ac3157e4369de66cc11f09994f7ff8d07de3c159cd9d3f9ec",
    "18540697b82c4cbb42703f24a44d808661bf2baf8883b135a2c1a385c1c6d7fb",
}
EVENT_HTML_SHA256 = "f26f413c1b8aa505baaa25b995ce0ce69f280b6c30bf08b645dc24f0fdce9900"
EVENT_PDF_SHA256 = "eda79108129061e29ebccc1b26bce97df55326d66d4bb01855a9fdbafc8b067c"
EVENT_PAYLOAD_SHA256 = "d7bf2bc8cbe933f7a68291623761be9d9fd491bcfb0647a6e74c7ac84827cb54"
PPI_MEASUREMENT_BOUNDARY = (
    "average change over time in prices received by domestic producers; seller perspective"
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
    lock = load_ppi_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(
        args.event_lock.read_text(encoding="utf-8")
    )
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match BLS PPI input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match BLS PPI input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match BLS PPI input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-bls-ppi-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_ppi_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_ppi_boundary_replay_spec(
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
        raise SystemExit("BLS PPI event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_change = int(event_record.payload["value_basis_points"])
    variable = "next_final_demand_monthly_change_basis_points"
    baseline = int(shock["naive_baseline"][variable])
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
        "four_official_html_pdf_hashes_are_bound": (
            set(lock.source_response_sha256s)
            == set(compiled.source_hashes)
            == INPUT_RESPONSE_SHA256S
        ),
        "locked_release_pair_is_exact": {
            (
                record.payload.get("release_date"),
                record.payload.get("reference_month"),
                record.payload.get("value_basis_points"),
                record.payload.get("prior_month_change_tenths_percent"),
                record.payload.get("prior_month_revision_delta_tenths_percent"),
                record.payload.get("year_over_year_change_tenths_percent"),
                record.payload.get("release_pdf_pages"),
                record.interval.available_at,
                record.source.sha256,
                record.payload.get("release_html_sha256"),
            )
            for record in lock.records
        }
        == {
            (
                "2020-03-12",
                "2020-02",
                -60,
                5,
                None,
                13,
                32,
                datetime(2020, 3, 12, 12, 30, tzinfo=UTC),
                "392c9ee30d9deae5007a796917f8c332ecbc617e947a61b38562d67fc86c96b2",
                "515855b318616035f7d4a9d06672f90636f3ec3e424a630a0eb6076167573ca2",
            ),
            (
                "2020-04-09",
                "2020-03",
                -20,
                -6,
                0,
                7,
                31,
                datetime(2020, 4, 9, 12, 30, tzinfo=UTC),
                "18540697b82c4cbb42703f24a44d808661bf2baf8883b135a2c1a385c1c6d7fb",
                "318dafbdf942ea9ac3157e4369de66cc11f09994f7ff8d07de3c159cd9d3f9ec",
            ),
        },
        "paired_html_pdf_crosscheck_is_explicit": (
            timevault["html_pdf_crosscheck_verified"] is True
            and timevault["source_response_file_count"] == 4
        ),
        "adjacent_february_value_is_unchanged": (
            timevault["adjacent_prior_value_crosscheck_verified"] is True
            and next(
                record
                for record in lock.records
                if record.payload.get("reference_month") == "2020-03"
            ).payload["prior_month_revision_delta_tenths_percent"]
            == 0
        ),
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_one_known_increase": (
            lower == -20
            and upper == 20
            and known_increase == 40
            and shock["bound_construction"]["range_width_basis_points"] == 40
            and shock["bound_construction"]["original_release_values_only"] is True
            and shock["bound_construction"]["endpoint_method"]
            == "latest_change_persistence_or_repeat_one_known_increase"
        ),
        "naive_baseline_is_march_change_persistence": baseline == -20,
        "source_auxiliary_measures_are_not_used": (
            shock["bound_construction"]["source_auxiliary_measures_used"] is False
            and timevault["source_auxiliary_measures_used_as_range_input"] is False
        ),
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": not (
            event_record_ids & set(compiled.source_record_ids)
        ),
        "post_decision_event_is_exact_ppi_fact": (
            event_record.source.source_id == BLS_PPI_SOURCE_ID
            and event_record.source.sha256 == EVENT_PDF_SHA256
            and event_record.payload.get("release_html_sha256") == EVENT_HTML_SHA256
            and event_record.entity_id == "bls_ppi:final_demand_united_states"
            and str(event_record.source.url)
            == "https://www.bls.gov/news.release/archives/ppi_05132020.pdf"
            and event_record.payload.get("release_html_url")
            == "https://www.bls.gov/news.release/archives/ppi_05132020.htm"
            and event_record.interval.published_at
            == datetime(2020, 5, 13, 12, 30, tzinfo=UTC)
            and event_record.interval.available_at
            == datetime(2020, 5, 13, 12, 30, tzinfo=UTC)
            and event_record.interval.valid_from
            == datetime(2020, 4, 1, 0, 0, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-05-13"
            and event_record.payload.get("reference_month") == "2020-04"
            and event_record.payload.get("release_number") == "USDL 20-0920"
            and event_record.payload.get("metric")
            == "final_demand_monthly_change_seasonally_adjusted"
            and event_record.payload.get("unit") == "Tenths of a Percent"
            and event_record.payload.get("value_tenths_percent") == -13
            and event_record.payload.get("value_basis_points") == -130
            and event_record.payload.get("prior_month_change_tenths_percent") == -2
            and event_record.payload.get("prior_month_revision_delta_tenths_percent") == 0
            and event_record.payload.get("year_over_year_change_tenths_percent") == -12
            and event_record.payload.get("release_pdf_sha256") == EVENT_PDF_SHA256
            and event_record.payload.get("release_pdf_pages") == 31
            and event_record.payload.get("html_pdf_crosscheck_verified") is True
            and event_record.payload.get("availability_method")
            == "exact_bls_embargo_end_crosschecked_html_pdf"
            and _hash(event_record.payload) == EVENT_PAYLOAD_SHA256
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_change_is_below_declared_range": event_change < lower,
        "post_event_lower_bound_breach_equals_110_basis_points": lower_breach == 110,
        "post_decision_prior_march_value_remains_unchanged": (
            event_record.payload.get("prior_month_value_in_previous_release_tenths_percent")
            == -2
            and event_record.payload.get("prior_month_change_tenths_percent") == -2
            and event_record.payload.get("prior_month_revision_delta_tenths_percent") == 0
        ),
        "ppi_measurement_and_revision_boundaries_are_explicit": all(
            record.payload.get("ppi_measurement_boundary") == PPI_MEASUREMENT_BOUNDARY
            and record.payload.get("revision_window_months") == 4
            and record.payload.get("html_pdf_crosscheck_verified") is True
            for record in (*lock.records, *event_lock.records)
        ),
        "covid_methodology_text_is_retained_without_causal_claim": (
            next(
                record
                for record in lock.records
                if record.payload.get("reference_month") == "2020-03"
            ).payload.get("covid_methodology_statement_present")
            is True
            and event_record.payload.get("covid_methodology_statement_present") is True
            and "causal model" in compiled.spec.claim_boundary
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
        raise SystemExit(f"BLS PPI assertions failed: {failed}")

    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines "
            "rebuild the committed directory and deterministic ZIP bytes from two locked "
            "pre-decision BLS PPI release records and four exact official HTML/PDF hashes. "
            "It verifies exact 8:30 a.m. EDT embargo timing, paired-format cross-checks, an "
            "unchanged February prior value in the March release, a March-change persistence "
            "baseline, and a no-probability -20-to-20-basis-point stress range. The separately "
            "locked April event is disjoint and, as evaluation only, is 110 basis points below "
            "the fixed lower endpoint; the range is not widened after the fact. PPI remains an "
            "aggregate seller-price measure subject to revision, and the retained COVID-19 "
            "methodology text is not causal evidence. This does not prove forecast skill, "
            "calibrated coverage, consumer prices, producer or product behavior, transactions, "
            "quantity, revenue, profit, pandemic or market causality, investment performance, "
            "external validation, deployment, or user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "post_event_evaluation": {
            "reported_april_change_basis_points": event_change,
            "march_persistence_baseline_basis_points": baseline,
            "known_february_to_march_increase_basis_points": known_increase,
            "declared_lower_change_basis_points": lower,
            "declared_upper_change_basis_points": upper,
            "lower_bound_breach_basis_points": lower_breach,
            "reported_prior_march_change_basis_points": -20,
            "reported_prior_march_revision_basis_points": 0,
            "range_breached": True,
            "probability_assigned": False,
            "source_auxiliary_measures_used": False,
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
        raise SystemExit("BLS PPI receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored BLS PPI receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored BLS PPI semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored BLS PPI semantic receipt differs from fresh rebuild")


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
