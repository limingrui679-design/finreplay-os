#!/usr/bin/env python3
"""Verify the self-hash and subject-commit bindings of the internal quality receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "receipt",
        nargs="?",
        type=Path,
        default=Path("verification/evidence/internal-quality-gates.json"),
    )
    args = parser.parse_args()
    payload = _load_json(args.receipt)
    claimed_hash = payload.pop("receipt_sha256", None)
    if claimed_hash != _hash(payload):
        raise SystemExit("internal quality receipt_sha256 mismatch")
    if payload.get("all_required_gates_passed") is not True:
        raise SystemExit("internal quality receipt does not pass every required gate")
    subject = payload["subject"]
    revision = subject["code_revision"]
    tree = _git("rev-parse", f"{revision}^{{tree}}")
    if tree != subject["tree_sha256"]:
        raise SystemExit("quality receipt subject tree mismatch")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", revision, "HEAD"],
        cwd=REPOSITORY,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise SystemExit("quality receipt subject is not an ancestor of the current checkout")
    for artifact in payload["subject_artifacts"]:
        content = subprocess.run(
            ["git", "show", f"{revision}:{artifact['path']}"],
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
        ).stdout
        if len(content) != artifact["bytes"] or hashlib.sha256(content).hexdigest() != artifact[
            "sha256"
        ]:
            raise SystemExit(f"quality subject artifact mismatch: {artifact['path']}")
    tests = payload["tests"]
    if tests["collected"] < 100 or tests["passed"] != tests["collected"]:
        raise SystemExit("quality receipt test threshold or pass count failed")
    coverage = payload["coverage"]
    if (
        coverage["branch_measurement_enabled"] is not True
        or coverage["combined_percent"] < coverage["required_combined_percent"]
    ):
        raise SystemExit("quality receipt branch-aware coverage gate failed")
    if payload["dependency_audit"]["known_vulnerability_count"] != 0:
        raise SystemExit("quality receipt dependency audit contains vulnerabilities")
    secret_scan = dict(payload["secret_and_privacy_scan"])
    scan_hash = secret_scan.pop("scan_sha256", None)
    if scan_hash != _hash(secret_scan) or secret_scan.get("clean") is not True:
        raise SystemExit("quality receipt secret scan is invalid or not clean")
    if not all(result["exit_code"] == 0 for result in payload["commands"].values()):
        raise SystemExit("quality receipt contains a failed command")
    if not all(payload["product_and_reproducibility"].values()):
        raise SystemExit("quality receipt product/reproducibility gate failed")
    print(
        f"verified=true revision={revision[:12]} tests={tests['passed']} "
        f"coverage={coverage['combined_percent']:.2f}% receipt_sha256={claimed_hash}"
    )


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


if __name__ == "__main__":
    main()
