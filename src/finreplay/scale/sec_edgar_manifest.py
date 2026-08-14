"""Cross-partition non-duplication and billion-row evidence for the SEC log lake."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationInfo, model_validator

from finreplay.scale.sec_edgar_download import SECLogDownloadReceipt
from finreplay.scale.sec_edgar_lake import (
    SECLogPartitionReceipt,
    verify_sec_log_partition_receipt,
)
from finreplay.scale.sec_edgar_logs import SECLogInventoryLock, SECLogPartition

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SECLogInventoryReference(_StrictModel):
    year: int = Field(ge=2003, le=2100)
    list_page_url: HttpUrl
    list_page_sha256: str = Field(pattern=_SHA256_PATTERN)
    inventory_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    retrieved_at: datetime
    listed_partition_count: int = Field(gt=0, le=366)


class SECLogScalePartitionSummary(_StrictModel):
    partition_date: date
    listed_url: HttpUrl
    source_url: HttpUrl
    list_page_url: HttpUrl
    inventory_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    download_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    partition_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_coordinate_sha256: str = Field(pattern=_SHA256_PATTERN)
    zip_sha256: str = Field(pattern=_SHA256_PATTERN)
    zip_bytes: int = Field(gt=0)
    parquet_sha256: str = Field(pattern=_SHA256_PATTERN)
    parquet_bytes: int = Field(gt=0)
    physical_row_count: int = Field(gt=0)
    invalid_row_count: int = Field(ge=0)
    archive_retrieved_at: datetime

    @model_validator(mode="after")
    def validate_summary(self) -> SECLogScalePartitionSummary:
        SECLogPartition.model_validate(
            {
                "partition_date": self.partition_date,
                "listed_url": self.listed_url,
                "source_url": self.source_url,
                "list_page_url": self.list_page_url,
            }
        )
        _require_aware(self.archive_retrieved_at, "archive_retrieved_at")
        if self.invalid_row_count > self.physical_row_count:
            raise ValueError("SEC log invalid row count exceeds physical row count")
        return self


class SECLogScaleManifest(_StrictModel):
    """Self-hashed sum of unique, non-overlapping, content-addressed daily partitions."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    generated_at: datetime
    code_revision: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    target_physical_row_count: int = Field(gt=0)
    target_met: bool
    physical_row_identity_definition: Literal[
        "unique_source_zip_sha256_plus_one_based_csv_data_row_ordinal"
    ] = "unique_source_zip_sha256_plus_one_based_csv_data_row_ordinal"
    inventory_references: tuple[SECLogInventoryReference, ...] = Field(min_length=1)
    partitions: tuple[SECLogScalePartitionSummary, ...] = Field(min_length=1)
    partition_count: int = Field(gt=0)
    total_distinct_physical_rows: int = Field(gt=0)
    total_invalid_rows: int = Field(ge=0)
    total_source_zip_bytes: int = Field(gt=0)
    total_parquet_bytes: int = Field(gt=0)
    claim_boundary: str = Field(min_length=400, max_length=4_000)
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_manifest(self, info: ValidationInfo) -> SECLogScaleManifest:
        _require_aware(self.generated_at, "generated_at")
        if self.partition_count != len(self.partitions):
            raise ValueError("SEC log manifest partition_count mismatch")
        partition_days = tuple(item.partition_date.isoformat() for item in self.partitions)
        if partition_days != tuple(sorted(partition_days)):
            raise ValueError("SEC log manifest partitions must be sorted by date")
        _require_unique(partition_days, "partition dates")
        _require_unique(
            tuple(str(item.source_url) for item in self.partitions), "partition source URLs"
        )
        _require_unique(tuple(item.zip_sha256 for item in self.partitions), "source ZIP hashes")
        _require_unique(
            tuple(item.source_coordinate_sha256 for item in self.partitions),
            "source coordinate hashes",
        )
        _require_unique(
            tuple(item.partition_receipt_sha256 for item in self.partitions),
            "partition receipt hashes",
        )
        if self.total_distinct_physical_rows != sum(
            item.physical_row_count for item in self.partitions
        ):
            raise ValueError("SEC log manifest total physical row count mismatch")
        if self.total_invalid_rows != sum(item.invalid_row_count for item in self.partitions):
            raise ValueError("SEC log manifest total invalid row count mismatch")
        if self.total_source_zip_bytes != sum(item.zip_bytes for item in self.partitions):
            raise ValueError("SEC log manifest total ZIP byte count mismatch")
        if self.total_parquet_bytes != sum(item.parquet_bytes for item in self.partitions):
            raise ValueError("SEC log manifest total Parquet byte count mismatch")
        if self.target_met != (self.total_distinct_physical_rows >= self.target_physical_row_count):
            raise ValueError("SEC log manifest target_met is inconsistent")
        inventory_by_hash = {
            reference.inventory_lock_sha256: reference for reference in self.inventory_references
        }
        if any(item.inventory_lock_sha256 not in inventory_by_hash for item in self.partitions):
            raise ValueError("SEC log partition references an unknown inventory lock")
        if len(inventory_by_hash) != len(self.inventory_references):
            raise ValueError("SEC log inventory references must be unique")
        if any(
            item.list_page_url != inventory_by_hash[item.inventory_lock_sha256].list_page_url
            for item in self.partitions
        ):
            raise ValueError("SEC log partition list page differs from its inventory reference")
        for reference in self.inventory_references:
            _require_aware(reference.retrieved_at, "inventory retrieved_at")
        payload = self.model_dump(mode="json", exclude={"manifest_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.manifest_sha256:
            raise ValueError("SEC log scale manifest_sha256 mismatch")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> SECLogScaleManifest:
        values = dict(payload)
        values.pop("manifest_sha256", None)
        normalized = cls.model_validate(
            {**values, "manifest_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"manifest_sha256"})
        return cls.model_validate({**normalized, "manifest_sha256": _hash(normalized)})


def build_sec_log_scale_manifest(
    *,
    inventory_locks: Sequence[SECLogInventoryLock],
    partition_receipts: Sequence[SECLogPartitionReceipt],
    target_physical_row_count: int,
    code_revision: str,
    generated_at: datetime,
) -> SECLogScaleManifest:
    """Build a fail-closed cross-partition sum without estimates or multipliers."""

    if not inventory_locks:
        raise ValueError("at least one SEC log inventory lock is required")
    if not partition_receipts:
        raise ValueError("at least one SEC log partition receipt is required")
    inventory_by_partition: dict[tuple[str, str, str], SECLogInventoryLock] = {}
    references: list[SECLogInventoryReference] = []
    for lock in inventory_locks:
        year = int(str(lock.list_page_url).removesuffix(".html").rsplit("edgar", 1)[1])
        references.append(
            SECLogInventoryReference(
                year=year,
                list_page_url=lock.list_page_url,
                list_page_sha256=lock.list_page_sha256,
                inventory_lock_sha256=lock.lock_sha256,
                retrieved_at=lock.retrieved_at,
                listed_partition_count=len(lock.partitions),
            )
        )
        for partition in lock.partitions:
            key = (
                partition.partition_date.isoformat(),
                str(partition.listed_url),
                str(partition.source_url),
            )
            if key in inventory_by_partition:
                raise ValueError("SEC log inventory locks overlap on one exact partition")
            inventory_by_partition[key] = lock
    summaries: list[SECLogScalePartitionSummary] = []
    for receipt in sorted(partition_receipts, key=lambda item: item.partition_date):
        key = (
            receipt.partition_date.isoformat(),
            str(receipt.listed_url),
            str(receipt.source_url),
        )
        matched_lock = inventory_by_partition.get(key)
        if matched_lock is None or receipt.list_page_url != matched_lock.list_page_url:
            raise ValueError("SEC log partition receipt is absent from supplied inventory locks")
        summaries.append(
            SECLogScalePartitionSummary(
                partition_date=receipt.partition_date,
                listed_url=receipt.listed_url,
                source_url=receipt.source_url,
                list_page_url=receipt.list_page_url,
                inventory_lock_sha256=matched_lock.lock_sha256,
                download_receipt_sha256=receipt.download_receipt_sha256,
                partition_receipt_sha256=receipt.receipt_sha256,
                source_coordinate_sha256=receipt.source_coordinate_sha256,
                zip_sha256=receipt.zip_sha256,
                zip_bytes=receipt.zip_bytes,
                parquet_sha256=receipt.parquet_sha256,
                parquet_bytes=receipt.parquet_bytes,
                physical_row_count=receipt.data_row_count,
                invalid_row_count=receipt.invalid_counts.rows_with_any_invalid,
                archive_retrieved_at=receipt.archive_retrieved_at,
            )
        )
    total_rows = sum(item.physical_row_count for item in summaries)
    return SECLogScaleManifest.create(
        {
            "schema_version": "1.0.0",
            "generated_at": generated_at,
            "code_revision": code_revision,
            "target_physical_row_count": target_physical_row_count,
            "target_met": total_rows >= target_physical_row_count,
            "physical_row_identity_definition": (
                "unique_source_zip_sha256_plus_one_based_csv_data_row_ordinal"
            ),
            "inventory_references": [
                item.model_dump(mode="json")
                for item in sorted(references, key=lambda reference: reference.year)
            ],
            "partitions": [item.model_dump(mode="json") for item in summaries],
            "partition_count": len(summaries),
            "total_distinct_physical_rows": total_rows,
            "total_invalid_rows": sum(item.invalid_row_count for item in summaries),
            "total_source_zip_bytes": sum(item.zip_bytes for item in summaries),
            "total_parquet_bytes": sum(item.parquet_bytes for item in summaries),
            "claim_boundary": (
                "This manifest sums only measured physical CSV data rows from unique official SEC "
                "daily ZIP hashes whose exact one-based row-coordinate ranges were materialized "
                "and sealed by partition receipts. It rejects duplicate dates, URLs, ZIP hashes, "
                "coordinate hashes, and partition-receipt hashes; it applies no synthetic "
                "multiplication, extrapolation, semantic deduplication, or estimated row counts. A "
                "repeated request in one official source file remains a distinct physical log row, "
                "but the count is not a count of unique users, filings, trades, decisions, or "
                "customers. SEC warns that lost or damaged files and extraction limitations may "
                "make its logs incomplete. This local, internally reproducible scale proof does "
                "not establish external review, production deployment, public-demo availability, "
                "adoption, governance certification, or real-world impact. target_met is purely "
                "the "
                "comparison of the exact summed row count with the declared numeric target."
            ),
        }
    )


def verify_sec_log_scale_manifest(
    manifest: SECLogScaleManifest,
    *,
    inventory_locks: Sequence[SECLogInventoryLock],
    partition_receipts: Sequence[SECLogPartitionReceipt],
    download_receipts: Sequence[SECLogDownloadReceipt],
    archive_directory: Path,
    parquet_directory: Path,
    deep: bool,
) -> None:
    """Rebuild the semantic sum and optionally re-read every source/output byte."""

    rebuilt = build_sec_log_scale_manifest(
        inventory_locks=inventory_locks,
        partition_receipts=partition_receipts,
        target_physical_row_count=manifest.target_physical_row_count,
        code_revision=manifest.code_revision,
        generated_at=manifest.generated_at,
    )
    if rebuilt != manifest:
        raise ValueError("SEC log scale manifest differs from fresh semantic rebuild")
    downloads = {item.receipt_sha256: item for item in download_receipts}
    receipts = {item.receipt_sha256: item for item in partition_receipts}
    if len(downloads) != len(download_receipts):
        raise ValueError("SEC log download receipt hashes must be unique")
    if len(receipts) != len(partition_receipts):
        raise ValueError("SEC log partition receipt hashes must be unique")
    for summary in manifest.partitions:
        receipt = receipts.get(summary.partition_receipt_sha256)
        download = downloads.get(summary.download_receipt_sha256)
        if receipt is None or download is None:
            raise ValueError("SEC log scale manifest receipt chain is incomplete")
        if receipt.download_receipt_sha256 != download.receipt_sha256:
            raise ValueError("SEC log partition/download receipt chain mismatch")
        if deep:
            verify_sec_log_partition_receipt(
                receipt,
                download_receipt=download,
                archive_path=archive_directory / receipt.zip_filename,
                parquet_path=parquet_directory / receipt.parquet_filename,
            )


def write_sec_log_scale_manifest(manifest: SECLogScaleManifest, path: Path) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    os.close(descriptor)
    try:
        temporary.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with temporary.open("rb") as output:
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_sec_log_scale_manifest(path: Path) -> SECLogScaleManifest:
    try:
        return SECLogScaleManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid SEC log scale manifest: {path}") from error


def _require_unique(values: tuple[str, ...], name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"SEC log manifest {name} must be unique")


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
