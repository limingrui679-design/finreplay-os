from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

REPOSITORY = Path(__file__).resolve().parents[2]
RECEIPT = REPOSITORY / "verification/evidence/public-site-readiness.json"
READINESS_SCRIPT = REPOSITORY / "scripts/run_public_site_readiness.py"


def test_public_site_readiness_receipt_is_self_hashed_and_subject_bound() -> None:
    payload = cast(dict[str, Any], json.loads(RECEIPT.read_text(encoding="utf-8")))
    claimed_hash = payload.pop("receipt_sha256")

    assert claimed_hash == _hash(payload)
    assert payload["all_required_gates_passed"] is True
    assert all(payload["assertions"].values())
    assert payload["rendered_tests"]["failed"] == 0
    minimum_tests = 10 if payload["schema_version"] == "1.1.0" else 3
    assert payload["rendered_tests"]["passed"] >= minimum_tests
    assert payload["dependency_audit"]["vulnerabilities"]["total"] == 0
    assert payload["site_state"] == {
        "hosted": False,
        "independent_review_completed": False,
        "public_url": None,
    }

    content = payload["site_content"]
    assert content["scenario_count"] == 30
    assert content["visible_breach_count"] == 19
    if payload["schema_version"] == "1.1.0":
        assert content["analytical_dimension_count"] == 10
        assert content["pathway_count"] == 5
        assert content["capability_count"] == 10

    revision = payload["subject"]["code_revision"]
    tree = subprocess.run(
        ["git", "rev-parse", f"{revision}^{{tree}}"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tree == payload["subject"]["tree_sha256"]
    for artifact in payload["source_artifacts"]["files"]:
        content_bytes = subprocess.run(
            ["git", "show", f"{revision}:{artifact['path']}"],
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
        ).stdout
        assert len(content_bytes) == artifact["bytes"]
        assert hashlib.sha256(content_bytes).hexdigest() == artifact["sha256"]


def test_public_site_readiness_cli_verifier_passes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/verify_public_site_readiness.py"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "verified=true" in completed.stdout
    assert "vulnerabilities=0" in completed.stdout


def test_fresh_site_archive_contains_cross_directory_catalog_inputs(
    tmp_path: Path,
) -> None:
    script = runpy.run_path(
        str(READINESS_SCRIPT), run_name="finreplay_site_readiness_test"
    )
    validation_paths = cast(tuple[str, ...], script["SITE_VALIDATION_PATHS"])
    extract_site_archive = cast(
        Callable[[str, Path], None], script["_extract_site_archive"]
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD^{commit}"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    extract_site_archive(revision, tmp_path)

    for relative in validation_paths:
        assert (tmp_path / "source" / relative).is_file()


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
