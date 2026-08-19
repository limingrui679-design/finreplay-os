#!/usr/bin/env python3
"""Record and verify a completed FinReplay Sites production deployment."""

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
from urllib.parse import urljoin

REPOSITORY = Path(__file__).resolve().parents[1]
PUBLIC_URL = "https://finreplay-evidence.limingrui2.chatgpt.site"
PROJECT_ID = "appgprj_6a8002cc2d308191a2cf9478863ce83e"
HOMEPAGE_MARKERS = (
    "FinReplay OS",
    "Independent review",
    "2,232 / 2,232",
    "18087f8fe4f6",
    "github.com/limingrui679-design/finreplay-os/tree/18087f8fe4f6",
)
CAPABILITY_MARKERS = (
    "Choose the question.",
    "Direct evidence",
    "Transferable method",
    "Boundary only",
    "Methods transfer; unearned domain claims do not.",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-revision", default="HEAD")
    parser.add_argument("--site-source-commit", required=True)
    parser.add_argument("--version-number", required=True, type=int)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--deployment-completed-at", required=True)
    parser.add_argument("--archive-content-hash", required=True)
    parser.add_argument("--archive-file-count", required=True, type=int)
    parser.add_argument("--archive-size-bytes", required=True, type=int)
    parser.add_argument("--access-policy-revision", required=True, type=int)
    parser.add_argument("--access-updated-at", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/evidence/public-site-deployment.json"),
    )
    args = parser.parse_args()

    parent_revision = _git("rev-parse", f"{args.parent_revision}^{{commit}}")
    site_source_commit = _git("rev-parse", f"{args.site_source_commit}^{{commit}}")
    parent_web_tree = _git("rev-parse", f"{parent_revision}:web")
    source_tree = _git("rev-parse", f"{site_source_commit}^{{tree}}")
    if source_tree != parent_web_tree:
        raise SystemExit("Sites source tree differs from the parent repository web tree")
    if args.version_number < 1 or args.archive_file_count < 1 or args.archive_size_bytes < 1:
        raise SystemExit("saved Sites version metadata is invalid")
    if not args.archive_content_hash.startswith("sha256:"):
        raise SystemExit("saved Sites archive content hash is not SHA-256")

    homepage, homepage_status = _fetch(PUBLIC_URL)
    homepage_text = homepage.decode("utf-8")
    missing_homepage = [marker for marker in HOMEPAGE_MARKERS if marker not in homepage_text]
    if homepage_status != 200 or missing_homepage:
        raise SystemExit(f"public homepage verification failed: {missing_homepage}")

    capability_url = f"{PUBLIC_URL}/capabilities"
    capability_body, capability_status = _fetch(capability_url)
    capability_text = capability_body.decode("utf-8")
    missing_capabilities = [
        marker for marker in CAPABILITY_MARKERS if marker not in capability_text
    ]
    if capability_status != 200 or missing_capabilities:
        raise SystemExit(f"public capability route verification failed: {missing_capabilities}")

    review_manifest_url = f"{PUBLIC_URL}/review/finreplay-review-manifest.json"
    review_manifest_body, review_manifest_status = _fetch(review_manifest_url)
    local_review_manifest = (
        REPOSITORY / "web/public/review/finreplay-review-manifest.json"
    ).read_bytes()
    if review_manifest_status != 200 or review_manifest_body != local_review_manifest:
        raise SystemExit("public review manifest differs from the deployed source tree")
    review_manifest = _object(json.loads(review_manifest_body))
    source_archive = _object(review_manifest.get("source_archive"))
    archive_file = source_archive.get("file")
    if not isinstance(archive_file, str) or not archive_file:
        raise SystemExit("public review manifest source archive is invalid")
    review_archive_url = f"{PUBLIC_URL}/review/{archive_file}"
    review_archive_body, review_archive_status = _fetch(review_archive_url)
    local_review_archive = (REPOSITORY / "web/public/review" / archive_file).read_bytes()
    if review_archive_status != 200 or review_archive_body != local_review_archive:
        raise SystemExit("public review archive differs from the deployed source tree")
    if (
        source_archive.get("bytes") != len(review_archive_body)
        or source_archive.get("sha256") != _sha256(review_archive_body)
    ):
        raise SystemExit("public review manifest does not bind the review archive")
    with zipfile.ZipFile(io.BytesIO(review_archive_body)) as handle:
        corrupt = handle.testzip()
        unsafe = [
            name
            for name in handle.namelist()
            if name.startswith("/") or ".." in Path(name).parts
        ]
    if corrupt is not None or unsafe:
        raise SystemExit("public review archive failed integrity or path-safety checks")

    scenario_manifest_url = f"{PUBLIC_URL}/replaypacks/manifest.json"
    scenario_manifest_body, scenario_manifest_status = _fetch(scenario_manifest_url)
    local_scenario_manifest = (
        REPOSITORY / "web/public/replaypacks/manifest.json"
    ).read_bytes()
    if scenario_manifest_status != 200 or scenario_manifest_body != local_scenario_manifest:
        raise SystemExit("public scenario manifest differs from the deployed source tree")
    scenario_manifest = _object(json.loads(scenario_manifest_body))
    bundles = scenario_manifest.get("bundles")
    if scenario_manifest.get("scenario_count") != 30 or not isinstance(bundles, list):
        raise SystemExit("public scenario manifest count or bundle list is invalid")
    if len(bundles) != 30:
        raise SystemExit("public scenario manifest does not contain 30 bundles")

    detail_count = 0
    detail_bindings: list[dict[str, str]] = []
    download_observations: list[dict[str, object]] = []
    for item in bundles:
        bundle = _object(item)
        slug = bundle.get("slug")
        pack_sha256 = bundle.get("pack_sha256")
        download_path = bundle.get("download_path")
        bundle_strings = (slug, pack_sha256, download_path)
        if not all(isinstance(value, str) and value for value in bundle_strings):
            raise SystemExit("public scenario bundle metadata is invalid")
        assert isinstance(slug, str)
        assert isinstance(pack_sha256, str)
        assert isinstance(download_path, str)

        detail_url = f"{PUBLIC_URL}/replays/{slug}"
        detail_body, detail_status = _fetch(detail_url)
        if detail_status != 200 or pack_sha256 not in detail_body.decode("utf-8"):
            raise SystemExit(f"public scenario detail does not bind pack hash: {slug}")
        detail_count += 1
        detail_bindings.append({"slug": slug, "pack_sha256": pack_sha256})

        download_url = urljoin(f"{PUBLIC_URL}/", download_path.lstrip("/"))
        download_body, download_status = _fetch(download_url)
        local_download = (REPOSITORY / "web/public" / download_path.lstrip("/")).read_bytes()
        if download_status != 200 or download_body != local_download:
            raise SystemExit(f"public scenario download differs from source: {slug}")
        if (
            bundle.get("bytes") != len(download_body)
            or bundle.get("sha256") != _sha256(download_body)
        ):
            raise SystemExit(f"public scenario manifest does not bind download: {slug}")
        download_observations.append(
            {"slug": slug, "bytes": len(download_body), "sha256": _sha256(download_body)}
        )

    assertions = {
        "production_deployment_succeeded": True,
        "public_access_mode": True,
        "anonymous_http_200": homepage_status == 200,
        "fixed_github_source_link_present": all(
            marker in homepage_text
            for marker in ("github.com/limingrui679-design/finreplay-os", "18087f8fe4f6")
        ),
        "capability_route_http_200": capability_status == 200,
        "capability_markers_present": not missing_capabilities,
        "review_manifest_http_200": review_manifest_status == 200,
        "review_archive_http_200": review_archive_status == 200,
        "review_archive_sha256_matches_manifest": (
            source_archive.get("sha256") == _sha256(review_archive_body)
        ),
        "scenario_detail_routes_http_200": detail_count == 30,
        "scenario_downloads_http_200": len(download_observations) == 30,
        "scenario_downloads_match_manifest": True,
        "sites_source_tree_matches_parent_web_tree": source_tree == parent_web_tree,
        "independent_review_remains_pending": (
            review_manifest.get("evidence_status")
            == "internally_proven_external_review_pending"
        ),
    }
    if not all(assertions.values()):
        raise SystemExit(f"public deployment assertion failed: {assertions}")

    recorded_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "schema_version": "1.3.0",
        "evidence_kind": "public_site_production_deployment",
        "recorded_at": recorded_at,
        "site": {
            "project_id": PROJECT_ID,
            "slug": "finreplay-evidence",
            "title": "FinReplay OS Evidence",
            "version_id": args.version_id,
            "version_number": args.version_number,
            "deployment_id": args.deployment_id,
            "deployment_status": "succeeded",
            "deployment_completed_at": args.deployment_completed_at,
            "public_url": PUBLIC_URL,
            "access_mode": "public",
            "access_policy_revision": args.access_policy_revision,
            "access_updated_at": args.access_updated_at,
        },
        "source_binding": {
            "parent_repository_commit": parent_revision,
            "site_source_commit": site_source_commit,
            "site_source_tree_git_oid": source_tree,
            "saved_version_archive_storage": {
                "archive_format": "tar",
                "content_hash": args.archive_content_hash,
                "file_count": args.archive_file_count,
                "size_bytes": args.archive_size_bytes,
            },
        },
        "verification": {
            "anonymous_http_status": homepage_status,
            "anonymous_http_verified_at": recorded_at,
            "required_markers": list(HOMEPAGE_MARKERS),
            "response_body_bytes": len(homepage),
            "response_body_sha256": _sha256(homepage),
            "review_manifest": {
                "url": review_manifest_url,
                "anonymous_http_status": review_manifest_status,
                "bytes": len(review_manifest_body),
                "sha256": _sha256(review_manifest_body),
            },
            "review_archive": {
                "url": review_archive_url,
                "anonymous_http_status": review_archive_status,
                "bytes": len(review_archive_body),
                "sha256": _sha256(review_archive_body),
            },
        },
        "capability_surface": {
            "url": capability_url,
            "anonymous_http_status": capability_status,
            "bytes": len(capability_body),
            "sha256": _sha256(capability_body),
            "capability_count": 10,
            "required_markers": list(CAPABILITY_MARKERS),
        },
        "scenario_surface": {
            "scenario_count": 30,
            "detail_routes_http_200": detail_count,
            "downloads_http_200": len(download_observations),
            "downloads_match_manifest": True,
            "details_bind_pack_hash": True,
            "manifest": {
                "url": scenario_manifest_url,
                "anonymous_http_status": scenario_manifest_status,
                "bytes": len(scenario_manifest_body),
                "sha256": _sha256(scenario_manifest_body),
                "manifest_sha256": scenario_manifest.get("manifest_sha256"),
            },
            "detail_binding_set_sha256": _hash(detail_bindings),
            "observed_archive_set_sha256": _hash(download_observations),
        },
        "assertions": assertions,
        "all_deployment_gates_passed": True,
        "claim_boundary": (
            f"This receipt proves that Sites version {args.version_number} was successfully "
            "deployed to the existing public production URL, returned anonymous HTTP 200, "
            "served the evidence-bounded capability route, and served the recorded review "
            "artifacts plus 30 manifest-bound scenario pages and downloads at the recorded "
            f"time. It binds the Sites source tree to repository commit {parent_revision[:12]}. "
            "It does not prove continuous uptime, independent review, security or accessibility "
            "certification, users, adoption, investment performance, or real-world impact."
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
        f"recorded=true version={args.version_number} scenarios={len(bundles)} "
        f"capabilities=10 receipt_sha256={payload['receipt_sha256']}"
    )


def _fetch(url: str) -> tuple[bytes, int]:
    last_error: Exception | None = None
    for attempt in range(5):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "FinReplay-deployment-recorder/1.3"},
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
