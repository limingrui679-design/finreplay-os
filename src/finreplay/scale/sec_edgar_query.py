"""Knowledge-cutoff-aware aggregate queries over the exact SEC EDGAR log lake."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from finreplay.scale.sec_edgar_manifest import SECLogScaleManifest

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_QUERY_LOGIC = (
    "scan every row in manifest partitions with archive_retrieved_at <= knowledge_cutoff "
    "and partition_date <= event_cutoff_date; count source rows separately; include an event "
    "in result aggregates only when event_date and event_time_seconds are non-null and the "
    "tuple (event_date,event_time_seconds) is <= (event_cutoff_date,event_cutoff_second)"
)
_QUERY_LOGIC_SHA256 = hashlib.sha256(_QUERY_LOGIC.encode()).hexdigest()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SECLogAsOfQueryReceipt(_StrictModel):
    """Self-hashed result with separate event and observed-availability cutoffs."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    scale_target_met: bool
    event_cutoff_date: date
    event_cutoff_second: int = Field(ge=0, le=86_399)
    knowledge_cutoff: datetime
    executed_at: datetime
    eligible_partition_count: int = Field(ge=0)
    eligible_partition_receipt_sha256s: tuple[str, ...]
    eligible_physical_rows: int = Field(ge=0)
    eligible_parquet_bytes: int = Field(ge=0)
    input_hashes_verified: bool
    input_hash_verification_seconds: float = Field(ge=0)
    input_scan_rows: int = Field(ge=0)
    temporal_invalid_rows: int = Field(ge=0)
    rows_at_or_before_cutoff: int = Field(ge=0)
    invalid_rows_at_or_before_cutoff: int = Field(ge=0)
    crawler_rows_at_or_before_cutoff: int = Field(ge=0)
    http_success_rows_at_or_before_cutoff: int = Field(ge=0)
    unique_ciks_at_or_before_cutoff: int = Field(ge=0)
    document_bytes_at_or_before_cutoff: int = Field(ge=0)
    query_elapsed_seconds: float = Field(ge=0)
    duckdb_version: str = Field(min_length=1, max_length=100)
    cache_state: Literal[
        "fresh_process_os_cache_not_controlled",
        "same_process_os_cache_not_controlled",
    ]
    query_logic_sha256: str = Field(pattern=_SHA256_PATTERN)
    claim_boundary: str = Field(min_length=350, max_length=4_000)
    receipt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_receipt(self, info: ValidationInfo) -> SECLogAsOfQueryReceipt:
        _require_aware(self.knowledge_cutoff, "knowledge_cutoff")
        _require_aware(self.executed_at, "executed_at")
        if self.executed_at < self.knowledge_cutoff:
            raise ValueError("query execution cannot precede its knowledge cutoff")
        if self.eligible_partition_count != len(self.eligible_partition_receipt_sha256s):
            raise ValueError("SEC log query eligible partition count mismatch")
        if len(set(self.eligible_partition_receipt_sha256s)) != len(
            self.eligible_partition_receipt_sha256s
        ):
            raise ValueError("SEC log query eligible receipt hashes must be unique")
        if self.input_scan_rows != self.eligible_physical_rows:
            raise ValueError("SEC log query input scan rows differ from manifest row sum")
        if self.temporal_invalid_rows > self.input_scan_rows:
            raise ValueError("SEC log query temporal invalid rows exceed input rows")
        if self.rows_at_or_before_cutoff > self.input_scan_rows:
            raise ValueError("SEC log query result rows exceed input rows")
        bounded_counts = (
            self.invalid_rows_at_or_before_cutoff,
            self.crawler_rows_at_or_before_cutoff,
            self.http_success_rows_at_or_before_cutoff,
            self.unique_ciks_at_or_before_cutoff,
        )
        if any(value > self.rows_at_or_before_cutoff for value in bounded_counts):
            raise ValueError("SEC log query aggregate exceeds cutoff row count")
        if self.eligible_partition_count == 0 and any(
            (
                self.eligible_physical_rows,
                self.eligible_parquet_bytes,
                self.input_scan_rows,
                self.temporal_invalid_rows,
                self.rows_at_or_before_cutoff,
                self.document_bytes_at_or_before_cutoff,
            )
        ):
            raise ValueError("empty SEC log query eligibility must produce zero counts")
        if self.query_logic_sha256 != _QUERY_LOGIC_SHA256:
            raise ValueError("SEC log query logic hash mismatch")
        payload = self.model_dump(mode="json", exclude={"receipt_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.receipt_sha256:
            raise ValueError("SEC log query receipt_sha256 mismatch")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> SECLogAsOfQueryReceipt:
        values = dict(payload)
        values.pop("receipt_sha256", None)
        normalized = cls.model_validate(
            {**values, "receipt_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"receipt_sha256"})
        return cls.model_validate({**normalized, "receipt_sha256": _hash(normalized)})


def run_sec_log_asof_query(
    manifest: SECLogScaleManifest,
    *,
    parquet_directory: Path,
    event_cutoff_date: date,
    event_cutoff_second: int,
    knowledge_cutoff: datetime,
    executed_at: datetime,
    cache_state: Literal[
        "fresh_process_os_cache_not_controlled",
        "same_process_os_cache_not_controlled",
    ],
    verify_input_hashes: bool = True,
    threads: int = 4,
) -> SECLogAsOfQueryReceipt:
    """Run a measured aggregate without treating retrieval time as event time."""

    if event_cutoff_second < 0 or event_cutoff_second > 86_399:
        raise ValueError("event_cutoff_second must be in 0..86399")
    _require_aware(knowledge_cutoff, "knowledge_cutoff")
    _require_aware(executed_at, "executed_at")
    if executed_at < knowledge_cutoff:
        raise ValueError("executed_at cannot precede knowledge_cutoff")
    if threads <= 0:
        raise ValueError("threads must be positive")
    eligible = tuple(
        item
        for item in manifest.partitions
        if item.archive_retrieved_at <= knowledge_cutoff
        and item.partition_date <= event_cutoff_date
    )
    parquet_directory = parquet_directory.expanduser().resolve()
    input_hash_started = time.perf_counter()
    paths: list[Path] = []
    if verify_input_hashes:
        for item in eligible:
            path = parquet_directory / f"log{item.partition_date:%Y%m%d}.parquet"
            if not path.is_file():
                raise ValueError(f"eligible SEC log Parquet is missing: {path.name}")
            if path.stat().st_size != item.parquet_bytes:
                raise ValueError(f"eligible SEC log Parquet byte mismatch: {path.name}")
            if _file_sha256(path) != item.parquet_sha256:
                raise ValueError(f"eligible SEC log Parquet hash mismatch: {path.name}")
            paths.append(path)
    else:
        paths = [
            parquet_directory / f"log{item.partition_date:%Y%m%d}.parquet" for item in eligible
        ]
        if any(not path.is_file() for path in paths):
            raise ValueError("one or more eligible SEC log Parquet files are missing")
    input_hash_seconds = time.perf_counter() - input_hash_started
    eligible_rows = sum(item.physical_row_count for item in eligible)
    eligible_bytes = sum(item.parquet_bytes for item in eligible)
    if paths:
        query_started = time.perf_counter()
        values = _execute_query(
            paths,
            event_cutoff_date=event_cutoff_date,
            event_cutoff_second=event_cutoff_second,
            threads=threads,
        )
        query_seconds = time.perf_counter() - query_started
    else:
        values = {
            "input_scan_rows": 0,
            "temporal_invalid_rows": 0,
            "rows_at_or_before_cutoff": 0,
            "invalid_rows_at_or_before_cutoff": 0,
            "crawler_rows_at_or_before_cutoff": 0,
            "http_success_rows_at_or_before_cutoff": 0,
            "unique_ciks_at_or_before_cutoff": 0,
            "document_bytes_at_or_before_cutoff": 0,
        }
        query_seconds = 0.0
    if values["input_scan_rows"] != eligible_rows:
        raise ValueError("SEC log query scan row count differs from manifest")
    return SECLogAsOfQueryReceipt.create(
        {
            "schema_version": "1.0.0",
            "manifest_sha256": manifest.manifest_sha256,
            "scale_target_met": manifest.target_met,
            "event_cutoff_date": event_cutoff_date,
            "event_cutoff_second": event_cutoff_second,
            "knowledge_cutoff": knowledge_cutoff,
            "executed_at": executed_at,
            "eligible_partition_count": len(eligible),
            "eligible_partition_receipt_sha256s": [
                item.partition_receipt_sha256 for item in eligible
            ],
            "eligible_physical_rows": eligible_rows,
            "eligible_parquet_bytes": eligible_bytes,
            "input_hashes_verified": verify_input_hashes,
            "input_hash_verification_seconds": input_hash_seconds,
            **values,
            "query_elapsed_seconds": query_seconds,
            "duckdb_version": duckdb.__version__,
            "cache_state": cache_state,
            "query_logic_sha256": _QUERY_LOGIC_SHA256,
            "claim_boundary": (
                "This receipt measures one aggregate scan over content-addressed Parquet inputs "
                "selected by two distinct cutoffs: logged event time and the time this project "
                "actually observed the archive. It does not backdate 2026 retrieval into 2012 "
                "knowledge, infer SEC's exact historical publication time, or treat access "
                "requests as unique users, filings, trades, decisions, customers, or outcomes. "
                "Rows with invalid event date/time remain in input_scan_rows and "
                "temporal_invalid_rows but are excluded from cutoff aggregates. OS cache state is "
                "explicitly not controlled, so "
                "elapsed time is a local measurement rather than a universal performance claim. "
                "Internal reproducibility does not establish deployment, adoption, external "
                "review, or real-world impact."
            ),
        }
    )


def write_sec_log_asof_query_receipt(receipt: SECLogAsOfQueryReceipt, path: Path) -> None:
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


def load_sec_log_asof_query_receipt(path: Path) -> SECLogAsOfQueryReceipt:
    try:
        return SECLogAsOfQueryReceipt.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid SEC log as-of query receipt: {path}") from error


def _execute_query(
    paths: list[Path],
    *,
    event_cutoff_date: date,
    event_cutoff_second: int,
    threads: int,
) -> dict[str, int]:
    path_list = "[" + ", ".join(_sql_string(path) for path in paths) + "]"
    cutoff_date = _sql_string(event_cutoff_date.isoformat()) + "::DATE"
    cutoff = (
        "event_date IS NOT NULL AND event_time_seconds IS NOT NULL AND "
        f"(event_date < {cutoff_date} OR "
        f"(event_date = {cutoff_date} AND event_time_seconds <= {event_cutoff_second}))"
    )
    sql = f"""
SELECT
    count(*)::UBIGINT AS input_scan_rows,
    count_if(event_date IS NULL OR event_time_seconds IS NULL)::UBIGINT
        AS temporal_invalid_rows,
    count_if({cutoff})::UBIGINT AS rows_at_or_before_cutoff,
    count_if(({cutoff}) AND invalid_mask <> 0)::UBIGINT
        AS invalid_rows_at_or_before_cutoff,
    count_if(({cutoff}) AND coalesce(is_crawler, false))::UBIGINT
        AS crawler_rows_at_or_before_cutoff,
    count_if(({cutoff}) AND status_code BETWEEN 200 AND 299)::UBIGINT
        AS http_success_rows_at_or_before_cutoff,
    count(DISTINCT CASE WHEN {cutoff} THEN cik END)::UBIGINT
        AS unique_ciks_at_or_before_cutoff,
    coalesce(sum(CASE WHEN {cutoff} THEN document_size ELSE 0 END), 0)::HUGEINT
        AS document_bytes_at_or_before_cutoff
FROM read_parquet({path_list}, union_by_name=false)
"""
    connection = duckdb.connect()
    connection.execute(f"SET threads = {threads}")
    try:
        row = connection.execute(sql).fetchone()
        if row is None:
            raise ValueError("SEC log query returned no aggregate row")
        names = [item[0] for item in connection.description]
    finally:
        connection.close()
    return {name: int(value) for name, value in zip(names, row, strict=True)}


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
