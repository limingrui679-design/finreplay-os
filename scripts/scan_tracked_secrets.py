#!/usr/bin/env python3
"""Scan tracked text without printing any matched credential material."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finreplay.security import scan_repository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = scan_repository(args.repository)
    findings = payload["findings"]
    if not isinstance(findings, list):
        raise TypeError("secret scan findings must be a list")
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(
        f"clean={str(payload['clean']).lower()} tracked_files={payload['tracked_file_count']} "
        f"text_files={payload['scanned_text_file_count']} findings={len(findings)} "
        f"scan_sha256={payload['scan_sha256']}"
    )
    if not payload["clean"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
