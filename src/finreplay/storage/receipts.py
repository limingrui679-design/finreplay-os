"""Canonical live-verification receipts linking source bytes to measured storage state."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from finreplay.adapters import AdapterBatch
from finreplay.engines import AppendReceipt, TimeVaultManifest
from finreplay.storage.artifacts import StoredArtifact


def write_live_verification(
    *,
    output_directory: Path,
    batch: AdapterBatch,
    stored_artifacts: tuple[StoredArtifact, ...],
    append_receipt: AppendReceipt,
    vault_manifest: TimeVaultManifest,
) -> Path:
    """Atomically persist a machine-readable receipt without copying raw source data."""

    output_directory = output_directory.expanduser().resolve()
    if output_directory == Path(output_directory.anchor):
        raise ValueError("verification output must not be a filesystem root")
    output_directory.mkdir(parents=True, exist_ok=True)
    relative_artifacts = [
        {
            "sha256": item.sha256,
            "bytes": item.bytes,
            "created": item.created,
            "cache_key": f"{item.sha256[:2]}/{item.sha256}.bin",
        }
        for item in stored_artifacts
    ]
    append_payload = _compact_append_receipt(append_receipt)
    payload: dict[str, Any] = {
        "schema_version": "1.1.0",
        "claim_boundary": (
            "This receipt proves a live official-source response was retrieved, parsed, hashed, "
            "and appended locally. It does not prove historical-vintage eligibility, source "
            "authenticity beyond HTTPS/publication context, investment validity, or external use."
        ),
        "adapter_id": batch.receipts[0].adapter_id,
        "fetch_receipts": [
            receipt.model_dump(mode="json") for receipt in batch.receipts
        ],
        "stored_artifacts": relative_artifacts,
        "append_receipt": append_payload,
        "timevault_manifest": _jsonable(asdict(vault_manifest)),
    }
    canonical = _canonical_json(payload).encode()
    payload["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    final = (_canonical_json(payload) + "\n").encode()
    name = f"{batch.receipts[0].adapter_id}-{payload['receipt_sha256'][:16]}.json"
    destination = output_directory / name
    _atomic_write(destination, final)
    return destination


def _compact_append_receipt(receipt: AppendReceipt) -> dict[str, Any]:
    """Commit to every fact hash without making large live receipts scale linearly."""

    ordered_hashes = sorted(receipt.fact_hashes)
    fact_hash_set_sha256 = hashlib.sha256("\n".join(ordered_hashes).encode()).hexdigest()
    return {
        "attempted_records": receipt.attempted_records,
        "inserted_records": receipt.inserted_records,
        "idempotent_records": receipt.idempotent_records,
        "artifact_ids": list(receipt.artifact_ids),
        "fact_hash_count": len(receipt.fact_hashes),
        "fact_hash_set_sha256": fact_hash_set_sha256,
    }


def _atomic_write(destination: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
