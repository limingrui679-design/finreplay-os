"""Verification of portable live receipts and their local content-addressed artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class VerifiedLiveReceipt:
    path: Path
    adapter_id: str
    retrieved_at: str
    record_count: int
    inserted_records: int
    idempotent_records: int
    temporal_coverage: str
    historical_replay_eligible: bool
    response_hashes: tuple[str, ...]


def verify_live_receipt(path: Path, *, raw_store: Path) -> VerifiedLiveReceipt:
    """Fail closed unless receipt self-hash, raw hashes, counts and temporal labels agree."""

    path = path.expanduser().resolve()
    raw_store = raw_store.expanduser().resolve()
    payload = _json_object(json.loads(path.read_text()), "live receipt")
    claimed_hash = payload.pop("receipt_sha256", None)
    if not isinstance(claimed_hash, str) or len(claimed_hash) != 64:
        raise ValueError(f"{path.name}: missing receipt_sha256")
    actual_hash = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    if claimed_hash != actual_hash:
        raise ValueError(f"{path.name}: self-hash mismatch")
    schema_version = payload.get("schema_version")
    if schema_version != "1.1.0":
        raise ValueError(f"{path.name}: unsupported receipt schema {schema_version!r}")
    adapter_id = payload.get("adapter_id")
    if not isinstance(adapter_id, str) or not adapter_id:
        raise ValueError(f"{path.name}: invalid adapter_id")
    fetches = payload.get("fetch_receipts")
    if not isinstance(fetches, list) or not fetches:
        raise ValueError(f"{path.name}: fetch_receipts must be a non-empty list")
    artifacts = payload.get("stored_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError(f"{path.name}: stored_artifacts must be a non-empty list")
    response_hashes: list[str] = []
    record_count = 0
    retrieved_times: list[str] = []
    coverages: set[str] = set()
    eligibility: set[bool] = set()
    for position, raw_fetch in enumerate(fetches):
        fetch = _json_object(raw_fetch, f"fetch_receipts[{position}]")
        if fetch.get("adapter_id") != adapter_id:
            raise ValueError(f"{path.name}: adapter id mismatch inside fetch receipt")
        response_hash = fetch.get("response_sha256")
        if not isinstance(response_hash, str) or len(response_hash) != 64:
            raise ValueError(f"{path.name}: invalid response_sha256")
        response_hashes.append(response_hash)
        count = fetch.get("record_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"{path.name}: invalid record_count")
        record_count += count
        retrieved_at = fetch.get("retrieved_at")
        if not isinstance(retrieved_at, str) or not retrieved_at:
            raise ValueError(f"{path.name}: invalid retrieved_at")
        retrieved_times.append(retrieved_at)
        coverage = fetch.get("temporal_coverage")
        eligible = fetch.get("historical_replay_eligible")
        if not isinstance(coverage, str) or not isinstance(eligible, bool):
            raise ValueError(f"{path.name}: invalid temporal eligibility")
        if coverage == "latest_only" and eligible:
            raise ValueError(f"{path.name}: latest-only source cannot be historically eligible")
        coverages.add(coverage)
        eligibility.add(eligible)
    if len(coverages) != 1 or len(eligibility) != 1:
        raise ValueError(f"{path.name}: mixed temporal labels in one adapter receipt")
    artifact_hashes: list[str] = []
    for position, raw_artifact in enumerate(artifacts):
        artifact = _json_object(raw_artifact, f"stored_artifacts[{position}]")
        digest = artifact.get("sha256")
        cache_key = artifact.get("cache_key")
        byte_count = artifact.get("bytes")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"{path.name}: invalid artifact sha256")
        expected_key = f"{digest[:2]}/{digest}.bin"
        if cache_key != expected_key:
            raise ValueError(f"{path.name}: non-canonical artifact cache key")
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            raise ValueError(f"{path.name}: invalid artifact byte count")
        raw_path = (raw_store / expected_key).resolve()
        if not raw_path.is_relative_to(raw_store):
            raise ValueError(f"{path.name}: artifact path escaped raw store")
        content = raw_path.read_bytes()
        if len(content) != byte_count or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"{path.name}: raw artifact size or hash mismatch")
        artifact_hashes.append(digest)
    if sorted(response_hashes) != sorted(artifact_hashes):
        raise ValueError(f"{path.name}: response and artifact hash multisets differ")
    append = _json_object(payload.get("append_receipt"), "append_receipt")
    attempted = append.get("attempted_records")
    inserted = append.get("inserted_records")
    idempotent = append.get("idempotent_records")
    hash_count = append.get("fact_hash_count")
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (attempted, inserted, idempotent, hash_count)
    ):
        raise ValueError(f"{path.name}: invalid append counts")
    assert isinstance(attempted, int)
    assert isinstance(inserted, int)
    assert isinstance(idempotent, int)
    assert isinstance(hash_count, int)
    if attempted != record_count or inserted + idempotent != attempted or hash_count != attempted:
        raise ValueError(f"{path.name}: append counts do not reconcile")
    fact_hash_set = append.get("fact_hash_set_sha256")
    if not isinstance(fact_hash_set, str) or len(fact_hash_set) != 64:
        raise ValueError(f"{path.name}: invalid fact_hash_set_sha256")
    return VerifiedLiveReceipt(
        path=path,
        adapter_id=adapter_id,
        retrieved_at=max(retrieved_times),
        record_count=record_count,
        inserted_records=inserted,
        idempotent_records=idempotent,
        temporal_coverage=next(iter(coverages)),
        historical_replay_eligible=next(iter(eligibility)),
        response_hashes=tuple(response_hashes),
    )


def latest_live_receipts(
    directory: Path,
    *,
    raw_store: Path,
) -> tuple[VerifiedLiveReceipt, ...]:
    """Verify all current receipts and select exactly the newest one per adapter."""

    candidates: list[Path] = []
    for path in sorted(directory.expanduser().resolve().glob("**/*.json")):
        # Receipt schema 1.0 expanded every fact hash and is retained only as immutable legacy
        # evidence in Git. Current summaries include schema 1.1 exclusively; every 1.1 candidate
        # is then fully verified below, so a malformed current receipt cannot be hidden.
        try:
            payload = _json_object(json.loads(path.read_text()), "live receipt")
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"{path.name}: receipt is not valid JSON") from error
        if payload.get("schema_version") == "1.1.0":
            candidates.append(path)
    verified = [verify_live_receipt(path, raw_store=raw_store) for path in candidates]
    latest: dict[str, VerifiedLiveReceipt] = {}
    for receipt in verified:
        prior = latest.get(receipt.adapter_id)
        if prior is None or (receipt.retrieved_at, receipt.path.name) > (
            prior.retrieved_at,
            prior.path.name,
        ):
            latest[receipt.adapter_id] = receipt
    return tuple(latest[key] for key in sorted(latest))


def _json_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
