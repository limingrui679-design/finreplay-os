"""Append-only bitemporal fact store backed by DuckDB."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import TypeAdapter

from finreplay.contracts import (
    BitemporalInterval,
    BitemporalRecord,
    EvidenceClass,
    LicenseClass,
    SourceReference,
    TemporalCoverage,
)


class SourceMutationError(RuntimeError):
    """Raised when identical source identity tries to supply different record content."""


@dataclass(frozen=True, slots=True)
class AppendReceipt:
    attempted_records: int
    inserted_records: int
    idempotent_records: int
    artifact_ids: tuple[str, ...]
    fact_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TimeVaultManifest:
    distinct_records: int
    fact_versions: int
    source_artifacts: int
    retrieval_receipts: int
    database_bytes: int
    fact_set_sha256: str
    generated_at: datetime


class TimeVault:
    """Store immutable financial facts and query only versions knowable at a past time."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        self._connection = duckdb.connect(self.database)
        # DuckDB renders TIMESTAMPTZ in the session zone. Pin UTC so persisted facts and
        # manifests remain byte/semantically comparable across developer machines.
        self._connection.execute("SET TimeZone = 'UTC'")
        self._initialize_schema()

    def __enter__(self) -> TimeVault:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def append(self, records: Iterable[BitemporalRecord]) -> AppendReceipt:
        """Append validated records transactionally; exact retries are idempotent."""

        materialized = list(records)
        prepared = [self._prepare(record) for record in materialized]
        artifact_ids = [item.artifact_id for item in prepared]
        fact_hashes = [item.fact_hash for item in prepared]
        if not prepared:
            return AppendReceipt(0, 0, 0, (), ())

        self._connection.execute("BEGIN TRANSACTION")
        try:
            self._register_staging_tables(prepared)
            self._reject_staged_mutations()
            before = int(self._connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
            self._connection.execute(
                "INSERT INTO source_artifacts SELECT * FROM _staged_sources ON CONFLICT DO NOTHING"
            )
            self._connection.execute(
                "INSERT INTO source_receipts SELECT * FROM _staged_receipts ON CONFLICT DO NOTHING"
            )
            self._connection.execute(
                """
                INSERT INTO facts
                SELECT
                    record_id,
                    entity_id,
                    artifact_id,
                    receipt_id,
                    valid_from,
                    valid_to,
                    published_at,
                    available_at,
                    revised_at,
                    ingested_at,
                    availability_rule,
                    availability_confidence,
                    evidence_class,
                    payload_schema_version,
                    payload_json::JSON,
                    fact_hash
                FROM _staged_facts
                ON CONFLICT DO NOTHING
                """
            )
            after = int(self._connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
            inserted = after - before
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        finally:
            self._clear_staging_tables()

        return AppendReceipt(
            attempted_records=len(materialized),
            inserted_records=inserted,
            idempotent_records=len(materialized) - inserted,
            artifact_ids=tuple(sorted(set(artifact_ids))),
            fact_hashes=tuple(fact_hashes),
        )

    def records_as_of(
        self,
        decision_time: datetime,
        *,
        valid_at: datetime | None = None,
        source_ids: Iterable[str] | None = None,
        allow_latest_only: bool = False,
    ) -> list[BitemporalRecord]:
        """Return the newest fact version available by a historical decision time."""

        _require_aware(decision_time, "decision_time")
        if valid_at is not None:
            _require_aware(valid_at, "valid_at")

        predicates = ["f.available_at <= ?"]
        parameters: list[Any] = [decision_time]
        if not allow_latest_only:
            predicates.append("a.temporal_coverage != 'latest_only'")
        if valid_at is not None:
            predicates.extend(["f.valid_from <= ?", "(f.valid_to IS NULL OR f.valid_to > ?)"])
            parameters.extend([valid_at, valid_at])
        selected_sources = tuple(source_ids or ())
        if selected_sources:
            placeholders = ", ".join("?" for _ in selected_sources)
            predicates.append(f"a.source_id IN ({placeholders})")
            parameters.extend(selected_sources)
        where_clause = " AND ".join(predicates)
        rows = self._connection.execute(
            f"""
            WITH eligible AS (
                SELECT
                    f.*,
                    a.source_id,
                    a.publisher,
                    a.url,
                    a.source_version,
                    a.sha256,
                    a.license_class,
                    a.temporal_coverage,
                    a.vintage_as_of,
                    a.redistribution_note,
                    r.retrieved_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY f.record_id
                        ORDER BY
                            f.available_at DESC,
                            f.revised_at DESC NULLS LAST,
                            f.ingested_at DESC,
                            f.fact_hash DESC
                    ) AS version_rank
                FROM facts AS f
                JOIN source_artifacts AS a USING (artifact_id)
                JOIN source_receipts AS r USING (receipt_id)
                WHERE {where_clause}
            )
            SELECT * EXCLUDE (version_rank)
            FROM eligible
            WHERE version_rank = 1
            ORDER BY record_id
            """,
            parameters,
        ).fetchall()
        columns = [column[0] for column in self._connection.description]
        return [self._row_to_record(dict(zip(columns, row, strict=True))) for row in rows]

    def history(self, record_id: str) -> list[BitemporalRecord]:
        """Return every immutable version of a logical record in availability order."""

        rows = self._connection.execute(
            """
            SELECT
                f.*,
                a.source_id,
                a.publisher,
                a.url,
                a.source_version,
                a.sha256,
                a.license_class,
                a.temporal_coverage,
                a.vintage_as_of,
                a.redistribution_note,
                r.retrieved_at
            FROM facts AS f
            JOIN source_artifacts AS a USING (artifact_id)
            JOIN source_receipts AS r USING (receipt_id)
            WHERE f.record_id = ?
            ORDER BY f.available_at, f.ingested_at, f.fact_hash
            """,
            [record_id],
        ).fetchall()
        columns = [column[0] for column in self._connection.description]
        return [self._row_to_record(dict(zip(columns, row, strict=True))) for row in rows]

    def manifest(self, *, generated_at: datetime | None = None) -> TimeVaultManifest:
        """Measure stored facts; do not infer scale from configuration or documentation."""

        generated = generated_at or datetime.now(UTC)
        _require_aware(generated, "generated_at")
        distinct_records, fact_versions = self._connection.execute(
            "SELECT COUNT(DISTINCT record_id), COUNT(*) FROM facts"
        ).fetchone()
        source_artifacts = self._connection.execute(
            "SELECT COUNT(*) FROM source_artifacts"
        ).fetchone()[0]
        retrieval_receipts = self._connection.execute(
            "SELECT COUNT(*) FROM source_receipts"
        ).fetchone()[0]
        fact_hashes = [row[0] for row in self._connection.execute(
            "SELECT fact_hash FROM facts ORDER BY fact_hash"
        ).fetchall()]
        fact_set_hash = hashlib.sha256("\n".join(fact_hashes).encode()).hexdigest()
        database_bytes = 0
        if self.database != ":memory:":
            path = Path(self.database)
            if path.exists():
                self._connection.execute("CHECKPOINT")
                database_bytes = path.stat().st_size
        return TimeVaultManifest(
            distinct_records=int(distinct_records),
            fact_versions=int(fact_versions),
            source_artifacts=int(source_artifacts),
            retrieval_receipts=int(retrieval_receipts),
            database_bytes=database_bytes,
            fact_set_sha256=fact_set_hash,
            generated_at=generated,
        )

    def _initialize_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_artifacts (
                artifact_id VARCHAR PRIMARY KEY,
                source_id VARCHAR NOT NULL,
                publisher VARCHAR NOT NULL,
                url VARCHAR NOT NULL,
                source_version VARCHAR NOT NULL,
                sha256 VARCHAR NOT NULL,
                license_class VARCHAR NOT NULL,
                temporal_coverage VARCHAR NOT NULL,
                vintage_as_of TIMESTAMPTZ,
                redistribution_note VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_receipts (
                receipt_id VARCHAR PRIMARY KEY,
                artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
                retrieved_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE IF NOT EXISTS facts (
                record_id VARCHAR NOT NULL,
                entity_id VARCHAR,
                artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
                receipt_id VARCHAR NOT NULL REFERENCES source_receipts(receipt_id),
                valid_from TIMESTAMPTZ NOT NULL,
                valid_to TIMESTAMPTZ,
                published_at TIMESTAMPTZ NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                revised_at TIMESTAMPTZ,
                ingested_at TIMESTAMPTZ NOT NULL,
                availability_rule VARCHAR NOT NULL,
                availability_confidence DOUBLE NOT NULL,
                evidence_class VARCHAR NOT NULL,
                payload_schema_version VARCHAR NOT NULL,
                payload_json JSON NOT NULL,
                fact_hash VARCHAR NOT NULL,
                PRIMARY KEY (record_id, artifact_id),
                UNIQUE (fact_hash),
                CHECK (available_at >= published_at),
                CHECK (ingested_at >= available_at),
                CHECK (valid_to IS NULL OR valid_to >= valid_from),
                CHECK (availability_confidence BETWEEN 0 AND 1)
            );
            """
        )

    def _prepare(self, record: BitemporalRecord) -> _PreparedRecord:
        source_payload = record.source.model_dump(mode="json", exclude={"retrieved_at"})
        if record.source.temporal_coverage is TemporalCoverage.IMMUTABLE_EVENT:
            # A current event ledger may be retrieved repeatedly. Identical content and source
            # version are one artifact; the first retrieval remains a separate receipt.
            source_payload["vintage_as_of"] = "content_addressed_event_ledger"
        artifact_id = hashlib.sha256(_canonical_json(source_payload).encode()).hexdigest()
        receipt_payload = {
            "artifact_id": artifact_id,
            "retrieved_at": record.source.retrieved_at.isoformat(),
        }
        receipt_id = hashlib.sha256(_canonical_json(receipt_payload).encode()).hexdigest()
        fact_payload = record.model_dump(mode="json")
        fact_payload["source"] = {"artifact_id": artifact_id}
        # Ingestion time is operational provenance, not the identity of a financial fact.
        fact_payload["interval"]["ingested_at"] = "first_ingested"
        if record.source.temporal_coverage is TemporalCoverage.LATEST_ONLY:
            # The first retrieval establishes availability, but an exact-content retry hours later
            # must not create a mutation. Preserve the first stored timestamps while hashing only
            # deterministic source content and its economic-time interpretation.
            fact_payload["interval"]["published_at"] = "first_observed"
            fact_payload["interval"]["available_at"] = "first_observed"
        fact_hash = hashlib.sha256(_canonical_json(fact_payload).encode()).hexdigest()
        return _PreparedRecord(
            record=record,
            artifact_id=artifact_id,
            receipt_id=receipt_id,
            fact_hash=fact_hash,
        )

    def _register_staging_tables(self, prepared: list[_PreparedRecord]) -> None:
        pa: Any = importlib.import_module("pyarrow")
        source_rows: dict[str, dict[str, Any]] = {}
        receipt_rows: dict[str, dict[str, Any]] = {}
        fact_rows: list[dict[str, Any]] = []
        for item in prepared:
            record = item.record
            source = record.source
            source_rows.setdefault(
                item.artifact_id,
                {
                    "artifact_id": item.artifact_id,
                    "source_id": source.source_id,
                    "publisher": source.publisher,
                    "url": str(source.url),
                    "source_version": source.source_version,
                    "sha256": source.sha256,
                    "license_class": source.license_class.value,
                    "temporal_coverage": source.temporal_coverage.value,
                    "vintage_as_of": source.vintage_as_of,
                    "redistribution_note": source.redistribution_note,
                },
            )
            receipt_rows.setdefault(
                item.receipt_id,
                {
                    "receipt_id": item.receipt_id,
                    "artifact_id": item.artifact_id,
                    "retrieved_at": source.retrieved_at,
                },
            )
            fact_rows.append(
                {
                    "record_id": record.record_id,
                    "entity_id": record.entity_id,
                    "artifact_id": item.artifact_id,
                    "receipt_id": item.receipt_id,
                    "valid_from": record.interval.valid_from,
                    "valid_to": record.interval.valid_to,
                    "published_at": record.interval.published_at,
                    "available_at": record.interval.available_at,
                    "revised_at": record.interval.revised_at,
                    "ingested_at": record.interval.ingested_at,
                    "availability_rule": record.interval.availability_rule,
                    "availability_confidence": record.interval.availability_confidence,
                    "evidence_class": record.evidence_class.value,
                    "payload_schema_version": record.payload_schema_version,
                    "payload_json": _canonical_json(record.payload),
                    "fact_hash": item.fact_hash,
                }
            )
        self._connection.register(
            "_staged_sources", pa.Table.from_pylist(list(source_rows.values()))
        )
        self._connection.register(
            "_staged_receipts", pa.Table.from_pylist(list(receipt_rows.values()))
        )
        self._connection.register("_staged_facts", pa.Table.from_pylist(fact_rows))

    def _reject_staged_mutations(self) -> None:
        staged_collision = self._connection.execute(
            """
            SELECT record_id, artifact_id, MIN(fact_hash), MAX(fact_hash)
            FROM _staged_facts
            GROUP BY record_id, artifact_id
            HAVING COUNT(DISTINCT fact_hash) > 1
            LIMIT 1
            """
        ).fetchone()
        if staged_collision is not None:
            raise SourceMutationError(
                "one source artifact produced conflicting staged content for record "
                f"{staged_collision[0]!r}: {staged_collision[2]} != {staged_collision[3]}"
            )
        existing_collision = self._connection.execute(
            """
            SELECT s.record_id, s.fact_hash, f.fact_hash
            FROM _staged_facts AS s
            JOIN facts AS f USING (record_id, artifact_id)
            WHERE s.fact_hash != f.fact_hash
            LIMIT 1
            """
        ).fetchone()
        if existing_collision is not None:
            raise SourceMutationError(
                "source artifact attempted to mutate record "
                f"{existing_collision[0]!r}: {existing_collision[2]} != "
                f"{existing_collision[1]}"
            )

    def _clear_staging_tables(self) -> None:
        for view in ("_staged_facts", "_staged_receipts", "_staged_sources"):
            with suppress(duckdb.Error):
                self._connection.unregister(view)

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> BitemporalRecord:
        payload = row["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        source = SourceReference(
            source_id=row["source_id"],
            publisher=row["publisher"],
            url=row["url"],
            retrieved_at=row["retrieved_at"],
            source_version=row["source_version"],
            sha256=row["sha256"],
            license_class=LicenseClass(row["license_class"]),
            temporal_coverage=TemporalCoverage(row["temporal_coverage"]),
            vintage_as_of=row["vintage_as_of"],
            redistribution_note=row["redistribution_note"],
        )
        interval = BitemporalInterval(
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            published_at=row["published_at"],
            available_at=row["available_at"],
            revised_at=row["revised_at"],
            ingested_at=row["ingested_at"],
            availability_rule=row["availability_rule"],
            availability_confidence=row["availability_confidence"],
        )
        return BitemporalRecord(
            record_id=row["record_id"],
            entity_id=row["entity_id"],
            source=source,
            interval=interval,
            evidence_class=EvidenceClass(row["evidence_class"]),
            payload_schema_version=row["payload_schema_version"],
            payload=TypeAdapter(dict[str, Any]).validate_python(payload),
        )


@dataclass(frozen=True, slots=True)
class _PreparedRecord:
    record: BitemporalRecord
    artifact_id: str
    receipt_id: str
    fact_hash: str


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
