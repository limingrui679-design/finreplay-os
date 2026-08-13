#!/usr/bin/env python3
"""Verify every counted scenario and emit the deterministic current inventory."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from finreplay.scenarios import scenario_catalog_summary, verify_scenario_catalog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--proof-directory",
        type=Path,
        default=Path("verification/scenarios/proofs"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/scenarios/latest-summary.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    verified = verify_scenario_catalog(
        args.proof_directory,
        repository_root=args.repository_root,
    )
    summary = scenario_catalog_summary(verified, proof_directory=args.proof_directory)
    serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _atomic_write(args.output, serialized.encode())
    print(serialized, end="")


def _atomic_write(destination: Path, content: bytes) -> None:
    destination = destination.expanduser().resolve()
    if destination == Path(destination.anchor):
        raise ValueError("scenario summary output must not be a filesystem root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    main()
