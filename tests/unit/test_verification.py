from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import HttpUrl

from finreplay.adapters import AdapterBatch, FetchReceipt, RawArtifact
from finreplay.contracts import TemporalCoverage
from finreplay.engines import AppendReceipt, TimeVaultManifest
from finreplay.storage import ContentAddressedStore, write_live_verification
from finreplay.verification import latest_live_receipts, verify_live_receipt

NOW = datetime(2026, 8, 12, 15, tzinfo=UTC)


def write_receipt(
    root: Path,
    *,
    adapter_id: str = "test.official.adapter",
    retrieved_at: datetime = NOW,
    coverage: TemporalCoverage = TemporalCoverage.LATEST_ONLY,
    eligible: bool = False,
) -> tuple[Path, Path]:
    content = f"official-{retrieved_at.isoformat()}".encode()
    digest = hashlib.sha256(content).hexdigest()
    raw = RawArtifact(sha256=digest, content_type="application/json", content=content)
    raw_store = root / "raw"
    stored = ContentAddressedStore(raw_store).put(raw)
    fetch = FetchReceipt(
        adapter_id=adapter_id,
        request_url=HttpUrl("https://example.gov/data"),
        retrieved_at=retrieved_at,
        status_code=200,
        content_type="application/json",
        response_sha256=digest,
        response_bytes=len(content),
        record_count=1,
        source_version="v1",
        temporal_coverage=coverage,
        historical_replay_eligible=eligible,
        warnings=("Explicit fixture boundary.",),
    )
    batch = AdapterBatch(
        records=(),
        receipts=(fetch.model_copy(update={"record_count": 0}),),
        artifacts=(raw,),
    )
    append = AppendReceipt(0, 0, 0, (), ())
    manifest = TimeVaultManifest(0, 0, 0, 0, 0, hashlib.sha256(b"").hexdigest(), NOW)
    path = write_live_verification(
        output_directory=root / "receipts",
        batch=batch,
        stored_artifacts=(stored,),
        append_receipt=append,
        vault_manifest=manifest,
    )
    return path, raw_store


def rewrite_self_hash(path: Path, mutator: Any) -> None:
    payload = json.loads(path.read_text())
    payload.pop("receipt_sha256")
    mutator(payload)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    payload["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    )


def test_verifier_checks_self_hash_raw_hash_counts_and_temporal_boundary(tmp_path: Path) -> None:
    path, raw_store = write_receipt(tmp_path)
    verified = verify_live_receipt(path, raw_store=raw_store)
    assert verified.adapter_id == "test.official.adapter"
    assert verified.temporal_coverage == "latest_only"
    assert verified.historical_replay_eligible is False

    path.write_text(path.read_text().replace("Explicit", "Changed"))
    with pytest.raises(ValueError, match="self-hash mismatch"):
        verify_live_receipt(path, raw_store=raw_store)


def test_verifier_fails_on_raw_tamper_and_reconciled_receipt_tamper(tmp_path: Path) -> None:
    path, raw_store = write_receipt(tmp_path)
    payload = json.loads(path.read_text())
    raw_path = raw_store / payload["stored_artifacts"][0]["cache_key"]
    raw_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="size or hash mismatch"):
        verify_live_receipt(path, raw_store=raw_store)

    path, raw_store = write_receipt(tmp_path / "second")
    rewrite_self_hash(path, lambda value: value["append_receipt"].update(attempted_records=1))
    with pytest.raises(ValueError, match="counts do not reconcile"):
        verify_live_receipt(path, raw_store=raw_store)


def test_verifier_rejects_latest_only_historical_claim_even_with_valid_self_hash(
    tmp_path: Path,
) -> None:
    path, raw_store = write_receipt(tmp_path)
    rewrite_self_hash(
        path,
        lambda value: value["fetch_receipts"][0].update(
            historical_replay_eligible=True
        ),
    )
    with pytest.raises(ValueError, match="cannot be historically eligible"):
        verify_live_receipt(path, raw_store=raw_store)


def test_latest_receipts_selects_one_verified_receipt_per_adapter(tmp_path: Path) -> None:
    first, raw_store = write_receipt(tmp_path, retrieved_at=NOW)
    second, _ = write_receipt(tmp_path, retrieved_at=NOW + timedelta(hours=1))
    other, _ = write_receipt(
        tmp_path,
        adapter_id="test.second.adapter",
        retrieved_at=NOW + timedelta(minutes=30),
    )
    assert len({first, second, other}) == 3
    latest = latest_live_receipts(tmp_path / "receipts", raw_store=raw_store)
    assert [item.adapter_id for item in latest] == [
        "test.official.adapter",
        "test.second.adapter",
    ]
    assert latest[0].retrieved_at == (NOW + timedelta(hours=1)).isoformat().replace(
        "+00:00", "Z"
    )


def test_latest_receipts_ignores_legacy_schema_but_not_invalid_current_json(
    tmp_path: Path,
) -> None:
    current, raw_store = write_receipt(tmp_path)
    legacy = tmp_path / "receipts" / "legacy.json"
    legacy.write_text('{"schema_version":"1.0.0"}\n')
    assert latest_live_receipts(tmp_path / "receipts", raw_store=raw_store)[0].path == current

    invalid = tmp_path / "receipts" / "invalid.json"
    invalid.write_text("not-json")
    with pytest.raises(ValueError, match="not valid JSON"):
        latest_live_receipts(tmp_path / "receipts", raw_store=raw_store)
