from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]


def test_no_key_no_network_demo_rebuilds_through_cli_and_api() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/verify_no_key_demo.py"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "credentials_present=0" in completed.stdout
    assert "network_attempts=0" in completed.stdout
    assert "cli=true api=true" in completed.stdout
