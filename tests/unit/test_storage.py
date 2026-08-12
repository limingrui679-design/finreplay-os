from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import HttpUrl

from finreplay.adapters import AdapterBatch, FetchReceipt, RawArtifact
from finreplay.contracts import TemporalCoverage
from finreplay.engines import AppendReceipt, TimeVaultManifest
from finreplay.storage import ContentAddressedStore, write_live_verification

NOW = datetime(2026, 8, 12, 18, tzinfo=UTC)


def artifact(content: bytes = b"official-response") -> RawArtifact:
    return RawArtifact(
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="application/json",
        content=content,
    )


def test_content_store_is_atomic_idempotent_and_hash_verified(tmp_path: Path) -> None:
    store = ContentAddressedStore(tmp_path / "raw")
    first = store.put(artifact())
    second = store.put(artifact())
    assert first.created is True
    assert second.created is False
    assert first.path == second.path
    assert first.path.read_bytes() == b"official-response"

    first.path.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match=r"size mismatch|hash mismatch"):
        store.put(artifact())


def test_content_store_refuses_filesystem_root() -> None:
    with pytest.raises(ValueError, match="filesystem root"):
        ContentAddressedStore(Path("/"))


def test_live_verification_receipt_is_portable_and_self_hashing(tmp_path: Path) -> None:
    raw = artifact()
    stored = ContentAddressedStore(tmp_path / "raw").put(raw)
    fetch = FetchReceipt(
        adapter_id="fdic.bankfind.financials",
        request_url=HttpUrl("https://api.fdic.gov/banks/financials?filters=CERT%3A24735"),
        retrieved_at=NOW,
        status_code=200,
        content_type="application/json",
        response_sha256=raw.sha256,
        response_bytes=len(raw.content),
        record_count=0,
        source_version="fixture-index",
        temporal_coverage=TemporalCoverage.LATEST_ONLY,
        historical_replay_eligible=False,
        warnings=("Latest-only fixture.",),
    )
    batch = AdapterBatch(records=(), receipts=(fetch,), artifacts=(raw,))
    append = AppendReceipt(
        attempted_records=2,
        inserted_records=2,
        idempotent_records=0,
        artifact_ids=("a" * 64,),
        fact_hashes=("c" * 64, "b" * 64),
    )
    manifest = TimeVaultManifest(
        distinct_records=0,
        fact_versions=0,
        source_artifacts=0,
        retrieval_receipts=0,
        database_bytes=0,
        fact_set_sha256=hashlib.sha256(b"").hexdigest(),
        generated_at=NOW,
    )
    path = write_live_verification(
        output_directory=tmp_path / "receipts",
        batch=batch,
        stored_artifacts=(stored,),
        append_receipt=append,
        vault_manifest=manifest,
    )
    payload = json.loads(path.read_text())
    claimed = payload.pop("receipt_sha256")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert claimed == hashlib.sha256(canonical).hexdigest()
    serialized = path.read_text()
    assert str(tmp_path) not in serialized
    assert payload["stored_artifacts"][0]["cache_key"].endswith(".bin")
    assert payload["schema_version"] == "1.1.0"
    assert payload["append_receipt"]["fact_hash_count"] == 2
    assert "fact_hashes" not in payload["append_receipt"]
    expected_hash = hashlib.sha256(f"{'b' * 64}\n{'c' * 64}".encode()).hexdigest()
    assert payload["append_receipt"]["fact_hash_set_sha256"] == expected_hash


def test_live_verification_refuses_filesystem_root(tmp_path: Path) -> None:
    raw = artifact()
    fetch = FetchReceipt(
        adapter_id="test.adapter",
        request_url=HttpUrl("https://example.gov/data"),
        retrieved_at=NOW,
        status_code=200,
        content_type="application/json",
        response_sha256=raw.sha256,
        response_bytes=len(raw.content),
        record_count=0,
        source_version="v1",
        temporal_coverage=TemporalCoverage.LATEST_ONLY,
        historical_replay_eligible=False,
        warnings=("Latest-only fixture.",),
    )
    batch = AdapterBatch(records=(), receipts=(fetch,), artifacts=(raw,))
    append = AppendReceipt(0, 0, 0, (), ())
    manifest = TimeVaultManifest(0, 0, 0, 0, 0, "0" * 64, NOW)
    with pytest.raises(ValueError, match="filesystem root"):
        write_live_verification(
            output_directory=Path("/"),
            batch=batch,
            stored_artifacts=(),
            append_receipt=append,
            vault_manifest=manifest,
        )
