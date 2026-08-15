from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

REPOSITORY = Path(__file__).resolve().parents[2]
REGISTRY = REPOSITORY / "verification/claims/public-claims.json"


def test_public_claim_registry_rebuilds_byte_identically(tmp_path: Path) -> None:
    rebuilt = tmp_path / "public-claims.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_public_claim_registry.py",
            "--output",
            str(rebuilt),
        ],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "violations=0" in completed.stdout
    assert rebuilt.read_bytes() == REGISTRY.read_bytes()


def test_public_claim_registry_binds_every_evidence_locator() -> None:
    values = cast(dict[str, Any], json.loads(REGISTRY.read_text(encoding="utf-8")))
    claimed_hash = values.pop("registry_sha256")
    assert claimed_hash == _hash(values)
    assert values["boundary_scan"]["violations"] == []
    assert values["replaypack_surface"]["report_count"] == 31
    assert values["replaypack_surface"]["public_claim_count"] == 155
    assert values["replaypack_surface"]["evidence_classes"] == [
        "extracted",
        "inferred",
        "observed",
        "reported",
        "simulated",
    ]
    headline = {
        item["claim_id"]: item["observed_value"] for item in values["headline_claims"]
    }
    assert headline == {
        "seven-connected-engines": 7,
        "official-adapters": 30,
        "replay-proven-scenarios": 30,
        "sec-scale-partitions": 244,
        "sec-scale-physical-rows": 1_014_736_394,
    }
    for claim in values["headline_claims"]:
        evidence = REPOSITORY / claim["evidence_path"]
        assert hashlib.sha256(evidence.read_bytes()).hexdigest() == claim["evidence_sha256"]
    for report in values["replaypack_surface"]["reports"]:
        path = REPOSITORY / report["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == report["file_sha256"]


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
