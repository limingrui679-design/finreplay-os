#!/usr/bin/env python3
"""Verify the recorded public GitHub release and optionally recheck it anonymously."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
EXPECTED_REPOSITORY = "limingrui679-design/finreplay-os"
EXPECTED_REPOSITORY_URL = f"https://github.com/{EXPECTED_REPOSITORY}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "receipt",
        nargs="?",
        type=Path,
        default=Path("verification/evidence/public-github-release.json"),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="also fetch the fixed-commit raw README and source ZIP anonymously",
    )
    args = parser.parse_args()

    payload = _load_json(args.receipt)
    claimed_hash = payload.pop("receipt_sha256", None)
    if claimed_hash != _hash(payload):
        raise SystemExit("public GitHub release receipt_sha256 mismatch")
    if payload.get("all_release_gates_passed") is not True:
        raise SystemExit("public GitHub release gates did not all pass")
    if not all(payload["assertions"].values()):
        raise SystemExit("public GitHub release receipt contains a failed assertion")

    repository = payload["repository"]
    if repository["full_name"] != EXPECTED_REPOSITORY:
        raise SystemExit("public GitHub repository name differs")
    if repository["html_url"] != EXPECTED_REPOSITORY_URL:
        raise SystemExit("public GitHub repository URL differs")
    if repository["visibility"] != "public" or repository["default_branch"] != "main":
        raise SystemExit("GitHub repository is not recorded as public main")

    binding = payload["release_binding"]
    published_head = binding["published_head_commit"]
    requested_source = binding["requested_source_commit"]
    if _git("rev-parse", f"{published_head}^{{commit}}") != published_head:
        raise SystemExit("published GitHub head is absent from local history")
    if _git("rev-parse", f"{requested_source}^{{commit}}") != requested_source:
        raise SystemExit("requested source commit is absent from local history")
    if not _is_ancestor(requested_source, published_head):
        raise SystemExit("requested source commit is not an ancestor of published head")
    if not _is_ancestor(published_head, "HEAD"):
        raise SystemExit("published GitHub head is not an ancestor of current HEAD")

    verification = payload["verification"]
    raw = verification["raw_readme"]
    archive = verification["source_archive"]
    if raw["anonymous_http_status"] != 200:
        raise SystemExit("recorded anonymous raw README status is not 200")
    if archive["anonymous_http_status"] != 200 or not archive["zip_integrity_passed"]:
        raise SystemExit("recorded anonymous source archive check did not pass")

    live_detail = ""
    if args.live:
        raw_body, raw_status = _fetch(raw["url"])
        if raw_status != 200 or hashlib.sha256(raw_body).hexdigest() != raw["sha256"]:
            raise SystemExit("live fixed-commit README differs from recorded release")
        archive_body, archive_status = _fetch(archive["url"])
        if archive_status != 200 or hashlib.sha256(archive_body).hexdigest() != archive["sha256"]:
            raise SystemExit("live fixed-commit source archive differs from recorded release")
        with zipfile.ZipFile(io.BytesIO(archive_body)) as handle:
            corrupt = handle.testzip()
            names = handle.namelist()
        if corrupt is not None or not names or names[0] != archive["archive_root"]:
            raise SystemExit("live fixed-commit source archive failed integrity or root check")
        live_detail = (
            f" live_raw_http={raw_status} live_archive_http={archive_status} "
            f"live_archive_bytes={len(archive_body)}"
        )

    print(
        f"verified=true repository={EXPECTED_REPOSITORY} "
        f"published_head={published_head[:12]} receipt_sha256={claimed_hash}{live_detail}"
    )


def _fetch(url: str) -> tuple[bytes, int]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "FinReplay-GitHub-release-verifier/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read(), response.status


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=REPOSITORY,
    )
    return completed.returncode == 0


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


if __name__ == "__main__":
    main()
