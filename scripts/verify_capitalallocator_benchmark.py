#!/usr/bin/env python3
"""Verify CapitalAllocator receipt hashes and semantic benchmark assertions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "receipt",
        nargs="?",
        type=Path,
        default=Path("verification/evidence/capitalallocator-benchmark.json"),
    )
    return parser.parse_args()


def main() -> None:
    path = parse_args().receipt
    payload = json.loads(path.read_text())
    receipt_hash = payload.pop("receipt_sha256", None)
    if receipt_hash != _hash(payload):
        raise SystemExit("receipt_sha256 mismatch")
    runtime = payload.pop("runtime", None)
    semantic_hash = payload.pop("semantic_sha256", None)
    if semantic_hash != _hash(payload):
        raise SystemExit("semantic_sha256 mismatch")
    if not isinstance(runtime, dict) or float(runtime.get("elapsed_seconds", -1)) < 0:
        raise SystemExit("invalid runtime evidence")
    assertions = payload.get("assertions")
    if not isinstance(assertions, dict) or not assertions:
        raise SystemExit("missing assertions")
    if payload.get("all_assertions_passed") is not True or not all(
        value is True for value in assertions.values()
    ):
        raise SystemExit("benchmark assertions did not all pass")
    tolerance = float(payload["tolerance"])
    errors = payload["golden"]["absolute_error"]
    if any(
        not math.isfinite(float(value)) or float(value) > tolerance
        for value in errors.values()
    ):
        raise SystemExit("golden numerical error exceeds tolerance")
    if payload["infeasible"]["status"] != "infeasible":
        raise SystemExit("infeasibility was not preserved")
    if payload["infeasible"]["weights"]:
        raise SystemExit("infeasible result contains candidate weights")
    if payload["reversal_surface"]["adjacent_reversal_count"] < 1:
        raise SystemExit("reversal surface has no decision reversal")
    evpi = float(
        payload["value_of_perfect_information"][
            "expected_value_of_perfect_information"
        ]
    )
    if abs(evpi - 0.1) > tolerance:
        raise SystemExit("EVPI does not match hand calculation")
    print(
        f"verified_assertions={len(assertions)} semantic_sha256={semantic_hash} "
        f"receipt_sha256={receipt_hash}"
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
