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
    assert values["boundary_scan"]["scanned_text_file_count"] == 170
    assert values["replaypack_surface"]["report_count"] == 31
    assert values["replaypack_surface"]["public_claim_count"] == 155
    assert values["replaypack_surface"]["evidence_classes"] == [
        "extracted",
        "inferred",
        "observed",
        "reported",
        "simulated",
    ]
    headline = {item["claim_id"]: item["observed_value"] for item in values["headline_claims"]}
    assert headline == {
        "seven-connected-engines": 7,
        "official-adapters": 30,
        "replay-proven-scenarios": 30,
        "sec-scale-partitions": 244,
        "sec-scale-physical-rows": 1_014_736_394,
    }
    deployment = values["public_site_deployment"]
    assert deployment == {
        "access_mode": "public",
        "anonymous_http_status": 200,
        "deployment_status": "succeeded",
        "evidence_path": "verification/evidence/public-site-deployment.json",
        "evidence_sha256": hashlib.sha256(
            (REPOSITORY / "verification/evidence/public-site-deployment.json").read_bytes()
        ).hexdigest(),
        "independent_review_completed": False,
        "public_url": "https://finreplay-evidence.limingrui2.chatgpt.site",
        "receipt_sha256": "a2ff9b38201b79d0e0e09ddee88ce08d67f6a4c37a6d424caea2f16b6f9d1583",
        "sites_version_number": 1,
    }
    github_release = values["public_github_release"]
    assert github_release == {
        "default_branch": "main",
        "evidence_path": "verification/evidence/public-github-release.json",
        "evidence_sha256": hashlib.sha256(
            (REPOSITORY / "verification/evidence/public-github-release.json").read_bytes()
        ).hexdigest(),
        "independent_review_completed": False,
        "published_head_commit": "6a2b6fe535fab635b919d6f3e481905d3d82a6b4",
        "raw_readme_http_status": 200,
        "receipt_sha256": "c80cf98d37abe769fd4be3340524235ae9c207537ed1b39364e6e1021b4a49b7",
        "repository_url": "https://github.com/limingrui679-design/finreplay-os",
        "requested_source_commit": "51a52337ce4ed485333fba1c21c8132692b9801e",
        "source_archive_http_status": 200,
        "visibility": "public",
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
