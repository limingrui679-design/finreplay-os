from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from finreplay.security import scan_repository, scan_text

REPOSITORY = Path(__file__).resolve().parents[2]


def test_secret_scanner_finds_supported_formats_without_retaining_values() -> None:
    fake_aws = "AK" + "IA" + "A" * 16
    fake_github = "gh" + "p_" + "b" * 24
    fake_private_key = "-----BEGIN " + "PRIVATE KEY-----"
    fake_local_path = "/Us" + "ers/example/private.txt"
    text = f"{fake_aws}\n{fake_github}\n{fake_private_key}\n{fake_local_path}\n"

    findings = scan_text(text, "fixture.txt")

    assert {item.rule_id for item in findings} == {
        "aws_access_key",
        "github_token",
        "local_user_absolute_path",
        "private_key_pem",
    }
    assert all(item.path == "fixture.txt" for item in findings)
    assert all(len(item.matched_value_sha256) == 64 for item in findings)
    assert all(fake_aws not in repr(item) for item in findings)


def test_current_tracked_repository_scan_is_clean() -> None:
    payload = scan_repository(REPOSITORY)
    tracked_file_count = payload["tracked_file_count"]

    assert payload["clean"] is True
    assert payload["findings"] == []
    assert isinstance(tracked_file_count, int)
    assert tracked_file_count >= 1_400
    assert len(str(payload["scan_sha256"])) == 64

    completed = subprocess.run(
        [sys.executable, "scripts/scan_tracked_secrets.py"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "clean=true" in completed.stdout
    assert "findings=0" in completed.stdout
