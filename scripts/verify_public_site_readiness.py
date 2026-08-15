#!/usr/bin/env python3
"""Verify the self-hash and committed-source binding of the site receipt."""

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
        default=Path("verification/evidence/public-site-readiness.json"),
    )
    args = parser.parse_args()
    payload = _load_json(args.receipt)
    claimed_hash = payload.pop("receipt_sha256", None)
    if claimed_hash != _hash(payload):
        raise SystemExit("public site receipt_sha256 mismatch")
    if payload.get("all_required_gates_passed") is not True:
        raise SystemExit("public site readiness gates did not all pass")
    if not all(payload["assertions"].values()):
        raise SystemExit("public site receipt contains a failed assertion")
    if not all(item["exit_code"] == 0 for item in payload["commands"].values()):
        raise SystemExit("public site receipt contains a failed command")
    if payload["dependency_audit"]["vulnerabilities"]["total"] != 0:
        raise SystemExit("public site receipt contains npm audit findings")
    if payload["rendered_tests"] != {"failed": 0, "passed": 2}:
        raise SystemExit("public site rendered test counts differ")
    content = payload["site_content"]
    if content["scenario_count"] != 30 or content["visible_breach_count"] != 19:
        raise SystemExit("public site scenario counts differ")
    state = payload["site_state"]
    if state != {
        "hosted": False,
        "independent_review_completed": False,
        "public_url": None,
    }:
        raise SystemExit("local readiness receipt overstates hosting or review status")

    subject = payload["subject"]
    revision = subject["code_revision"]
    if _git("rev-parse", f"{revision}^{{tree}}") != subject["tree_sha256"]:
        raise SystemExit("public site subject tree mismatch")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", revision, "HEAD"], cwd=REPOSITORY
    )
    if ancestor.returncode != 0:
        raise SystemExit("public site subject is not an ancestor of the current checkout")
    artifacts = payload["source_artifacts"]
    files = artifacts["files"]
    if artifacts["file_count"] != len(files) or artifacts["set_sha256"] != _hash(files):
        raise SystemExit("public site artifact inventory mismatch")
    observed_total = 0
    for artifact in files:
        blob = subprocess.run(
            ["git", "show", f"{revision}:{artifact['path']}"],
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
        ).stdout
        observed_total += len(blob)
        if len(blob) != artifact["bytes"] or hashlib.sha256(blob).hexdigest() != artifact[
            "sha256"
        ]:
            raise SystemExit(f"public site subject artifact mismatch: {artifact['path']}")
    if observed_total != artifacts["total_bytes"]:
        raise SystemExit("public site artifact byte total mismatch")
    print(
        f"verified=true revision={revision[:12]} files={len(files)} "
        f"rendered_tests=2 vulnerabilities=0 receipt_sha256={claimed_hash}"
    )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
