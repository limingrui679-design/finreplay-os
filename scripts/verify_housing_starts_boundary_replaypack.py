#!/usr/bin/env python3
"""Fresh-run four engines and byte-compare the Census/HUD NRC ReplayPack."""

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
    CENSUS_NRC_SOURCE_ID,
    OfficialEventLock,
    build_housing_starts_boundary_replay_spec,
    load_housing_starts_boundary_input_lock,
)

RELEVANT_ENGINES = {
    EngineName.TIMEVAULT,
    EngineName.SHOCKCOMPILER,
    EngineName.TRIALCOURT,
    EngineName.REPLAYSTUDIO,
}
EVENT_PDF_SHA256 = "8adcf97272f7ee0e14c3e71dde9d121db72100499da0079870eec76e4036eb51"


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
    lock = load_housing_starts_boundary_input_lock(args.input_lock)
    event_lock = OfficialEventLock.model_validate_json(
        args.event_lock.read_text(encoding="utf-8")
    )
    if event_lock.scenario_id != lock.scenario_id:
        raise SystemExit("event-lock scenario_id does not match housing-starts input lock")
    if event_lock.scenario_version != lock.scenario_version:
        raise SystemExit("event-lock scenario_version does not match housing-starts input lock")
    if event_lock.decision_time != lock.decision_time:
        raise SystemExit("event-lock decision_time does not match housing-starts input lock")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="finreplay-census-nrc-boundary-") as temporary:
        temporary_root = Path(temporary)
        first_spec = build_housing_starts_boundary_replay_spec(
            lock,
            code_commit=compiled.spec.code_commit,
        )
        first_root = studio.build(first_spec, temporary_root / "first").root
        second_spec = build_housing_starts_boundary_replay_spec(
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
        raise SystemExit("housing-starts event lock must contain exactly one record")
    event_record = event_lock.records[0]
    event_record_ids = {event_record.record_id}
    event_level = int(event_record.payload["value_units"])
    baseline = int(shock["naive_baseline"]["next_total_housing_starts_saar_units"])
    lower = int(shock["bound_construction"]["lower_level_units"])
    upper = int(shock["bound_construction"]["upper_level_units"])
    known_increase = int(shock["bound_construction"]["known_headline_increase_units"])
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
        "locked_pre_decision_release_pair_is_exact": {
            (
                record.payload.get("reference_month"),
                record.payload.get("value_units"),
                record.payload.get("reported_monthly_change_percent"),
                record.payload.get("prior_month_revised_value_units"),
                record.source.sha256,
            )
            for record in lock.records
        }
        == {
            (
                "2020-01",
                1_567_000,
                "-3.6",
                1_626_000,
                "7aaddc9c7a6bf3655aad1bbcaa4f3a21047187115625626e94abb38ccdec191e",
            ),
            (
                "2020-02",
                1_599_000,
                "-1.5",
                1_624_000,
                "20042627dcaa63068a5bfd271f1fbb2880de0792103cf2fe0c084759f28f6a3e",
            ),
        },
        "future_event_excluded_from_bound_construction": (
            shock["bound_construction"]["future_event_used"] is False
        ),
        "historical_source_sets_eligible": compiled.source_set_historical_replay_eligible,
        "declared_range_reuses_only_known_headline_increase": (
            lower == 1_599_000
            and upper == 1_631_000
            and known_increase == 32_000
            and shock["bound_construction"]["range_width_units"] == 32_000
            and shock["bound_construction"]["endpoint_method"]
            == "latest_headline_persistence_or_repeat_known_headline_increase"
        ),
        "naive_baseline_is_latest_known_headline_persistence": baseline == 1_599_000,
        "headline_level_basis_is_not_official_monthly_change": (
            shock["bound_construction"][
                "basis_is_release_headline_levels_not_official_monthly_change"
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
        "post_decision_event_is_disjoint": not (event_record_ids & set(compiled.source_record_ids)),
        "post_decision_event_is_exact_archived_census_nrc_fact": (
            event_record.source.source_id == CENSUS_NRC_SOURCE_ID
            and event_record.source.sha256 == EVENT_PDF_SHA256
            and event_record.entity_id
            == "census_hud_nrc:privately_owned_housing_starts_total"
            and str(event_record.source.url)
            == "https://www.census.gov/construction/nrc/pdf/newresconst_202003.pdf"
            and event_record.interval.published_at
            == datetime(2020, 4, 16, 12, 30, tzinfo=UTC)
            and event_record.interval.available_at
            == datetime(2020, 4, 16, 12, 30, tzinfo=UTC)
            and event_record.payload.get("release_date") == "2020-04-16"
            and event_record.payload.get("reference_month") == "2020-03"
            and event_record.payload.get("release_number") == "CB20-61"
            and event_record.payload.get("metric")
            == "privately_owned_total_housing_starts_sa_annual_rate"
            and event_record.payload.get("unit")
            == "Housing Units at Seasonally Adjusted Annual Rate"
            and event_record.payload.get("value_units") == 1_216_000
            and event_record.payload.get("value_thousand_units") == 1_216
            and event_record.payload.get("reported_monthly_change_percent") == "-22.3"
            and event_record.payload.get("reported_monthly_margin_90_percent") == "12.2"
            and event_record.payload.get("reported_monthly_ci_includes_zero") is False
            and event_record.payload.get(
                "reported_monthly_change_significant_at_90_percent"
            )
            is True
            and event_record.payload.get("reported_year_over_year_change_percent") == "1.4"
            and event_record.payload.get("reported_year_over_year_margin_90_percent") == "12.7"
            and event_record.payload.get("single_family_starts_units") == 856_000
            and event_record.payload.get("single_family_monthly_change_percent") == "-17.5"
            and event_record.payload.get("five_units_or_more_starts_units") == 347_000
            and event_record.payload.get("table3_average_rse_percent") == 6
            and event_record.payload.get("release_pdf_sha256") == EVENT_PDF_SHA256
            and event_record.payload.get("release_pdf_pages") == 7
            and event_record.payload.get("release_timezone_abbreviation") == "EDT"
            and event_record.payload.get("availability_method") == "exact_time_in_pdf"
            and event_record.payload.get("pdf_table_snapshot_verified") is True
            and event_record.payload.get("covid_publication_standard_statement_present") is True
            and event_level == 1_216_000
        ),
        "post_decision_february_revision_remains_later_snapshot_only": (
            event_record.payload.get("prior_month_revised_value_units") == 1_564_000
            and event_record.payload.get("prior_month_value_in_previous_release_units")
            == 1_599_000
            and event_record.payload.get("prior_month_revision_delta_units") == -35_000
            and all(record.payload.get("release_date") != "2020-04-16" for record in lock.records)
            and next(
                record
                for record in lock.records
                if record.payload.get("reference_month") == "2020-02"
            ).payload["value_units"]
            == 1_599_000
        ),
        "post_decision_event_timing_is_later": all(
            record.interval.available_at > lock.decision_time for record in event_lock.records
        ),
        "reported_post_event_level_is_below_declared_range": event_level < lower,
        "post_event_lower_bound_breach_equals_383000_units": lower_breach == 383_000,
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
        raise SystemExit(f"housing-starts assertions failed: {failed}")
    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "claim_boundary": (
            "This receipt proves two fresh local executions of the four relevant engines rebuild "
            "the committed directory and deterministic ZIP bytes from two locked pre-decision "
            "archived Census/HUD NRC preliminary headline levels. It verifies the separately "
            "locked March 2020 level is disjoint and, as an evaluation only, falls 383,000 SAAR "
            "units below the previously declared no-probability range. The range is not widened "
            "after the fact, and the April release's revision of February from 1,599,000 to "
            "1,564,000 does not overwrite the decision snapshot. The 32,000-unit input step is "
            "a difference between two release-time headlines, not the official monthly change "
            "against a revised prior. Official 90-percent sampling intervals are not range "
            "inputs. This does not prove forecast skill, calibrated coverage, housing or "
            "pandemic causality, investment performance, deployment, external review, or user "
            "impact."
        ),
        "assertions": assertions,
        "input_lock_sha256": lock.lock_sha256,
        "event_lock_sha256": event_lock.lock_sha256,
        "post_event_evaluation": {
            "reported_march_level_units": event_level,
            "latest_known_persistence_baseline_units": baseline,
            "known_headline_increase_units": known_increase,
            "declared_lower_level_units": lower,
            "declared_upper_level_units": upper,
            "lower_bound_breach_units": lower_breach,
            "event_february_previous_headline_units": 1_599_000,
            "event_february_revised_units": 1_564_000,
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
        raise SystemExit("housing-starts receipt is missing; use --write-receipt once")
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
        raise SystemExit("stored housing-starts receipt_sha256 mismatch")
    stored.pop("runtime", None)
    semantic_sha256 = stored.pop("semantic_sha256", None)
    if semantic_sha256 != _hash(stored):
        raise SystemExit("stored housing-starts semantic_sha256 mismatch")
    if semantic_sha256 != expected_semantic_sha256 or stored != expected_semantic:
        raise SystemExit("stored housing-starts semantic receipt differs from fresh rebuild")


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
