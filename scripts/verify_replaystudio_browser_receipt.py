#!/usr/bin/env python3
"""Verify the operator-recorded ReplayStudio browser receipt and referenced pack."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from finreplay.engines import ReplayStudio


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "receipt",
        nargs="?",
        type=Path,
        default=Path("verification/evidence/replaystudio-browser-check.json"),
    )
    return parser.parse_args()


def main() -> None:
    path = parse_args().receipt
    payload = json.loads(path.read_text())
    receipt_hash = payload.pop("receipt_sha256", None)
    if receipt_hash != _hash(payload):
        raise SystemExit("browser receipt_sha256 mismatch")
    assertions = payload.get("assertions")
    if not isinstance(assertions, dict) or not assertions:
        raise SystemExit("missing browser assertions")
    if payload.get("all_assertions_passed") is not True or not all(
        value is True for value in assertions.values()
    ):
        raise SystemExit("browser assertions did not all pass")
    mobile = payload["mobile"]
    if mobile["document_client_width"] != mobile["document_scroll_width"]:
        raise SystemExit("mobile document still has horizontal overflow")
    if mobile["table_container_scroll_width"] <= mobile["table_container_client_width"]:
        raise SystemExit("browser receipt does not exercise local table overflow")
    golden = payload["golden_pack"]
    pack_root = Path(golden["relative_path"])
    pack_receipt = ReplayStudio().verify(pack_root)
    if (
        golden["pack_sha256"] != pack_receipt.pack_sha256
        or golden["receipt_sha256"] != pack_receipt.receipt_sha256
    ):
        raise SystemExit("browser receipt references a different golden pack")
    print(
        f"verified=true assertions={len(assertions)} "
        f"pack_sha256={pack_receipt.pack_sha256} receipt_sha256={receipt_hash}"
    )


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


if __name__ == "__main__":
    main()
