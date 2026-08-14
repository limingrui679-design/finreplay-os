"""Two-fresh-process benchmark receipts for the SEC EDGAR scale query."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from finreplay.scale.sec_edgar_query import SECLogAsOfQueryReceipt

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SECLogBenchmarkHardware(_StrictModel):
    system: str = Field(min_length=1, max_length=100)
    release: str = Field(min_length=1, max_length=200)
    machine: str = Field(min_length=1, max_length=100)
    logical_cpu_count: int = Field(gt=0)
    physical_memory_bytes: int = Field(gt=0)
    python_version: str = Field(min_length=1, max_length=100)
    duckdb_version: str = Field(min_length=1, max_length=100)


class SECLogQueryBenchmarkReceipt(_StrictModel):
    """Self-hashed pair of isolated query processes with explicitly uncontrolled OS cache."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    generated_at: datetime
    code_revision: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    run_labels: tuple[
        Literal["fresh_process_run_1_os_cache_not_controlled"],
        Literal["fresh_process_run_2_os_cache_not_controlled"],
    ]
    runs: tuple[SECLogAsOfQueryReceipt, SECLogAsOfQueryReceipt]
    hardware: SECLogBenchmarkHardware
    os_cache_controlled: Literal[False] = False
    claim_boundary: str = Field(min_length=350, max_length=4_000)
    benchmark_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_benchmark(self, info: ValidationInfo) -> SECLogQueryBenchmarkReceipt:
        _require_aware(self.generated_at, "generated_at")
        first, second = self.runs
        if first.process_id == second.process_id:
            raise ValueError("SEC log benchmark runs must come from distinct processes")
        for run in self.runs:
            if not run.input_hashes_verified:
                raise ValueError("SEC log benchmark requires verified input hashes")
            if run.cache_state != "fresh_process_os_cache_not_controlled":
                raise ValueError("SEC log benchmark requires fresh-process run labels")
            if run.duckdb_version != self.hardware.duckdb_version:
                raise ValueError("SEC log benchmark DuckDB versions disagree")
        comparable = (
            "manifest_sha256",
            "scale_target_met",
            "event_cutoff_date",
            "event_cutoff_second",
            "knowledge_cutoff",
            "eligible_partition_count",
            "eligible_partition_receipt_sha256s",
            "eligible_physical_rows",
            "eligible_parquet_bytes",
            "input_scan_rows",
            "temporal_invalid_rows",
            "rows_at_or_before_cutoff",
            "invalid_rows_at_or_before_cutoff",
            "crawler_rows_at_or_before_cutoff",
            "http_success_rows_at_or_before_cutoff",
            "unique_ciks_at_or_before_cutoff",
            "document_bytes_at_or_before_cutoff",
            "query_logic_sha256",
            "logical_sql_sha256",
        )
        if any(getattr(first, name) != getattr(second, name) for name in comparable):
            raise ValueError("SEC log benchmark runs are not semantically comparable")
        payload = self.model_dump(mode="json", exclude={"benchmark_receipt_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.benchmark_receipt_sha256:
            raise ValueError("SEC log benchmark_receipt_sha256 mismatch")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> SECLogQueryBenchmarkReceipt:
        values = dict(payload)
        values.pop("benchmark_receipt_sha256", None)
        normalized = cls.model_validate(
            {**values, "benchmark_receipt_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"benchmark_receipt_sha256"})
        return cls.model_validate({**normalized, "benchmark_receipt_sha256": _hash(normalized)})


def build_sec_log_query_benchmark_receipt(
    *,
    first: SECLogAsOfQueryReceipt,
    second: SECLogAsOfQueryReceipt,
    code_revision: str,
    generated_at: datetime,
) -> SECLogQueryBenchmarkReceipt:
    """Bind two child receipts and the observed local hardware without a cold-cache claim."""

    hardware = SECLogBenchmarkHardware(
        system=platform.system(),
        release=platform.release(),
        machine=platform.machine(),
        logical_cpu_count=os.cpu_count() or 1,
        physical_memory_bytes=_physical_memory_bytes(),
        python_version=platform.python_version(),
        duckdb_version=first.duckdb_version,
    )
    return SECLogQueryBenchmarkReceipt.create(
        {
            "schema_version": "1.0.0",
            "generated_at": generated_at,
            "code_revision": code_revision,
            "run_labels": [
                "fresh_process_run_1_os_cache_not_controlled",
                "fresh_process_run_2_os_cache_not_controlled",
            ],
            "runs": [first.model_dump(mode="json"), second.model_dump(mode="json")],
            "hardware": hardware.model_dump(mode="json"),
            "os_cache_controlled": False,
            "claim_boundary": (
                "This benchmark binds two semantically identical, hash-verified SEC log queries "
                "executed in distinct fresh processes on the recorded local hardware. Each child "
                "receipt includes logical SQL, exact manifest/input rows and bytes, elapsed query "
                "and input-hash time, and a process-lifetime peak-RSS high-water mark. The "
                "operating system page cache is not flushed, pinned, measured, or otherwise "
                "controlled, so "
                "neither run is called cold or warm and their timings are not a production SLA or "
                "universal throughput claim. The benchmark tests local reproducibility and scale "
                "only; it does not establish method correctness, external review, deployment, "
                "adoption, unique users, financial performance, or real-world impact."
            ),
        }
    )


def write_sec_log_query_benchmark_receipt(receipt: SECLogQueryBenchmarkReceipt, path: Path) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    os.close(descriptor)
    try:
        temporary.write_text(
            json.dumps(receipt.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with temporary.open("rb") as output:
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_sec_log_query_benchmark_receipt(path: Path) -> SECLogQueryBenchmarkReceipt:
    try:
        return SECLogQueryBenchmarkReceipt.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid SEC log query benchmark receipt: {path}") from error


def _physical_memory_bytes() -> int:
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError):
        pages = 1
        page_size = 1
    return max(1, pages * page_size)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()
