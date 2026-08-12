from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import HttpUrl

from finreplay.contracts import (
    BitemporalInterval,
    BitemporalRecord,
    EvidenceClass,
    LicenseClass,
    SourceReference,
    TemporalCoverage,
)
from finreplay.engines import SourceMutationError, TimeVault

NOW = datetime(2026, 8, 12, 15, tzinfo=UTC)


def make_source(version: str, content_digit: str, retrieved_at: datetime = NOW) -> SourceReference:
    return SourceReference(
        source_id="fdic.financials",
        publisher="Federal Deposit Insurance Corporation",
        url=HttpUrl(f"https://banks.data.fdic.gov/financials/{version}.json"),
        retrieved_at=retrieved_at,
        source_version=version,
        sha256=content_digit * 64,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        vintage_as_of=retrieved_at,
        redistribution_note="Connector and derived fixture only; verify upstream terms.",
    )


def make_record(
    *,
    value: int,
    source_version: str,
    source_digit: str,
    published_at: datetime,
    available_at: datetime,
    ingested_at: datetime = NOW,
) -> BitemporalRecord:
    return BitemporalRecord(
        record_id="fdic:24735:2022-12-31:asset",
        entity_id="fdic_cert:24735",
        source=make_source(source_version, source_digit),
        interval=BitemporalInterval(
            valid_from=datetime(2022, 12, 31, tzinfo=UTC),
            published_at=published_at,
            available_at=available_at,
            ingested_at=ingested_at,
            availability_rule="FDIC quarter publication date; no backfill to report date.",
            availability_confidence=1.0,
        ),
        evidence_class=EvidenceClass.REPORTED,
        payload_schema_version="1.0.0",
        payload={"metric": "ASSET", "value_thousands_usd": value},
    )


def test_as_of_query_excludes_future_revision() -> None:
    original = make_record(
        value=211_793_000,
        source_version="2023-02-28",
        source_digit="1",
        published_at=datetime(2023, 2, 28, tzinfo=UTC),
        available_at=datetime(2023, 2, 28, 23, 59, tzinfo=UTC),
    )
    revised = make_record(
        value=212_022_000,
        source_version="2023-04-15-amended",
        source_digit="2",
        published_at=datetime(2023, 4, 15, tzinfo=UTC),
        available_at=datetime(2023, 4, 15, 23, 59, tzinfo=UTC),
    )

    with TimeVault() as vault:
        receipt = vault.append([original, revised])
        assert receipt.inserted_records == 2
        before_failure = vault.records_as_of(datetime(2023, 3, 8, 21, tzinfo=UTC))
        after_revision = vault.records_as_of(datetime(2023, 5, 1, tzinfo=UTC))
        assert before_failure[0].payload["value_thousands_usd"] == 211_793_000
        assert after_revision[0].payload["value_thousands_usd"] == 212_022_000
        history_values = [
            item.payload["value_thousands_usd"] for item in vault.history(original.record_id)
        ]
        assert history_values == [
            211_793_000,
            212_022_000,
        ]


def test_append_is_idempotent_for_exact_retry() -> None:
    record = make_record(
        value=211_793_000,
        source_version="2023-02-28",
        source_digit="3",
        published_at=datetime(2023, 2, 28, tzinfo=UTC),
        available_at=datetime(2023, 2, 28, 23, 59, tzinfo=UTC),
    )
    with TimeVault() as vault:
        first = vault.append([record])
        second = vault.append([record])
        assert first.inserted_records == 1
        assert second.inserted_records == 0
        assert second.idempotent_records == 1
        assert vault.manifest(generated_at=NOW).fact_versions == 1


def test_source_identity_cannot_mutate_record_content() -> None:
    record = make_record(
        value=211_793_000,
        source_version="2023-02-28",
        source_digit="4",
        published_at=datetime(2023, 2, 28, tzinfo=UTC),
        available_at=datetime(2023, 2, 28, 23, 59, tzinfo=UTC),
    )
    mutated = record.model_copy(update={"payload": {"metric": "ASSET", "value_thousands_usd": 1}})
    with TimeVault() as vault:
        vault.append([record])
        with pytest.raises(SourceMutationError, match="attempted to mutate"):
            vault.append([mutated])
        assert vault.history(record.record_id) == [record]


def test_append_batch_is_atomic_on_mutation() -> None:
    first = make_record(
        value=10,
        source_version="v1",
        source_digit="5",
        published_at=NOW - timedelta(days=2),
        available_at=NOW - timedelta(days=1),
    )
    second = first.model_copy(update={"record_id": "fdic:24735:2022-12-31:dep"})
    collision = first.model_copy(update={"payload": {"metric": "ASSET", "value_thousands_usd": 99}})
    with TimeVault() as vault:
        vault.append([first])
        with pytest.raises(SourceMutationError):
            vault.append([second, collision])
        assert vault.history(second.record_id) == []


def test_conflicting_duplicates_inside_one_batch_are_rejected_atomically() -> None:
    record = make_record(
        value=10,
        source_version="staged-v1",
        source_digit="f",
        published_at=NOW - timedelta(days=2),
        available_at=NOW - timedelta(days=1),
    )
    collision = record.model_copy(
        update={"payload": {"metric": "ASSET", "value_thousands_usd": 99}}
    )
    with TimeVault() as vault:
        with pytest.raises(SourceMutationError, match="conflicting staged content"):
            vault.append([record, collision])
        assert vault.manifest(generated_at=NOW).fact_versions == 0


def test_empty_append_returns_zero_receipt_without_creating_staging_views() -> None:
    with TimeVault() as vault:
        receipt = vault.append([])
        assert receipt == receipt.__class__(0, 0, 0, (), ())
        assert vault.manifest(generated_at=NOW).fact_versions == 0


def test_bulk_append_and_retry_preserve_all_distinct_records() -> None:
    template = make_record(
        value=0,
        source_version="bulk-v1",
        source_digit="0",
        published_at=NOW - timedelta(days=2),
        available_at=NOW - timedelta(days=1),
    )
    records = [
        template.model_copy(
            update={
                "record_id": f"bulk:record:{index}",
                "payload": {"metric": "ASSET", "value_thousands_usd": index},
            }
        )
        for index in range(2_000)
    ]
    with TimeVault() as vault:
        first = vault.append(records)
        second = vault.append(records)
        manifest = vault.manifest(generated_at=NOW)
    assert first.inserted_records == 2_000
    assert second.inserted_records == 0
    assert second.idempotent_records == 2_000
    assert manifest.distinct_records == 2_000
    assert manifest.fact_versions == 2_000


def test_valid_at_and_source_filters() -> None:
    record = make_record(
        value=10,
        source_version="v1",
        source_digit="6",
        published_at=NOW - timedelta(days=2),
        available_at=NOW - timedelta(days=1),
    )
    expired_interval = record.interval.model_copy(
        update={"valid_to": datetime(2023, 1, 1, tzinfo=UTC)}
    )
    record = record.model_copy(update={"interval": expired_interval})
    with TimeVault() as vault:
        vault.append([record])
        assert vault.records_as_of(NOW, valid_at=datetime(2022, 12, 31, tzinfo=UTC))
        assert not vault.records_as_of(NOW, valid_at=datetime(2023, 2, 1, tzinfo=UTC))
        assert not vault.records_as_of(NOW, source_ids=["sec.companyfacts"])


def test_manifest_is_measured_and_stable(tmp_path: Path) -> None:
    database = tmp_path / "timevault.duckdb"
    record = make_record(
        value=10,
        source_version="v1",
        source_digit="7",
        published_at=NOW - timedelta(days=2),
        available_at=NOW - timedelta(days=1),
    )
    with TimeVault(database) as vault:
        vault.append([record])
        first = vault.manifest(generated_at=NOW)
        second = vault.manifest(generated_at=NOW + timedelta(seconds=1))
    assert first.distinct_records == 1
    assert first.fact_versions == 1
    assert first.source_artifacts == 1
    assert first.retrieval_receipts == 1
    assert first.database_bytes > 0
    assert first.fact_set_sha256 == second.fact_set_sha256
    assert first.generated_at != second.generated_at


def test_retrieval_receipt_is_independent_of_source_artifact() -> None:
    record = make_record(
        value=10,
        source_version="v1",
        source_digit="8",
        published_at=NOW - timedelta(days=2),
        available_at=NOW - timedelta(days=1),
    )
    later_source = record.source.model_copy(update={"retrieved_at": NOW + timedelta(hours=1)})
    later_record = record.model_copy(update={"source": later_source})
    with TimeVault() as vault:
        vault.append([record])
        receipt = vault.append([later_record])
        manifest = vault.manifest(generated_at=NOW)
    assert receipt.idempotent_records == 1
    assert manifest.source_artifacts == 1
    assert manifest.retrieval_receipts == 2


def test_as_of_requires_timezone_aware_inputs() -> None:
    with TimeVault() as vault, pytest.raises(ValueError, match="decision_time"):
        vault.records_as_of(NOW.replace(tzinfo=None))


def test_latest_only_source_is_excluded_from_historical_query_by_default() -> None:
    record = make_record(
        value=10,
        source_version="latest-index",
        source_digit="9",
        published_at=NOW,
        available_at=NOW,
    )
    latest_source = record.source.model_copy(
        update={
            "temporal_coverage": TemporalCoverage.LATEST_ONLY,
            "vintage_as_of": None,
        }
    )
    record = record.model_copy(update={"source": latest_source})
    with TimeVault() as vault:
        vault.append([record])
        assert vault.records_as_of(NOW + timedelta(seconds=1)) == []
        assert vault.records_as_of(NOW + timedelta(seconds=1), allow_latest_only=True) == [
            record
        ]


def test_exact_latest_only_artifact_retry_preserves_first_observed_time() -> None:
    record = make_record(
        value=10,
        source_version="latest-retry-index",
        source_digit="b",
        published_at=NOW,
        available_at=NOW,
    )
    latest_source = record.source.model_copy(
        update={"temporal_coverage": TemporalCoverage.LATEST_ONLY, "vintage_as_of": None}
    )
    record = record.model_copy(update={"source": latest_source})
    later_source = latest_source.model_copy(update={"retrieved_at": NOW + timedelta(hours=1)})
    later_interval = record.interval.model_copy(
        update={
            "published_at": NOW + timedelta(hours=1),
            "available_at": NOW + timedelta(hours=1),
            "ingested_at": NOW + timedelta(hours=1),
        }
    )
    retry = record.model_copy(update={"source": later_source, "interval": later_interval})
    with TimeVault() as vault:
        vault.append([record])
        receipt = vault.append([retry])
        stored = vault.records_as_of(
            NOW + timedelta(hours=2),
            allow_latest_only=True,
        )[0]
    assert receipt.idempotent_records == 1
    assert stored.interval.available_at == NOW


def test_exact_immutable_event_retry_does_not_double_count() -> None:
    record = make_record(
        value=10,
        source_version="immutable-event-ledger-v1",
        source_digit="c",
        published_at=NOW - timedelta(days=2),
        available_at=NOW - timedelta(days=2),
    )
    immutable_source = record.source.model_copy(
        update={
            "temporal_coverage": TemporalCoverage.IMMUTABLE_EVENT,
            "vintage_as_of": NOW,
        }
    )
    record = record.model_copy(update={"source": immutable_source})
    later_source = immutable_source.model_copy(
        update={
            "retrieved_at": NOW + timedelta(hours=1),
            "vintage_as_of": NOW + timedelta(hours=1),
        }
    )
    later_interval = record.interval.model_copy(
        update={"ingested_at": NOW + timedelta(hours=1)}
    )
    retry = record.model_copy(update={"source": later_source, "interval": later_interval})
    with TimeVault() as vault:
        vault.append([record])
        receipt = vault.append([retry])
        manifest = vault.manifest(generated_at=NOW)
    assert receipt.idempotent_records == 1
    assert manifest.distinct_records == 1
    assert manifest.fact_versions == 1


def test_new_versioned_snapshot_remains_a_distinct_fact_version() -> None:
    record = make_record(
        value=10,
        source_version="versioned-snapshot-v1",
        source_digit="d",
        published_at=NOW - timedelta(days=2),
        available_at=NOW - timedelta(days=2),
    )
    later_source = record.source.model_copy(
        update={
            "source_version": "versioned-snapshot-v2",
            "sha256": "e" * 64,
            "retrieved_at": NOW + timedelta(hours=1),
            "vintage_as_of": NOW + timedelta(hours=1),
        }
    )
    later_interval = record.interval.model_copy(
        update={
            "revised_at": NOW - timedelta(days=1),
            "ingested_at": NOW + timedelta(hours=1),
        }
    )
    revision = record.model_copy(
        update={
            "source": later_source,
            "interval": later_interval,
            "payload": {"metric": "ASSET", "value_thousands_usd": 11},
        }
    )
    with TimeVault() as vault:
        vault.append([record, revision])
        manifest = vault.manifest(generated_at=NOW)
    assert manifest.distinct_records == 1
    assert manifest.fact_versions == 2


def test_manifest_requires_timezone_aware_generated_at() -> None:
    with TimeVault() as vault, pytest.raises(ValueError, match="generated_at"):
        vault.manifest(generated_at=NOW.replace(tzinfo=None))


def test_timevault_pins_session_timezone_to_utc() -> None:
    record = make_record(
        value=10,
        source_version="utc-v1",
        source_digit="a",
        published_at=NOW - timedelta(days=2),
        available_at=NOW - timedelta(days=1),
    )
    with TimeVault() as vault:
        vault.append([record])
        assert vault.history(record.record_id)[0].interval.valid_from.hour == 0
