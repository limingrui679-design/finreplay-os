#!/usr/bin/env python3
"""Recompute the ExecutionLab golden evidence receipt and numerical error gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "receipt",
        nargs="?",
        type=Path,
        default=Path("verification/evidence/executionlab-golden.json"),
    )
    return parser.parse_args()


def main() -> None:
    path = parse_args().receipt
    payload = json.loads(path.read_text())
    claimed_hash = payload.pop("receipt_sha256", None)
    actual_hash = _sha256(payload)
    if claimed_hash != actual_hash:
        raise SystemExit("receipt_sha256 mismatch")
    tolerance = float(payload["tolerance"])
    if not math.isfinite(tolerance) or tolerance < 0:
        raise SystemExit("invalid tolerance")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SystemExit("receipt has no cases")
    for case in cases:
        expected = case["expected"]
        actual = case["actual"]
        recomputed = {
            key: abs(float(actual[key]) - float(value))
            for key, value in expected.items()
        }
        if recomputed != case["absolute_error"]:
            raise SystemExit(f"absolute error mismatch: {case['case_id']}")
        if not all(math.isfinite(value) and value <= tolerance for value in recomputed.values()):
            raise SystemExit(f"golden mismatch: {case['case_id']}")
        if case.get("within_tolerance") is not True:
            raise SystemExit(f"case does not claim pass: {case['case_id']}")
    if payload.get("all_within_tolerance") is not True:
        raise SystemExit("receipt does not claim aggregate pass")
    print(f"verified_cases={len(cases)} receipt_sha256={claimed_hash}")


def _sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


if __name__ == "__main__":
    main()
