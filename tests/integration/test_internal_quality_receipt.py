from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

REPOSITORY = Path(__file__).resolve().parents[2]
RECEIPT = REPOSITORY / "verification/evidence/internal-quality-gates.json"


def test_internal_quality_receipt_is_self_hashed_and_subject_bound() -> None:
    payload = cast(dict[str, Any], json.loads(RECEIPT.read_text(encoding="utf-8")))
    claimed_hash = payload.pop("receipt_sha256")

    assert claimed_hash == _hash(payload)
    assert payload["all_required_gates_passed"] is True
    assert payload["tests"]["collected"] >= 100
    assert payload["tests"]["passed"] == payload["tests"]["collected"]
    assert payload["coverage"]["branch_measurement_enabled"] is True
    assert payload["coverage"]["combined_percent"] >= 90.0
    assert payload["dependency_audit"]["known_vulnerability_count"] == 0
    assert payload["secret_and_privacy_scan"]["clean"] is True
    assert all(payload["product_and_reproducibility"].values())

    revision = payload["subject"]["code_revision"]
    tree = subprocess.run(
        ["git", "rev-parse", f"{revision}^{{tree}}"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tree == payload["subject"]["tree_sha256"]
    for artifact in payload["subject_artifacts"]:
        content = subprocess.run(
            ["git", "show", f"{revision}:{artifact['path']}"],
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
        ).stdout
        assert len(content) == artifact["bytes"]
        assert hashlib.sha256(content).hexdigest() == artifact["sha256"]


def test_internal_quality_receipt_cli_verifier_passes() -> None:
    payload = cast(dict[str, Any], json.loads(RECEIPT.read_text(encoding="utf-8")))
    completed = subprocess.run(
        [sys.executable, "scripts/verify_internal_quality_receipt.py"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )

    revision = payload["subject"]["code_revision"]
    tests = payload["tests"]["passed"]
    coverage = payload["coverage"]["combined_percent"]
    assert f"verified=true revision={revision[:12]}" in completed.stdout
    assert f"tests={tests} coverage={coverage:.2f}%" in completed.stdout


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
