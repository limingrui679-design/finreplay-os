"""Build and verify the bundled SVB evidence scenario without network access."""

from __future__ import annotations

import argparse
from pathlib import Path

from finreplay.catalog import find_scenario, run_scenario


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("out/svb-2023"))
    args = parser.parse_args()

    entry = find_scenario("svb-2023")
    archive = args.output.parent / f"{args.output.name}.zip"
    result = run_scenario(entry.slug, args.output, archive=archive)
    if result.receipt.pack_sha256 != entry.pack_sha256:
        raise SystemExit("reproduced pack hash differs from the recorded scenario evidence")
    print(f"verified=true offline=true report={result.root / 'index.html'}")
    print(f"archive={archive.resolve()}")


if __name__ == "__main__":
    main()
