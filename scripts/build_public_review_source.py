#!/usr/bin/env python3
"""Build a deterministic review-source ZIP from one committed revision."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", default="HEAD")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    revision = _git("rev-parse", f"{args.subject}^{{commit}}")
    short_revision = revision[:12]
    completed = subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            f"--prefix=finreplay-os-{short_revision}/",
            revision,
        ],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
    )
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(completed.stdout)
    digest = hashlib.sha256(completed.stdout).hexdigest()
    print(
        f"revision={revision} bytes={len(completed.stdout)} sha256={digest} "
        f"output={output}"
    )


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    main()
