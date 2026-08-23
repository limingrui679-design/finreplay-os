#!/usr/bin/env python3
"""Select or validate a Python interpreter supported by FinReplay."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from typing import Optional

SUPPORTED_VERSIONS = {(3, 11), (3, 12)}
CANDIDATES = ("python3.12", "python3.11", "python3")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assert-current", action="store_true")
    args = parser.parse_args()

    if args.assert_current:
        version = (sys.version_info.major, sys.version_info.minor)
        if not _supported_version(*version):
            raise SystemExit(
                "FinReplay supports Python 3.11 and 3.12; "
                f"selected interpreter is {sys.version.split()[0]}"
            )
        print(f"python_supported=true version={version[0]}.{version[1]}")
        return

    selected = _select_interpreter()
    if selected is None:
        raise SystemExit(
            "FinReplay bootstrap could not find Python 3.11 or 3.12 on PATH. "
            "Install a supported interpreter or set PYTHON_BOOTSTRAP=/absolute/path/to/python."
        )
    print(selected)


def _supported_version(major: int, minor: int) -> bool:
    return (major, minor) in SUPPORTED_VERSIONS


def _select_interpreter() -> Optional[str]:  # noqa: UP045 - must parse on Python 3.9
    visited: set[str] = set()
    for candidate in CANDIDATES:
        path = shutil.which(candidate)
        if path is None or path in visited:
            continue
        visited.add(path)
        if _interpreter_supported(path):
            return path
    return None


def _interpreter_supported(path: str) -> bool:
    completed = subprocess.run(
        [
            path,
            "-c",
            (
                "import sys; "
                "raise SystemExit(0 if (sys.version_info.major, sys.version_info.minor) "
                "in {(3, 11), (3, 12)} else 1)"
            ),
        ],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


if __name__ == "__main__":
    main()
