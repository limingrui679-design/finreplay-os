#!/usr/bin/env python3
"""Rebuild and inspect the static demo with credentials removed and sockets forbidden."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from build_replaystudio_golden import build_spec
from finreplay.api import build_replaypack, load_verified_replaypack, verify_replaypack
from finreplay.cli import app

REPOSITORY = Path(__file__).resolve().parents[1]
GOLDEN = REPOSITORY / "verification/replaypacks/replaystudio-golden"
_CREDENTIAL_NAME = re.compile(
    r"(?:^|_)(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?)(?:$|_)",
    re.IGNORECASE,
)


class NetworkAccessAttemptedError(RuntimeError):
    """Raised if the no-network demo attempts to create a socket."""


def main() -> None:
    if "--isolated-child" not in sys.argv:
        _run_isolated_child()
        return
    credential_names = _credential_variable_names(os.environ)
    if credential_names:
        raise SystemExit("isolated demo environment still contains credential variables")
    with patch.object(socket, "socket", side_effect=NetworkAccessAttemptedError):
        _verify_demo()


def _run_isolated_child() -> None:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name != "FINREPLAY_SEC_USER_AGENT" and _CREDENTIAL_NAME.search(name) is None
    }
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--isolated-child"],
        cwd=REPOSITORY,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    print(completed.stdout.strip())


def _verify_demo() -> None:
    committed_receipt = verify_replaypack(GOLDEN)
    committed_report = load_verified_replaypack(GOLDEN)
    with tempfile.TemporaryDirectory(prefix="finreplay-no-key-demo-") as temporary:
        root = Path(temporary)
        rebuilt = build_replaypack(
            build_spec(),
            root / "pack",
            archive=root / "pack.zip",
        )
        rebuilt_receipt = verify_replaypack(rebuilt.root)
        if _file_map(GOLDEN) != _file_map(rebuilt.root):
            raise SystemExit("isolated no-key rebuild differs from committed golden pack")
        runner = CliRunner()
        cli_result = runner.invoke(app, ["verify-replaypack", str(rebuilt.root)])
        if cli_result.exit_code != 0 or "verified=true" not in cli_result.stdout:
            raise SystemExit("CLI failed to verify the isolated no-key rebuild")
    if rebuilt_receipt.pack_sha256 != committed_receipt.pack_sha256:
        raise SystemExit("isolated no-key rebuild identifies a different pack")
    print(
        "verified=true credentials_present=0 network_attempts=0 cli=true api=true "
        f"engines={len(committed_report.engine_artifact_counts)} "
        f"claims={len(committed_report.spec.claims)} "
        f"pack_sha256={committed_receipt.pack_sha256}"
    )


def _credential_variable_names(environment: dict[str, str] | os._Environ[str]) -> list[str]:
    return sorted(
        name
        for name in environment
        if name == "FINREPLAY_SEC_USER_AGENT" or _CREDENTIAL_NAME.search(name) is not None
    )


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


if __name__ == "__main__":
    main()
