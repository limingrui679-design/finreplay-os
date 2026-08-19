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
EXPECTED_HOMEPAGE = "https://finreplay-evidence.limingrui2.chatgpt.site/"
EXPECTED_TOPICS = {
    "backtesting",
    "evidence",
    "financial-data",
    "point-in-time-data",
    "python",
    "quantitative-finance",
    "reproducible-research",
    "systemic-risk",
}


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
    schema_version = payload.get("schema_version")
    if schema_version not in {"1.0.0", "1.1.0"}:
        raise SystemExit("public GitHub release receipt schema version is unsupported")

    repository = payload["repository"]
    if repository["full_name"] != EXPECTED_REPOSITORY:
        raise SystemExit("public GitHub repository name differs")
    if repository["html_url"] != EXPECTED_REPOSITORY_URL:
        raise SystemExit("public GitHub repository URL differs")
    if repository["visibility"] != "public" or repository["default_branch"] != "main":
        raise SystemExit("GitHub repository is not recorded as public main")
    if schema_version == "1.1.0" and (
        repository.get("homepage") != EXPECTED_HOMEPAGE
        or set(repository.get("topics", [])) != EXPECTED_TOPICS
    ):
        raise SystemExit("GitHub repository discovery metadata differs")

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
    if schema_version == "1.1.0":
        package_release = binding["package_release_commit"]
        if _git("rev-parse", f"{package_release}^{{commit}}") != package_release:
            raise SystemExit("package release commit is absent from local history")
        if not _is_ancestor(package_release, published_head):
            raise SystemExit("package release commit is not an ancestor of published head")
        if _git("rev-list", "-n", "1", binding["version_tag"]) != package_release:
            raise SystemExit("version tag does not resolve to the package release commit")

    verification = payload["verification"]
    raw = verification["raw_readme"]
    archive = verification["source_archive"]
    if raw["anonymous_http_status"] != 200:
        raise SystemExit("recorded anonymous raw README status is not 200")
    if archive["anonymous_http_status"] != 200 or not archive["zip_integrity_passed"]:
        raise SystemExit("recorded anonymous source archive check did not pass")
    release_value = payload.get("release")
    release: dict[str, Any] = release_value if isinstance(release_value, dict) else {}
    if schema_version == "1.1.0":
        version_tag = binding["version_tag"]
        package_version = version_tag.removeprefix("v")
        if not package_version or version_tag != f"v{package_version}":
            raise SystemExit("GitHub Release version tag is malformed")
        expected_assets = {
            "SHA256SUMS",
            f"finreplay_os-{package_version}-py3-none-any.whl",
            f"finreplay_os-{package_version}.tar.gz",
        }
        if not release:
            raise SystemExit("GitHub Release metadata is missing")
        if (
            release.get("tag_name") != binding["version_tag"]
            or release.get("draft") is not False
            or release.get("prerelease") is not True
            or release.get("workflow_conclusion") != "success"
            or release.get("checksums_match") is not True
        ):
            raise SystemExit("GitHub Release status or checksum boundary differs")
        assets = release.get("assets")
        if not isinstance(assets, list) or {
            item.get("name") for item in assets
        } != expected_assets:
            raise SystemExit("GitHub Release asset set differs")
        if any(
            item.get("anonymous_http_status") != 200
            or not isinstance(item.get("bytes"), int)
            or item["bytes"] <= 0
            or not isinstance(item.get("sha256"), str)
            or len(item["sha256"]) != 64
            for item in assets
        ):
            raise SystemExit("GitHub Release asset metadata is invalid")

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
        if schema_version == "1.1.0":
            observed_assets = {}
            for asset in release["assets"]:
                body, status = _fetch(asset["url"])
                if (
                    status != 200
                    or len(body) != asset["bytes"]
                    or hashlib.sha256(body).hexdigest() != asset["sha256"]
                ):
                    raise SystemExit(f"live GitHub Release asset differs: {asset['name']}")
                observed_assets[asset["name"]] = body
            checksum_lines = {
                line.split()[1]: line.split()[0]
                for line in observed_assets["SHA256SUMS"].decode().splitlines()
                if line.strip()
            }
            for name, body in observed_assets.items():
                if name != "SHA256SUMS" and checksum_lines.get(name) != hashlib.sha256(
                    body
                ).hexdigest():
                    raise SystemExit(f"SHA256SUMS does not bind live release asset: {name}")
        live_detail = (
            f" live_raw_http={raw_status} live_archive_http={archive_status} "
            f"live_archive_bytes={len(archive_body)}"
        )

    print(
        f"verified=true repository={EXPECTED_REPOSITORY} "
        f"published_head={published_head[:12]} "
        f"release={binding.get('version_tag', 'not-recorded')} "
        f"receipt_sha256={claimed_hash}{live_detail}"
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
