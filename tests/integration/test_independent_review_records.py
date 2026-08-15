from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
SHA256 = "0" * 64


def test_empty_review_catalog_is_valid_but_does_not_claim_external_evidence() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/validate_independent_review_records.py"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "schema_valid_records=0" in completed.stdout
    assert "schema_validation_only=true" in completed.stdout
    assert "independent_review_completed=true" not in completed.stdout


def test_completed_review_record_validates_against_schema_and_git_history(
    tmp_path: Path,
) -> None:
    record = _record()
    path = tmp_path / "independent-review.json"
    path.write_text(json.dumps(record), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "scripts/validate_independent_review_records.py", str(path)],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "schema_valid_records=1" in completed.stdout
    assert f"review_ids={record['review_id']}" in completed.stdout


def test_record_rejects_maintainer_identity_and_non_descendant_resolution(
    tmp_path: Path,
) -> None:
    record = _record()
    record["reviewer"]["is_finreplay_maintainer"] = True
    record["resolution"]["revision"] = _git("rev-list", "--max-parents=0", "HEAD").splitlines()[0]
    path = tmp_path / "invalid-review.json"
    path.write_text(json.dumps(record), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "scripts/validate_independent_review_records.py", str(path)],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "/reviewer/is_finreplay_maintainer" in completed.stderr
    assert "must descend from or equal" in completed.stderr


def _record() -> dict[str, Any]:
    subject = _git("rev-parse", "HEAD^")
    resolution = _git("rev-parse", "HEAD")
    command = {
        "argv": [".venv/bin/python", "-m", "pytest", "-q", "tests/unit/test_timevault.py"],
        "exit_code": 0,
        "stdout_sha256": SHA256,
        "stderr_sha256": SHA256,
        "observation": "The bounded test completed with the recorded output hashes.",
    }
    evidence = {
        "locator": "reviewer-retained-output.txt",
        "sha256": SHA256,
        "description": "Reviewer-retained output supporting the reported issue.",
    }
    return {
        "schema_version": "1.0.0",
        "record_status": "complete",
        "review_id": "review-2026-0001",
        "reviewer": {
            "public_name": "Independent Reviewer",
            "qualification_basis": ["Relevant reproducibility and temporal-data experience."],
            "public_profile_url": "https://example.com/reviewer",
            "affiliation": None,
            "relationship_disclosure": "No financial, employment, or authorship relationship.",
            "is_finreplay_maintainer": False,
            "authored_subject_code": False,
        },
        "independence_attestation": {
            "personally_created_fresh_environment": True,
            "personally_executed_recorded_commands": True,
            "results_not_supplied_by_maintainer": True,
            "conflicts_disclosed": True,
        },
        "subject": {
            "revision": subject,
            "source_archive_name": "external-review-source.zip",
            "source_archive_sha256": SHA256,
            "target_id": "timevault-revision-boundary",
            "other_target_description": None,
        },
        "environment": {
            "started_at": "2026-08-15T00:00:00Z",
            "completed_at": "2026-08-15T00:20:00Z",
            "operating_system": "Linux",
            "architecture": "x86_64",
            "python_version": "3.12.5",
            "node_version": None,
            "dependency_lock_sha256": SHA256,
        },
        "reproduction": {
            "result": "not_reproduced",
            "commands": [command],
            "summary": "The reviewer found a bounded documentation-to-command mismatch.",
        },
        "issue": {
            "title": "Documented command used the wrong fixture",
            "severity": "P2",
            "description": (
                "The documented bounded command selected a fixture that did not exercise the "
                "stated point-in-time revision behavior."
            ),
            "evidence": [evidence],
            "disposition": "confirmed",
            "maintainer_response": "The mismatch was confirmed and corrected in the resolution.",
        },
        "resolution": {
            "revision": resolution,
            "description": "The documentation and fixture locator were corrected and tested.",
            "evidence": [evidence],
        },
        "recheck": {
            "performed_by_same_reviewer": True,
            "result": "passed",
            "issue_closed": True,
            "commands": [command],
            "summary": "The same reviewer reran the corrected bounded command successfully.",
        },
        "attestation": {
            "statement": (
                "I attest that I personally performed the recorded review and recheck, and that "
                "this record accurately preserves my findings and disclosed conflicts."
            ),
            "signed_by": "Independent Reviewer",
            "signed_at": "2026-08-15T00:30:00Z",
        },
        "claim_boundary": (
            "This record documents one bounded independent review. It is not general "
            "certification, source authentication, investment validation, adoption, deployment, "
            "or real-world impact evidence."
        ),
    }


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
