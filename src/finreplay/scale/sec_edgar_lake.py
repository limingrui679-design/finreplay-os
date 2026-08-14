"""Loss-accounted Parquet materialization for official SEC EDGAR access logs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationInfo, model_validator

from finreplay.scale.sec_edgar_download import SECLogDownloadReceipt
from finreplay.scale.sec_edgar_logs import (
    SEC_EDGAR_LOG_HEADER_2003_2017,
    SECLogExtractedCSV,
    SECLogPartition,
    extract_sec_log_archive,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CSV_COLUMNS = SEC_EDGAR_LOG_HEADER_2003_2017
_BROWSER_CODES = (
    "",
    "and",
    "chr",
    "fox",
    "iem",
    "ipd",
    "iph",
    "lin",
    "mac",
    "mie",
    "opr",
    "oth",
    "rim",
    "saf",
    "sea",
    "win",
)
_INVALID_BITS = {
    "blank_ip": 1,
    "invalid_or_mismatched_date": 2,
    "invalid_time": 4,
    "invalid_zone": 8,
    "invalid_cik": 16,
    "invalid_accession": 32,
    "blank_document": 64,
    "invalid_status_code": 128,
    "invalid_document_size": 256,
    "invalid_index_flag": 512,
    "invalid_no_referrer_flag": 1_024,
    "invalid_no_user_agent_flag": 2_048,
    "invalid_find_code": 4_096,
    "invalid_crawler_flag": 8_192,
    "invalid_browser_code": 16_384,
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SECLogInvalidCounts(_StrictModel):
    """Transparent anomaly counts; malformed source rows are retained, not hidden."""

    rows_with_any_invalid: int = Field(ge=0)
    blank_ip: int = Field(ge=0)
    invalid_or_mismatched_date: int = Field(ge=0)
    invalid_time: int = Field(ge=0)
    invalid_zone: int = Field(ge=0)
    invalid_cik: int = Field(ge=0)
    invalid_accession: int = Field(ge=0)
    blank_document: int = Field(ge=0)
    invalid_status_code: int = Field(ge=0)
    invalid_document_size: int = Field(ge=0)
    invalid_index_flag: int = Field(ge=0)
    invalid_no_referrer_flag: int = Field(ge=0)
    invalid_no_user_agent_flag: int = Field(ge=0)
    invalid_find_code: int = Field(ge=0)
    invalid_crawler_flag: int = Field(ge=0)
    invalid_browser_code: int = Field(ge=0)


class SECLogParquetColumn(_StrictModel):
    name: str = Field(min_length=1, max_length=100)
    duckdb_type: str = Field(min_length=1, max_length=100)
    nullable: bool


EXPECTED_SEC_LOG_PARQUET_COLUMNS = (
    SECLogParquetColumn(name="row_ordinal", duckdb_type="UBIGINT", nullable=True),
    SECLogParquetColumn(name="source_partition_date", duckdb_type="DATE", nullable=True),
    SECLogParquetColumn(name="event_date", duckdb_type="DATE", nullable=True),
    SECLogParquetColumn(name="event_time_seconds", duckdb_type="UINTEGER", nullable=True),
    SECLogParquetColumn(name="apache_zone", duckdb_type="SMALLINT", nullable=True),
    SECLogParquetColumn(name="cik", duckdb_type="UBIGINT", nullable=True),
    SECLogParquetColumn(name="accession", duckdb_type="VARCHAR", nullable=True),
    SECLogParquetColumn(name="document", duckdb_type="VARCHAR", nullable=True),
    SECLogParquetColumn(name="status_code", duckdb_type="USMALLINT", nullable=True),
    SECLogParquetColumn(name="document_size", duckdb_type="UBIGINT", nullable=True),
    SECLogParquetColumn(name="is_index", duckdb_type="BOOLEAN", nullable=True),
    SECLogParquetColumn(name="no_referrer", duckdb_type="BOOLEAN", nullable=True),
    SECLogParquetColumn(name="no_user_agent", duckdb_type="BOOLEAN", nullable=True),
    SECLogParquetColumn(name="find_code", duckdb_type="UTINYINT", nullable=True),
    SECLogParquetColumn(name="is_crawler", duckdb_type="BOOLEAN", nullable=True),
    SECLogParquetColumn(name="browser_code", duckdb_type="VARCHAR", nullable=True),
    SECLogParquetColumn(name="invalid_mask", duckdb_type="USMALLINT", nullable=True),
)


class SECLogPartitionReceipt(_StrictModel):
    """Self-hashed evidence for one physical-row-preserving daily partition."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    partition_date: date
    listed_url: HttpUrl
    source_url: HttpUrl
    list_page_url: HttpUrl
    archive_retrieved_at: datetime
    materialized_at: datetime
    download_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    zip_filename: str
    zip_bytes: int = Field(gt=0)
    zip_sha256: str = Field(pattern=_SHA256_PATTERN)
    archive_member_names: tuple[str, ...] = Field(min_length=1, max_length=2)
    csv_member_name: str
    csv_bytes: int = Field(gt=0)
    csv_sha256: str = Field(pattern=_SHA256_PATTERN)
    csv_crc32: str = Field(pattern=r"^[0-9a-f]{8}$")
    readme_bytes: int | None = Field(default=None, gt=0)
    readme_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    readme_crc32: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}$")
    physical_line_count: int = Field(gt=1)
    data_row_count: int = Field(gt=0)
    parquet_filename: str
    parquet_bytes: int = Field(gt=0)
    parquet_sha256: str = Field(pattern=_SHA256_PATTERN)
    parquet_row_count: int = Field(gt=0)
    parquet_row_group_count: int = Field(gt=0)
    parquet_columns: tuple[SECLogParquetColumn, ...]
    first_row_ordinal: int = Field(ge=1)
    last_row_ordinal: int = Field(ge=1)
    ordinal_sequence_mismatch_count: int = Field(ge=0)
    partition_date_mismatch_count: int = Field(ge=0)
    invalid_counts: SECLogInvalidCounts
    source_coordinate_sha256: str = Field(pattern=_SHA256_PATTERN)
    duckdb_version: str = Field(min_length=1, max_length=100)
    claim_boundary: str = Field(min_length=300, max_length=4_000)
    receipt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_receipt(self, info: ValidationInfo) -> SECLogPartitionReceipt:
        SECLogPartition.model_validate(
            {
                "partition_date": self.partition_date,
                "listed_url": self.listed_url,
                "source_url": self.source_url,
                "list_page_url": self.list_page_url,
            }
        )
        _require_aware(self.archive_retrieved_at, "archive_retrieved_at")
        _require_aware(self.materialized_at, "materialized_at")
        if self.materialized_at < self.archive_retrieved_at:
            raise ValueError("materialized_at cannot precede archive_retrieved_at")
        compact = self.partition_date.strftime("%Y%m%d")
        if self.zip_filename != f"log{compact}.zip":
            raise ValueError("SEC log ZIP filename does not match partition_date")
        if self.csv_member_name != f"log{compact}.csv":
            raise ValueError("SEC log CSV member does not match partition_date")
        if self.parquet_filename != f"log{compact}.parquet":
            raise ValueError("SEC log Parquet filename does not match partition_date")
        expected_members = {self.csv_member_name}
        readme_values = (self.readme_bytes, self.readme_sha256, self.readme_crc32)
        if any(value is not None for value in readme_values):
            if not all(value is not None for value in readme_values):
                raise ValueError("SEC log README evidence must be all present or all absent")
            expected_members.add("README.txt")
        if set(self.archive_member_names) != expected_members:
            raise ValueError("SEC log archive member evidence is inconsistent")
        if self.archive_member_names != tuple(sorted(self.archive_member_names)):
            raise ValueError("SEC log archive member evidence must be sorted")
        if len(set(self.archive_member_names)) != len(self.archive_member_names):
            raise ValueError("SEC log archive member evidence must be unique")
        if self.physical_line_count != self.data_row_count + 1:
            raise ValueError("SEC log physical line and data row counts disagree")
        if self.parquet_row_count != self.data_row_count:
            raise ValueError("SEC log Parquet must retain every physical CSV data row")
        if self.first_row_ordinal != 1 or self.last_row_ordinal != self.data_row_count:
            raise ValueError("SEC log row ordinal range must be exactly 1..data_row_count")
        if self.ordinal_sequence_mismatch_count != 0:
            raise ValueError("SEC log row ordinals must form an exact ordered sequence")
        if self.partition_date_mismatch_count != 0:
            raise ValueError("SEC log source_partition_date must match the receipt partition")
        if self.parquet_columns != EXPECTED_SEC_LOG_PARQUET_COLUMNS:
            raise ValueError("SEC log Parquet schema does not match the compact lake contract")
        invalid_values = self.invalid_counts.model_dump()
        if any(value > self.data_row_count for value in invalid_values.values()):
            raise ValueError("SEC log invalid count exceeds data_row_count")
        individual_invalid = [
            value for name, value in invalid_values.items() if name != "rows_with_any_invalid"
        ]
        rows_with_invalid = self.invalid_counts.rows_with_any_invalid
        if rows_with_invalid < max(individual_invalid) or rows_with_invalid > sum(
            individual_invalid
        ):
            raise ValueError("SEC log rows_with_any_invalid is inconsistent")
        coordinate_payload = {
            "namespace": "sec-edgar-log-physical-row-v1",
            "source_zip_sha256": self.zip_sha256,
            "first_row_ordinal": self.first_row_ordinal,
            "last_row_ordinal": self.last_row_ordinal,
        }
        if _hash(coordinate_payload) != self.source_coordinate_sha256:
            raise ValueError("SEC log source coordinate hash mismatch")
        payload = self.model_dump(mode="json", exclude={"receipt_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.receipt_sha256:
            raise ValueError("SEC log partition receipt_sha256 mismatch")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> SECLogPartitionReceipt:
        values = dict(payload)
        values.pop("receipt_sha256", None)
        normalized = cls.model_validate(
            {**values, "receipt_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"receipt_sha256"})
        return cls.model_validate({**normalized, "receipt_sha256": _hash(normalized)})


def materialize_sec_log_csv(
    extracted: SECLogExtractedCSV,
    *,
    partition: SECLogPartition,
    download_receipt: SECLogDownloadReceipt,
    parquet_path: Path,
    materialized_at: datetime | None = None,
) -> SECLogPartitionReceipt:
    """Retain exactly one compact Parquet row for every physical source CSV data row."""

    if extracted.partition_date != partition.partition_date:
        raise ValueError("SEC log extraction and partition dates disagree")
    _validate_download_chain(download_receipt, extracted, partition)
    archive_retrieved_at = download_receipt.observation_completed_at
    observed_materialized_at = materialized_at or datetime.now(UTC)
    _require_aware(observed_materialized_at, "materialized_at")
    csv_path = extracted.csv_path.expanduser().resolve()
    parquet_path = parquet_path.expanduser().resolve()
    if not csv_path.is_file():
        raise ValueError(f"SEC log extracted CSV does not exist: {csv_path}")
    if csv_path.stat().st_size != extracted.csv_bytes:
        raise ValueError("SEC log extracted CSV byte count changed after extraction")
    if _file_sha256(csv_path) != extracted.csv_sha256:
        raise ValueError("SEC log extracted CSV hash changed after extraction")
    expected_rows = extracted.physical_line_count - 1
    connection = duckdb.connect()
    connection.execute("SET threads = 1")
    connection.execute("SET preserve_insertion_order = true")
    try:
        profile = _profile_csv(connection, csv_path, partition.partition_date)
        if profile["row_count"] != expected_rows:
            raise ValueError("SEC log CSV parser row count differs from physical data rows")
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{parquet_path.name}.", suffix=".tmp", dir=parquet_path.parent
        )
        temporary = Path(temporary_name)
        os.close(descriptor)
        try:
            temporary.unlink()
            transformed = _transformed_sql(csv_path, partition.partition_date)
            connection.execute(
                "COPY ("
                + transformed
                + ") TO "
                + _sql_string(temporary)
                + " (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)"
            )
            with temporary.open("rb") as output:
                os.fsync(output.fileno())
            os.replace(temporary, parquet_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        parquet = _inspect_parquet(connection, parquet_path, partition.partition_date)
    finally:
        connection.close()
    invalid_counts = SECLogInvalidCounts.model_validate(profile["invalid_counts"])
    coordinate_payload = {
        "namespace": "sec-edgar-log-physical-row-v1",
        "source_zip_sha256": extracted.zip_sha256,
        "first_row_ordinal": parquet["first_row_ordinal"],
        "last_row_ordinal": parquet["last_row_ordinal"],
    }
    return SECLogPartitionReceipt.create(
        {
            "schema_version": "1.0.0",
            "partition_date": partition.partition_date,
            "listed_url": partition.listed_url,
            "source_url": partition.source_url,
            "list_page_url": partition.list_page_url,
            "archive_retrieved_at": archive_retrieved_at,
            "materialized_at": observed_materialized_at,
            "download_receipt_sha256": download_receipt.receipt_sha256,
            "zip_filename": f"log{partition.partition_date:%Y%m%d}.zip",
            "zip_bytes": extracted.zip_bytes,
            "zip_sha256": extracted.zip_sha256,
            "archive_member_names": extracted.archive_member_names,
            "csv_member_name": extracted.member_name,
            "csv_bytes": extracted.csv_bytes,
            "csv_sha256": extracted.csv_sha256,
            "csv_crc32": extracted.csv_crc32,
            "readme_bytes": extracted.readme_bytes,
            "readme_sha256": extracted.readme_sha256,
            "readme_crc32": extracted.readme_crc32,
            "physical_line_count": extracted.physical_line_count,
            "data_row_count": expected_rows,
            "parquet_filename": parquet_path.name,
            "parquet_bytes": parquet_path.stat().st_size,
            "parquet_sha256": _file_sha256(parquet_path),
            "parquet_row_count": parquet["row_count"],
            "parquet_row_group_count": parquet["row_group_count"],
            "parquet_columns": [column.model_dump(mode="json") for column in parquet["columns"]],
            "first_row_ordinal": parquet["first_row_ordinal"],
            "last_row_ordinal": parquet["last_row_ordinal"],
            "ordinal_sequence_mismatch_count": parquet["ordinal_sequence_mismatch_count"],
            "partition_date_mismatch_count": parquet["partition_date_mismatch_count"],
            "invalid_counts": invalid_counts.model_dump(mode="json"),
            "source_coordinate_sha256": _hash(coordinate_payload),
            "duckdb_version": duckdb.__version__,
            "claim_boundary": (
                "This receipt proves that one fully hashed official SEC EDGAR daily ZIP was "
                "CRC-checked, structurally parsed, and materialized as exactly one compact "
                "Parquet row per physical CSV data row. Identity is the ZIP SHA-256 plus the "
                "one-based physical row ordinal; repeated requests are intentionally not "
                "deduplicated. The exact source observation is chained by its download-receipt "
                "SHA-256. Invalid source values are retained with a bit mask and counted, "
                "while the obfuscated IP field is deliberately omitted from the derived lake for "
                "data minimization. These are SEC.gov access-log requests, not unique people, "
                "filings, trades, customers, production usage, or demonstrated impact. Event date "
                "and time describe logged access; archive retrieval time is separate and does not "
                "establish the archive's exact historical public-release time. SEC warns that lost "
                "or damaged files and extraction limitations may leave the source logs incomplete."
            ),
        }
    )


def write_sec_log_partition_receipt(receipt: SECLogPartitionReceipt, path: Path) -> None:
    """Atomically write canonical, self-hashed receipt JSON without machine-local paths."""

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


def load_sec_log_partition_receipt(path: Path) -> SECLogPartitionReceipt:
    try:
        return SECLogPartitionReceipt.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid SEC log partition receipt: {path}") from error


def verify_sec_log_partition_receipt(
    receipt: SECLogPartitionReceipt,
    *,
    download_receipt: SECLogDownloadReceipt,
    archive_path: Path,
    parquet_path: Path,
) -> None:
    """Freshly re-read source and output bytes and re-check all semantic receipt counts."""

    archive_path = archive_path.expanduser().resolve()
    parquet_path = parquet_path.expanduser().resolve()
    partition = SECLogPartition.model_validate(
        {
            "partition_date": receipt.partition_date,
            "listed_url": receipt.listed_url,
            "source_url": receipt.source_url,
            "list_page_url": receipt.list_page_url,
        }
    )
    if archive_path.name != receipt.zip_filename or not archive_path.is_file():
        raise ValueError("SEC log verification archive is missing or misnamed")
    if parquet_path.name != receipt.parquet_filename or not parquet_path.is_file():
        raise ValueError("SEC log verification Parquet is missing or misnamed")
    if archive_path.stat().st_size != receipt.zip_bytes:
        raise ValueError("SEC log verification ZIP byte count mismatch")
    if _file_sha256(archive_path) != receipt.zip_sha256:
        raise ValueError("SEC log verification ZIP hash mismatch")
    if parquet_path.stat().st_size != receipt.parquet_bytes:
        raise ValueError("SEC log verification Parquet byte count mismatch")
    if _file_sha256(parquet_path) != receipt.parquet_sha256:
        raise ValueError("SEC log verification Parquet hash mismatch")
    with tempfile.TemporaryDirectory(prefix="finreplay-sec-log-verify-") as directory:
        extracted = extract_sec_log_archive(
            archive_path,
            partition_date=receipt.partition_date,
            destination=Path(directory) / receipt.csv_member_name,
        )
        _validate_download_chain(download_receipt, extracted, partition)
        if download_receipt.receipt_sha256 != receipt.download_receipt_sha256:
            raise ValueError("SEC log verification download receipt hash mismatch")
        if download_receipt.observation_completed_at != receipt.archive_retrieved_at:
            raise ValueError("SEC log verification archive retrieval time mismatch")
        _compare_extraction_to_receipt(extracted, receipt)
        connection = duckdb.connect()
        connection.execute("SET threads = 1")
        connection.execute("SET preserve_insertion_order = true")
        try:
            profile = _profile_csv(connection, extracted.csv_path, receipt.partition_date)
            parquet = _inspect_parquet(connection, parquet_path, receipt.partition_date)
        finally:
            connection.close()
    if profile["row_count"] != receipt.data_row_count:
        raise ValueError("SEC log verification CSV row count mismatch")
    if SECLogInvalidCounts.model_validate(profile["invalid_counts"]) != receipt.invalid_counts:
        raise ValueError("SEC log verification CSV invalid counts mismatch")
    if parquet["row_count"] != receipt.parquet_row_count:
        raise ValueError("SEC log verification Parquet row count mismatch")
    if parquet["row_group_count"] != receipt.parquet_row_group_count:
        raise ValueError("SEC log verification Parquet row-group count mismatch")
    if parquet["columns"] != receipt.parquet_columns:
        raise ValueError("SEC log verification Parquet schema mismatch")
    if parquet["first_row_ordinal"] != receipt.first_row_ordinal:
        raise ValueError("SEC log verification first row ordinal mismatch")
    if parquet["last_row_ordinal"] != receipt.last_row_ordinal:
        raise ValueError("SEC log verification last row ordinal mismatch")
    if parquet["ordinal_sequence_mismatch_count"] != 0:
        raise ValueError("SEC log verification row ordinal sequence mismatch")
    if parquet["partition_date_mismatch_count"] != 0:
        raise ValueError("SEC log verification source partition date mismatch")
    if parquet["invalid_counts"] != receipt.invalid_counts:
        raise ValueError("SEC log verification Parquet invalid counts mismatch")


def _profile_csv(
    connection: duckdb.DuckDBPyConnection, csv_path: Path, partition_date: date
) -> dict[str, Any]:
    valid = _validity_sql(partition_date)
    aliases = list(_INVALID_BITS)
    counts = ",\n".join(f"count_if(NOT ({valid[name]})) AS {name}" for name in aliases)
    any_invalid = " OR ".join(f"NOT ({valid[name]})" for name in aliases)
    row = connection.execute(
        "SELECT count(*) AS row_count,\n"
        f"count_if({any_invalid}) AS rows_with_any_invalid,\n"
        f"{counts}\nFROM {_csv_scan_sql(csv_path)}"
    ).fetchone()
    if row is None:
        raise ValueError("SEC log CSV profile returned no result")
    names = [item[0] for item in connection.description]
    values = dict(zip(names, row, strict=True))
    row_count = int(values.pop("row_count"))
    invalid_counts = {name: int(value) for name, value in values.items()}
    return {"row_count": row_count, "invalid_counts": invalid_counts}


def _transformed_sql(csv_path: Path, partition_date: date) -> str:
    valid = _validity_sql(partition_date)
    invalid_mask = " + ".join(
        f"CASE WHEN {valid[name]} THEN 0 ELSE {bit} END" for name, bit in _INVALID_BITS.items()
    )
    partition_literal = _sql_string(partition_date.isoformat())
    return f"""
WITH raw AS (
    SELECT row_number() OVER ()::UBIGINT AS row_ordinal, *
    FROM {_csv_scan_sql(csv_path)}
)
SELECT
    row_ordinal,
    {partition_literal}::DATE AS source_partition_date,
    try_cast(date AS DATE) AS event_date,
    CASE WHEN {valid["invalid_time"]} THEN
        epoch(try_strptime(time, '%H:%M:%S')::TIME)::UINTEGER
    END AS event_time_seconds,
    CASE WHEN {valid["invalid_zone"]} THEN
        try_cast(regexp_replace(zone, '\\.0$', '') AS SMALLINT)
    END AS apache_zone,
    CASE WHEN {valid["invalid_cik"]} THEN
        try_cast(regexp_replace(cik, '\\.0$', '') AS UBIGINT)
    END AS cik,
    accession,
    extention AS document,
    CASE WHEN {valid["invalid_status_code"]} THEN
        try_cast(regexp_replace(code, '\\.0$', '') AS USMALLINT)
    END AS status_code,
    CASE WHEN {valid["invalid_document_size"]} THEN
        try_cast(regexp_replace(size, '\\.0$', '') AS UBIGINT)
    END AS document_size,
    CASE WHEN {valid["invalid_index_flag"]} THEN idx IN ('1', '1.0') END AS is_index,
    CASE WHEN {valid["invalid_no_referrer_flag"]} THEN
        norefer IN ('1', '1.0')
    END AS no_referrer,
    CASE WHEN {valid["invalid_no_user_agent_flag"]} THEN
        noagent IN ('1', '1.0')
    END AS no_user_agent,
    CASE WHEN {valid["invalid_find_code"]} THEN
        try_cast(regexp_replace(find, '\\.0$', '') AS UTINYINT)
    END AS find_code,
    CASE WHEN {valid["invalid_crawler_flag"]} THEN
        crawler IN ('1', '1.0')
    END AS is_crawler,
    browser AS browser_code,
    ({invalid_mask})::USMALLINT AS invalid_mask
FROM raw
ORDER BY row_ordinal
""".strip()


def _inspect_parquet(
    connection: duckdb.DuckDBPyConnection,
    parquet_path: Path,
    partition_date: date,
) -> dict[str, Any]:
    path = _sql_string(parquet_path)
    bit_counts = ",\n".join(
        f"count_if((invalid_mask & {bit}) <> 0) AS {name}" for name, bit in _INVALID_BITS.items()
    )
    row = connection.execute(
        "WITH numbered AS (\n"
        "  SELECT *, row_number() OVER ()::UBIGINT AS observed_ordinal\n"
        f"  FROM read_parquet({path})\n"
        ")\n"
        "SELECT count(*) AS row_count, min(row_ordinal) AS first_row_ordinal, "
        "max(row_ordinal) AS last_row_ordinal,\n"
        "count_if(row_ordinal IS NULL OR row_ordinal <> observed_ordinal) "
        "AS ordinal_sequence_mismatch_count,\n"
        "count_if(source_partition_date IS NULL OR source_partition_date <> "
        + _sql_string(partition_date.isoformat())
        + "::DATE) AS partition_date_mismatch_count,\n"
        "count_if(invalid_mask IS NULL OR invalid_mask <> 0) AS rows_with_any_invalid,\n"
        f"{bit_counts}\nFROM numbered"
    ).fetchone()
    if row is None:
        raise ValueError("SEC log Parquet inspection returned no result")
    names = [item[0] for item in connection.description]
    values = dict(zip(names, row, strict=True))
    invalid_keys = ("rows_with_any_invalid", *_INVALID_BITS.keys())
    invalid_counts = {name: int(values.pop(name)) for name in invalid_keys}
    described = connection.execute(f"DESCRIBE SELECT * FROM read_parquet({path})").fetchall()
    columns = tuple(
        SECLogParquetColumn(
            name=str(item[0]),
            duckdb_type=str(item[1]),
            nullable=str(item[2]).upper() == "YES",
        )
        for item in described
    )
    metadata = connection.execute(
        f"SELECT count(DISTINCT row_group_id) FROM parquet_metadata({path})"
    ).fetchone()
    if metadata is None:
        raise ValueError("SEC log Parquet metadata returned no result")
    return {
        "row_count": int(values["row_count"]),
        "first_row_ordinal": int(values["first_row_ordinal"]),
        "last_row_ordinal": int(values["last_row_ordinal"]),
        "ordinal_sequence_mismatch_count": int(values["ordinal_sequence_mismatch_count"]),
        "partition_date_mismatch_count": int(values["partition_date_mismatch_count"]),
        "invalid_counts": SECLogInvalidCounts.model_validate(invalid_counts),
        "columns": columns,
        "row_group_count": int(metadata[0]),
    }


def _validity_sql(partition_date: date) -> dict[str, str]:
    decimal_integer = r"^[0-9]+(?:\.0)?$"
    signed_decimal_integer = r"^-?[0-9]+(?:\.0)?$"
    partition = _sql_string(partition_date.isoformat())
    binary_values = "('0', '1', '0.0', '1.0')"
    browsers = "(" + ", ".join(_sql_string(value) for value in _BROWSER_CODES) + ")"
    return {
        "blank_ip": "ip <> ''",
        "invalid_or_mismatched_date": f"date = {partition}",
        "invalid_time": "try_strptime(time, '%H:%M:%S') IS NOT NULL",
        "invalid_zone": (
            f"regexp_full_match(zone, {_sql_string(signed_decimal_integer)}) "
            "AND try_cast(regexp_replace(zone, '\\.0$', '') AS SMALLINT) IS NOT NULL"
        ),
        "invalid_cik": (
            f"regexp_full_match(cik, {_sql_string(decimal_integer)}) "
            "AND try_cast(regexp_replace(cik, '\\.0$', '') AS UBIGINT) IS NOT NULL"
        ),
        "invalid_accession": ("regexp_full_match(accession, '^[0-9]{10}-[0-9]{2}-[0-9]{6}$')"),
        "blank_document": "extention <> ''",
        "invalid_status_code": (
            f"regexp_full_match(code, {_sql_string(decimal_integer)}) "
            "AND try_cast(regexp_replace(code, '\\.0$', '') AS USMALLINT) IS NOT NULL"
        ),
        "invalid_document_size": (
            f"regexp_full_match(size, {_sql_string(decimal_integer)}) "
            "AND try_cast(regexp_replace(size, '\\.0$', '') AS UBIGINT) IS NOT NULL"
        ),
        "invalid_index_flag": f"idx IN {binary_values}",
        "invalid_no_referrer_flag": f"norefer IN {binary_values}",
        "invalid_no_user_agent_flag": f"noagent IN {binary_values}",
        "invalid_find_code": (
            f"regexp_full_match(find, {_sql_string(decimal_integer)}) "
            "AND try_cast(regexp_replace(find, '\\.0$', '') AS UTINYINT) BETWEEN 0 AND 10"
        ),
        "invalid_crawler_flag": f"crawler IN {binary_values}",
        "invalid_browser_code": f"browser IN {browsers}",
    }


def _csv_scan_sql(path: Path) -> str:
    columns = "{" + ", ".join(f"{_sql_string(name)}: 'VARCHAR'" for name in _CSV_COLUMNS) + "}"
    force_not_null = "[" + ", ".join(_sql_string(name) for name in _CSV_COLUMNS) + "]"
    return (
        f"read_csv({_sql_string(path)}, header=true, auto_detect=false, columns={columns}, "
        f"force_not_null={force_not_null}, strict_mode=true, parallel=false, "
        "null_padding=false, ignore_errors=false, maximum_line_size=1048576)"
    )


def _compare_extraction_to_receipt(
    extracted: SECLogExtractedCSV, receipt: SECLogPartitionReceipt
) -> None:
    observed = {
        "zip_bytes": extracted.zip_bytes,
        "zip_sha256": extracted.zip_sha256,
        "archive_member_names": extracted.archive_member_names,
        "csv_member_name": extracted.member_name,
        "csv_bytes": extracted.csv_bytes,
        "csv_sha256": extracted.csv_sha256,
        "csv_crc32": extracted.csv_crc32,
        "readme_bytes": extracted.readme_bytes,
        "readme_sha256": extracted.readme_sha256,
        "readme_crc32": extracted.readme_crc32,
        "physical_line_count": extracted.physical_line_count,
    }
    expected = {name: getattr(receipt, name) for name in observed}
    if observed != expected:
        raise ValueError("SEC log verification archive extraction evidence mismatch")


def _validate_download_chain(
    download_receipt: SECLogDownloadReceipt,
    extracted: SECLogExtractedCSV,
    partition: SECLogPartition,
) -> None:
    if download_receipt.partition_date != partition.partition_date:
        raise ValueError("SEC log download receipt partition mismatch")
    if download_receipt.listed_url != partition.listed_url:
        raise ValueError("SEC log download receipt listed URL mismatch")
    if download_receipt.source_url != partition.source_url:
        raise ValueError("SEC log download receipt source URL mismatch")
    if download_receipt.list_page_url != partition.list_page_url:
        raise ValueError("SEC log download receipt list page mismatch")
    if download_receipt.archive_bytes != extracted.zip_bytes:
        raise ValueError("SEC log download receipt archive byte count mismatch")
    if download_receipt.archive_sha256 != extracted.zip_sha256:
        raise ValueError("SEC log download receipt archive hash mismatch")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


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
