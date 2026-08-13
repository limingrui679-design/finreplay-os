#!/usr/bin/env python3
"""Fresh-build and byte-compare the committed ReplayStudio golden pack."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from build_replaystudio_golden import build_spec
from finreplay.engines import EngineName, ReplayStudio


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pack",
        nargs="?",
        type=Path,
        default=Path("verification/replaypacks/replaystudio-golden"),
    )
    return parser.parse_args()


def main() -> None:
    committed = parse_args().pack.resolve()
    studio = ReplayStudio()
    receipt = studio.verify(committed)
    with tempfile.TemporaryDirectory(prefix="finreplay-replaystudio-") as temporary:
        temporary_root = Path(temporary)
        rebuilt = studio.build(build_spec(), temporary_root / "rebuilt").root
        first_zip = studio.archive(committed, temporary_root / "committed.zip")
        second_zip = studio.archive(rebuilt, temporary_root / "rebuilt.zip")
        if _file_map(committed) != _file_map(rebuilt):
            raise SystemExit("fresh ReplayStudio build differs from committed golden pack")
        if first_zip.read_bytes() != second_zip.read_bytes():
            raise SystemExit("deterministic ReplayStudio archives differ")
    report = studio.compile(build_spec())
    if set(report.engine_artifact_counts) != set(EngineName):
        raise SystemExit("golden pack does not represent every engine")
    if len(report.spec.claims) != 5:
        raise SystemExit("golden pack does not represent all five evidence labels")
    print(
        f"verified=true engines={len(report.engine_artifact_counts)} "
        f"claims={len(report.spec.claims)} trace_id={receipt.trace_id} "
        f"pack_sha256={receipt.pack_sha256} receipt_sha256={receipt.receipt_sha256}"
    )


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


if __name__ == "__main__":
    main()
