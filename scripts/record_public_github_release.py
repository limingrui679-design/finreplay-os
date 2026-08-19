#!/usr/bin/env python3
"""Record and verify a completed public FinReplay GitHub prerelease."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
REPOSITORY_NAME = "limingrui679-design/finreplay-os"
REPOSITORY_URL = f"https://github.com/{REPOSITORY_NAME}"
API_URL = f"https://api.github.com/repos/{REPOSITORY_NAME}"
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
    parser.add_argument("--published-head", required=True)
    parser.add_argument("--version-tag", required=True)
    parser.add_argument("--workflow-run-id", required=True, type=int)
    parser.add_argument(
        "--requested-source-commit",
        default="51a52337ce4ed485333fba1c21c8132692b9801e",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/evidence/public-github-release.json"),
    )
    args = parser.parse_args()

    published_head = _git("rev-parse", f"{args.published_head}^{{commit}}")
    requested_source = _git(
        "rev-parse", f"{args.requested_source_commit}^{{commit}}"
    )
    package_release_commit = _git("rev-list", "-n", "1", args.version_tag)
    if package_release_commit != published_head:
        raise SystemExit("version tag does not resolve to the published release head")
    if not _is_ancestor(requested_source, published_head):
        raise SystemExit("requested source commit is not an ancestor of the release head")

    repository = _object(_fetch_json(API_URL))
    remote_main = _object(_fetch_json(f"{API_URL}/git/ref/heads/main"))
    remote_object = _object(remote_main.get("object"))
    if remote_object.get("sha") != published_head:
        raise SystemExit("public GitHub main does not point to the published head")
    if (
        repository.get("full_name") != REPOSITORY_NAME
        or repository.get("visibility") != "public"
        or repository.get("default_branch") != "main"
        or repository.get("homepage") != EXPECTED_HOMEPAGE
        or set(repository.get("topics", [])) != EXPECTED_TOPICS
    ):
        raise SystemExit("public GitHub repository metadata differs")

    release = _object(_fetch_json(f"{API_URL}/releases/tags/{args.version_tag}"))
    workflow = _object(
        _fetch_json(f"{API_URL}/actions/runs/{args.workflow_run_id}")
    )
    if (
        release.get("tag_name") != args.version_tag
        or release.get("draft") is not False
        or release.get("prerelease") is not True
        or workflow.get("head_sha") != published_head
        or workflow.get("conclusion") != "success"
    ):
        raise SystemExit("GitHub prerelease or workflow status differs")

    package_version = args.version_tag.removeprefix("v")
    expected_assets = {
        "SHA256SUMS",
        f"finreplay_os-{package_version}-py3-none-any.whl",
        f"finreplay_os-{package_version}.tar.gz",
    }
    release_assets = release.get("assets")
    if not isinstance(release_assets, list):
        raise SystemExit("GitHub prerelease asset list is invalid")
    asset_metadata = {
        item.get("name"): item
        for item in release_assets
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if set(asset_metadata) != expected_assets:
        raise SystemExit("GitHub prerelease asset set differs")

    observed_assets: dict[str, bytes] = {}
    recorded_assets = []
    for name in sorted(expected_assets):
        metadata = _object(asset_metadata[name])
        url = metadata.get("browser_download_url")
        if not isinstance(url, str) or not url:
            raise SystemExit(f"GitHub prerelease asset URL is invalid: {name}")
        body, status = _fetch(url)
        if status != 200 or metadata.get("size") != len(body):
            raise SystemExit(f"GitHub prerelease asset differs: {name}")
        observed_assets[name] = body
        recorded_assets.append(
            {
                "name": name,
                "url": url,
                "anonymous_http_status": status,
                "bytes": len(body),
                "sha256": _sha256(body),
            }
        )
    checksum_lines = {
        line.split()[1]: line.split()[0]
        for line in observed_assets["SHA256SUMS"].decode("utf-8").splitlines()
        if line.strip()
    }
    for name, body in observed_assets.items():
        if name != "SHA256SUMS" and checksum_lines.get(name) != _sha256(body):
            raise SystemExit(f"SHA256SUMS does not bind GitHub prerelease asset: {name}")

    raw_readme_url = (
        f"https://raw.githubusercontent.com/{REPOSITORY_NAME}/{published_head}/README.md"
    )
    raw_readme, raw_status = _fetch(raw_readme_url)
    if raw_status != 200 or b"FinReplay OS" not in raw_readme:
        raise SystemExit("fixed-commit public README verification failed")

    source_archive_url = f"{REPOSITORY_URL}/archive/{published_head}.zip"
    source_archive, source_status = _fetch(source_archive_url)
    expected_archive_root = f"finreplay-os-{published_head}/"
    with zipfile.ZipFile(io.BytesIO(source_archive)) as handle:
        corrupt = handle.testzip()
        names = handle.namelist()
    if (
        source_status != 200
        or corrupt is not None
        or not names
        or names[0] != expected_archive_root
    ):
        raise SystemExit("fixed-commit public source archive verification failed")

    assertions = {
        "public_visibility": repository.get("visibility") == "public",
        "default_branch_is_main": repository.get("default_branch") == "main",
        "repository_discovery_metadata_present": (
            repository.get("homepage") == EXPECTED_HOMEPAGE
            and set(repository.get("topics", [])) == EXPECTED_TOPICS
        ),
        "requested_source_commit_is_published_ancestor": True,
        "version_tag_resolves_to_package_release": True,
        "github_release_is_prerelease": release.get("prerelease") is True,
        "github_release_workflow_succeeded": workflow.get("conclusion") == "success",
        "github_release_assets_http_200": all(
            item["anonymous_http_status"] == 200 for item in recorded_assets
        ),
        "github_release_checksums_match": True,
        "anonymous_raw_readme_http_200": raw_status == 200,
        "anonymous_source_archive_http_200": source_status == 200,
        "source_archive_integrity_passed": corrupt is None,
    }
    if not all(assertions.values()):
        raise SystemExit(f"public GitHub release assertion failed: {assertions}")

    recorded_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "schema_version": "1.1.0",
        "evidence_kind": "public_github_repository_release",
        "recorded_at": recorded_at,
        "repository": {
            "repository_id": repository.get("id"),
            "full_name": repository.get("full_name"),
            "html_url": repository.get("html_url"),
            "clone_url": repository.get("clone_url"),
            "visibility": repository.get("visibility"),
            "default_branch": repository.get("default_branch"),
            "homepage": repository.get("homepage"),
            "topics": sorted(repository.get("topics", [])),
        },
        "release_binding": {
            "published_head_commit": published_head,
            "remote_main_ref_at_verification": published_head,
            "requested_source_commit": requested_source,
            "package_release_commit": package_release_commit,
            "version_tag": args.version_tag,
        },
        "release": {
            "release_id": release.get("id"),
            "tag_name": release.get("tag_name"),
            "name": release.get("name"),
            "html_url": release.get("html_url"),
            "draft": release.get("draft"),
            "prerelease": release.get("prerelease"),
            "published_at": release.get("published_at"),
            "workflow_run_id": args.workflow_run_id,
            "workflow_url": workflow.get("html_url"),
            "workflow_conclusion": workflow.get("conclusion"),
            "checksums_match": True,
            "assets": recorded_assets,
        },
        "verification": {
            "verified_at": recorded_at,
            "raw_readme": {
                "url": raw_readme_url,
                "anonymous_http_status": raw_status,
                "bytes": len(raw_readme),
                "sha256": _sha256(raw_readme),
            },
            "source_archive": {
                "url": source_archive_url,
                "anonymous_http_status": source_status,
                "bytes": len(source_archive),
                "sha256": _sha256(source_archive),
                "archive_root": expected_archive_root,
                "zip_integrity_passed": True,
            },
        },
        "assertions": assertions,
        "all_release_gates_passed": True,
        "claim_boundary": (
            f"This receipt proves that the public GitHub repository exposed commit "
            f"{published_head[:12]} on main, retained requested Sites source commit "
            f"{requested_source[:12]} in its history, published prerelease {args.version_tag} "
            f"from commit {package_release_commit[:12]}, and returned anonymous HTTP 200 "
            "responses for fixed-commit source artifacts plus the wheel, source distribution, "
            "and checksum file whose recorded hashes passed at the recorded time. It does not "
            "prove continuous availability, independent review, source authenticity beyond the "
            "committed evidence, security certification, PyPI publication, users, adoption, "
            "investment performance, or real-world impact."
        ),
    }
    payload["receipt_sha256"] = _hash(payload)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"recorded=true repository={REPOSITORY_NAME} release={args.version_tag} "
        f"assets={len(recorded_assets)} receipt_sha256={payload['receipt_sha256']}"
    )


def _fetch_json(url: str) -> object:
    body, status = _fetch(url)
    if status != 200:
        raise SystemExit(f"GitHub API request did not return HTTP 200: {url}")
    return json.loads(body)


def _fetch(url: str) -> tuple[bytes, int]:
    last_error: Exception | None = None
    for attempt in range(5):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "FinReplay-GitHub-release-recorder/1.1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read(), response.status
        except (
            OSError,
            TimeoutError,
            http.client.IncompleteRead,
            urllib.error.URLError,
        ) as error:
            last_error = error
            if attempt < 4:
                time.sleep(2 * (attempt + 1))
    raise SystemExit(f"anonymous fetch failed after retries: {url}: {last_error}")


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit("expected a JSON object")
    return value


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=REPOSITORY,
    )
    return completed.returncode == 0


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
