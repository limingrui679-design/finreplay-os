#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the BLS export-price ReplayPack."""

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
    BLS_EXPORT_PRICE_INPUT_RESPONSE_SHA256S,
    BLS_EXPORT_PRICE_SOURCE_ID,
    BLS_EXPORT_PRICE_SUPPORTING_RECEIPT_SHA256,
    OfficialEventLock,
    build_export_price_boundary_replay_spec,
    load_export_price_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
INPUT_RESPONSE_SHA256S = set(BLS_EXPORT_PRICE_INPUT_RESPONSE_SHA256S)
EVENT_PDF_SHA256 = "215974814451294a33cfae984599752e5c9c5d1dc0e432031d8d49b484b6e382"
EVENT_HTML_SHA256 = "b5433f3a694f72261a14801e922459eb74cebea96c00ae1f6b2610ce5e786ae5"
EVENT_PAYLOAD_SHA256 = "ad92cc62eca28d441feb8d0d326eea8042f7f1ef6d4a09c2b75cee69fd00b716"
MEASUREMENT_BOUNDARY = (
    "U.S. export transaction prices, generally f.a.s. factory or f.o.b., classified "
    "under Schedule B and aggregated as a modified Laspeyres price index"
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
    lock = load_export_price_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(args.event_lock.read_text(encoding="utf-8"))
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match export-price input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match export-price input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match export-price input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-bls-export-price-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_export_price_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_export_price_boundary_replay_spec(
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
        raise SystemExit("export-price event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_change = int(event_record.payload["value_basis_points"])
    variable = "next_all_exports_monthly_change_basis_points"
    baseline = int(shock["naive_baseline"][variable])
    lower = int(shock["bound_construction"]["lower_change_basis_points"])
    upper = int(shock["bound_construction"]["upper_change_basis_points"])
    known_decline = int(shock["bound_construction"]["known_decline_basis_points"])
    distance_above_lower = event_change - lower
    distance_below_upper = upper - event_change
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
        "idempotent_supporting_receipt_is_bound": (
            lock.supporting_receipt_sha256 == BLS_EXPORT_PRICE_SUPPORTING_RECEIPT_SHA256
            and timevault["supporting_receipt_sha256"] == BLS_EXPORT_PRICE_SUPPORTING_RECEIPT_SHA256
        ),
        "locked_release_pair_is_exact": {
            (
                record.payload.get("release_date"),
                record.payload.get("reference_month"),
                record.payload.get("value_basis_points"),
                record.payload.get("prior_month_change_tenths_percent"),
                record.payload.get("prior_month_value_in_previous_release_tenths_percent"),
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
                "2020-02-14",
                "2020-01",
                70,
                -2,
                None,
                None,
                5,
                18,
                datetime(2020, 2, 14, 13, 30, tzinfo=UTC),
                "186c6a60276ac896bdf37e1db97e7c6a313dd5e2cd2087e592b2ae8a76323327",
                "dcac2c1daecc12c2bce0769999b467e25b4a4c6dea66af3538feb88fe72247ce",
            ),
            (
                "2020-03-13",
                "2020-02",
                -110,
                6,
                7,
                -1,
                -13,
                18,
                datetime(2020, 3, 13, 12, 30, tzinfo=UTC),
                "e0167a9ec66bc0b884d0f58c5e7de42ddc8fd849f150bf438f9590f4be7fbbf9",
                "1b196f0ebed0fdd41d27a7696f956a5e962b1178b0687eade2ce06f845db15ae",
            ),
        },
        "paired_html_pdf_crosscheck_is_explicit": (
            timevault["html_pdf_crosscheck_verified"] is True
            and timevault["source_response_file_count"] == 4
            and all(
                record.payload.get("html_pdf_crosscheck_verified") is True
                for record in lock.records
            )
        ),
        "official_input_release_times_are_exact": all(
            record.interval.availability_confidence == 1.0
            and record.payload.get("availability_method")
            == "exact_bls_embargo_end_crosschecked_html_pdf"
            and record.payload.get("official_release_at")
            == record.interval.available_at.isoformat()
            for record in lock.records
        ),
        "adjacent_january_revision_is_retained": (
            timevault["adjacent_prior_value_crosscheck_verified"] is True
            and timevault["decision_observations_basis_points"][
                "january_revision_delta_basis_points"
            ]
            == -10
        ),
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_one_known_decline": (
            lower == -290
            and upper == -110
            and known_decline == 180
            and shock["bound_construction"]["range_width_basis_points"] == 180
            and shock["bound_construction"]["original_release_values_only"] is True
            and shock["bound_construction"]["endpoint_method"]
            == "latest_change_persistence_or_repeat_one_known_decline"
        ),
        "naive_baseline_is_february_change_persistence": baseline == -110,
        "auxiliary_and_revision_fields_are_not_used": (
            shock["bound_construction"]["source_auxiliary_measures_used"] is False
            and shock["bound_construction"]["prior_revision_used_as_endpoint"] is False
            and timevault["source_auxiliary_measures_used_as_range_input"] is False
        ),
        "no_probability_is_assigned": (
            shock["bound_construction"]["probability_assigned"] is False
        ),
        "only_relevant_engines_present": set(artifacts) == RELEVANT_ENGINES,
        "post_decision_event_is_disjoint": event_record.record_id
        not in set(compiled.source_record_ids),
        "post_decision_event_is_exact_export_price_fact": (
            event_record.source.source_id == BLS_EXPORT_PRICE_SOURCE_ID
            and event_record.source.sha256 == EVENT_PDF_SHA256
            and event_record.payload.get("release_html_sha256") == EVENT_HTML_SHA256
            and event_record.entity_id == "bls_export_price_index:all_exports_united_states"
            and str(event_record.source.url)
            == "https://www.bls.gov/news.release/archives/ximpim_04142020.pdf"
            and event_record.payload.get("release_html_url")
            == "https://www.bls.gov/news.release/archives/ximpim_04142020.htm"
            and event_record.interval.published_at == datetime(2020, 4, 14, 12, 30, tzinfo=UTC)
            and event_record.interval.available_at == datetime(2020, 4, 14, 12, 30, tzinfo=UTC)
            and event_record.interval.valid_from == datetime(2020, 3, 1, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-04-14"
            and event_record.payload.get("reference_month") == "2020-03"
            and event_record.payload.get("release_number") == "USDL-20-0610"
            and event_record.payload.get("metric")
            == "all_exports_monthly_change_not_seasonally_adjusted"
            and event_record.payload.get("unit") == "Tenths of a Percent"
            and event_record.payload.get("value_tenths_percent") == -16
            and event_record.payload.get("value_basis_points") == -160
            and event_record.payload.get("prior_month_change_tenths_percent") == -11
            and event_record.payload.get("prior_month_value_in_previous_release_tenths_percent")
            == -11
            and event_record.payload.get("prior_month_revision_delta_tenths_percent") == 0
            and event_record.payload.get("second_prior_month_change_tenths_percent") == 6
            and event_record.payload.get("year_over_year_change_tenths_percent") == -36
            and event_record.payload.get("release_pdf_sha256") == EVENT_PDF_SHA256
            and event_record.payload.get("release_pdf_pages") == 18
            and event_record.payload.get("html_pdf_crosscheck_verified") is True
            and event_record.payload.get("availability_method")
            == "exact_bls_embargo_end_crosschecked_html_pdf"
            and _hash(event_record.payload) == EVENT_PAYLOAD_SHA256
        ),
        "post_decision_event_timing_is_later": (
            event_record.interval.available_at > lock.decision_time
        ),
        "reported_post_event_change_is_inside_declared_range": (lower <= event_change <= upper),
        "post_event_lower_distance_equals_130_basis_points": (distance_above_lower == 130),
        "post_event_upper_distance_equals_50_basis_points": (distance_below_upper == 50),
        "fixed_range_is_not_changed_after_event": (
            shock["bound_construction"]["lower_change_basis_points"] == -290
            and shock["bound_construction"]["upper_change_basis_points"] == -110
            and event_change not in {lower, upper}
        ),
        "range_inclusion_remains_evaluation_without_success_claim": (
            "does not become forecast success" in event_lock.claim_boundary
            and "not a BLS forecast" in compiled.spec.claim_boundary
        ),
        "measurement_and_revision_boundaries_are_explicit": all(
            record.payload.get("measurement_boundary") == MEASUREMENT_BOUNDARY
            and record.payload.get("revision_window_months") == 3
            and record.payload.get("seasonally_adjusted") is False
            and record.payload.get("html_pdf_crosscheck_verified") is True
            for record in (*lock.records, *event_lock.records)
        ),
        "covid_methodology_text_is_not_promoted_to_causality": (
            event_record.payload.get("covid_methodology_statement_present") is True
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
        raise SystemExit(f"export-price assertions failed: {failed}")

    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines "
            "rebuild the committed directory and deterministic ZIP bytes from two locked "
            "pre-decision BLS all-export records and four exact official HTML/PDF input hashes. "
            "It binds the idempotent six-response supporting receipt, paired-format checks, "
            "exact 8:30 a.m. EST/EDT release times, the retained -10-basis-point January "
            "revision, a February -110-basis-point persistence baseline, and a no-probability "
            "-290-to--110-basis-point stress range built only from first reports. The separately "
            "locked March event is disjoint and its -160-basis-point change lies inside the "
            "fixed range, 130 basis points above the lower endpoint and 50 below the upper. "
            "That labelled inclusion remains post-event evaluation, does not become forecast "
            "success, and does not change the range after the fact. The modified-Laspeyres, "
            "export-transaction-price, non-seasonally-adjusted, three-release revision, and "
            "COVID-methodology boundaries are retained. This does not prove an official or "
            "calibrated interval, export quantity or nominal export value, tariff or PPI effect, "
            "firm result, P&L, pandemic causality, investment performance, external "
            "validation, deployment, or user impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "supporting_receipt_sha256": lock.supporting_receipt_sha256,
        "post_event_evaluation": {
            "reported_march_change_basis_points": event_change,
            "february_persistence_baseline_basis_points": baseline,
            "known_january_to_february_decline_basis_points": known_decline,
            "declared_lower_change_basis_points": lower,
            "declared_upper_change_basis_points": upper,
            "distance_above_lower_basis_points": distance_above_lower,
            "distance_below_upper_basis_points": distance_below_upper,
            "inside_declared_range": True,
            "forecast_success_claimed": False,
            "range_changed_after_event": False,
            "probability_assigned": False,
            "auxiliary_measures_used_as_range_input": False,
            "prior_revision_used_as_endpoint": False,
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
        raise SystemExit("export-price receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored export-price receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored export-price semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored export-price semantic receipt differs from fresh rebuild")


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
