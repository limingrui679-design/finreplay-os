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
    assert values["boundary_scan"]["scanned_text_file_count"] >= 170
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
    deployment_receipt = cast(
        dict[str, Any],
        json.loads(
            (REPOSITORY / "verification/evidence/public-site-deployment.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert deployment == {
        "access_mode": deployment_receipt["site"]["access_mode"],
        "anonymous_http_status": deployment_receipt["verification"][
            "anonymous_http_status"
        ],
        "deployment_status": deployment_receipt["site"]["deployment_status"],
        "evidence_path": "verification/evidence/public-site-deployment.json",
        "evidence_sha256": hashlib.sha256(
            (REPOSITORY / "verification/evidence/public-site-deployment.json").read_bytes()
        ).hexdigest(),
        "independent_review_completed": False,
        "public_url": deployment_receipt["site"]["public_url"],
        "receipt_sha256": deployment_receipt["receipt_sha256"],
        "sites_version_number": deployment_receipt["site"]["version_number"],
    }
    github_release = values["public_github_release"]
    github_receipt = cast(
        dict[str, Any],
        json.loads(
            (REPOSITORY / "verification/evidence/public-github-release.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert github_release == {
        "default_branch": github_receipt["repository"]["default_branch"],
        "evidence_path": "verification/evidence/public-github-release.json",
        "evidence_sha256": hashlib.sha256(
            (REPOSITORY / "verification/evidence/public-github-release.json").read_bytes()
        ).hexdigest(),
        "independent_review_completed": False,
        "published_head_commit": github_receipt["release_binding"][
            "published_head_commit"
        ],
        "raw_readme_http_status": github_receipt["verification"]["raw_readme"][
            "anonymous_http_status"
        ],
        "receipt_sha256": github_receipt["receipt_sha256"],
        "repository_url": github_receipt["repository"]["html_url"],
        "requested_source_commit": github_receipt["release_binding"][
            "requested_source_commit"
        ],
        "source_archive_http_status": github_receipt["verification"]["source_archive"][
            "anonymous_http_status"
        ],
        "visibility": github_receipt["repository"]["visibility"],
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
