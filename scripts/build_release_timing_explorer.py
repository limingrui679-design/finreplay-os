"""Embed the release-timing frontier in the single-file, offline cost explorer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finreplay.engines.release_timing import read_paths
from finreplay.engines.release_timing_frontier import evaluate_cost_frontier

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/release_timing/quarterly_output.csv"
PAGE = ROOT / "web/public/release-timing.html"
DATA_TAG = '<script id="timing-data" type="application/json">'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()

    with SOURCE.open(newline="", encoding="utf-8") as source:
        paths, _ = read_paths(source)
    report = evaluate_cost_frontier(paths)
    payload = json.dumps(report, separators=(",", ":"), allow_nan=False).replace("<", r"\u003c")
    html = PAGE.read_text(encoding="utf-8")
    if html.count(DATA_TAG) != 1:
        raise ValueError("Expected exactly one release-timing data block")
    before, _, section = html.partition(DATA_TAG)
    _, end, after = section.partition("</script>")
    if not end:
        raise ValueError("Unclosed release-timing data block")
    rebuilt = before + DATA_TAG + payload + end + after
    if args.write:
        PAGE.write_text(rebuilt, encoding="utf-8")
    elif rebuilt != html:
        raise SystemExit("Run scripts/build_release_timing_explorer.py --write to refresh data")


if __name__ == "__main__":
    main()
