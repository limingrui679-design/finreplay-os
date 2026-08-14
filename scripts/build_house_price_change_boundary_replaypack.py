#!/usr/bin/env python3
"""Build the deterministic April 2020 FHFA HPI ReplayPack."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from finreplay.engines import ReplayStudio
from finreplay.scenarios import (
    build_house_price_change_boundary_replay_spec,
    load_house_price_change_boundary_input_lock,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--code-commit",
        help="Exact 40-character commit or 'uncommitted'; defaults to current clean checkout.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    code_commit = args.code_commit or _current_code_commit()
    lock = load_house_price_change_boundary_input_lock(args.input_lock)
    spec = build_house_price_change_boundary_replay_spec(lock, code_commit=code_commit)
    studio = ReplayStudio()
    result = studio.build(spec, args.output)
    archive = studio.archive(result.root, args.archive) if args.archive else None
    print(
        f"verified=true engines={len(spec.artifacts)} input_records={spec.distinct_input_records} "
        f"derived_records={spec.derived_records} idempotent={str(result.idempotent).lower()} "
        f"trace_id={result.receipt.trace_id} receipt_sha256={result.receipt.receipt_sha256} "
        f"root={result.root}"
    )
    if archive:
        print(f"archive={archive}")


def _current_code_commit() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        return "uncommitted"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if len(commit) != 40:
        raise SystemExit("git rev-parse did not return a full commit hash")
    return commit


if __name__ == "__main__":
    main()
