#!/usr/bin/env python3
"""Verify the recorded Sites production deployment and optionally recheck it live."""

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
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

REPOSITORY = Path(__file__).resolve().parents[1]
EXPECTED_URL = "https://finreplay-evidence.limingrui2.chatgpt.site"
EXPECTED_PROJECT_ID = "appgprj_6a8002cc2d308191a2cf9478863ce83e"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "receipt",
        nargs="?",
        type=Path,
        default=Path("verification/evidence/public-site-deployment.json"),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="also perform a fresh anonymous HTTP request to the public site",
    )
    args = parser.parse_args()

    payload = _load_json(args.receipt)
    claimed_hash = payload.pop("receipt_sha256", None)
    if claimed_hash != _hash(payload):
        raise SystemExit("public site deployment receipt_sha256 mismatch")
    if payload.get("all_deployment_gates_passed") is not True:
        raise SystemExit("public site deployment gates did not all pass")
    if not all(payload["assertions"].values()):
        raise SystemExit("public site deployment receipt contains a failed assertion")
    schema_version = payload.get("schema_version")
    if schema_version not in {"1.1.0", "1.2.0", "1.3.0"}:
        raise SystemExit("public site deployment receipt schema version is unsupported")

    site = payload["site"]
    if site["project_id"] != EXPECTED_PROJECT_ID:
        raise SystemExit("public site project ID differs")
    if site["public_url"] != EXPECTED_URL:
        raise SystemExit("public site URL differs")
    if site["deployment_status"] != "succeeded" or site["access_mode"] != "public":
        raise SystemExit("public site is not recorded as successfully public")
    if not isinstance(site["version_number"], int) or site["version_number"] < 1:
        raise SystemExit("recorded Sites version is invalid")

    source = payload["source_binding"]
    parent_revision = source["parent_repository_commit"]
    observed_tree = _git("rev-parse", f"{parent_revision}:web")
    if observed_tree != source["site_source_tree_git_oid"]:
        raise SystemExit("parent repository web tree differs from Sites source tree")
    if _git("rev-parse", "HEAD:web") != source["site_source_tree_git_oid"]:
        raise SystemExit("current checkout web tree differs from deployed source tree")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", parent_revision, "HEAD"],
        cwd=REPOSITORY,
    )
    if ancestor.returncode != 0:
        raise SystemExit("deployment source commit is not an ancestor of HEAD")

    verification = payload["verification"]
    if verification["anonymous_http_status"] != 200:
        raise SystemExit("recorded anonymous HTTP status is not 200")
    if (
        verification["response_body_bytes"] <= 0
        or len(verification["response_body_sha256"]) != 64
    ):
        raise SystemExit("recorded public site response metadata is invalid")
    markers = verification["required_markers"]
    if (
        not isinstance(markers, list)
        or len(markers) != len(set(markers))
        or not all(isinstance(marker, str) and marker for marker in markers)
        or not {"FinReplay OS", "Independent review"}.issubset(markers)
    ):
        raise SystemExit("public site marker contract differs")
    manifest = verification["review_manifest"]
    archive = verification["review_archive"]
    for name, artifact in (("manifest", manifest), ("archive", archive)):
        if artifact["anonymous_http_status"] != 200:
            raise SystemExit(f"recorded public review {name} status is not 200")
        if artifact["bytes"] <= 0 or len(artifact["sha256"]) != 64:
            raise SystemExit(f"recorded public review {name} metadata is invalid")
    scenario_surface_value = payload.get("scenario_surface")
    scenario_surface: dict[str, Any] = (
        scenario_surface_value if isinstance(scenario_surface_value, dict) else {}
    )
    if schema_version in {"1.2.0", "1.3.0"}:
        if not scenario_surface:
            raise SystemExit("public scenario surface metadata is missing")
        if (
            scenario_surface.get("scenario_count") != 30
            or scenario_surface.get("detail_routes_http_200") != 30
            or scenario_surface.get("downloads_http_200") != 30
            or scenario_surface.get("downloads_match_manifest") is not True
            or scenario_surface.get("details_bind_pack_hash") is not True
        ):
            raise SystemExit("public scenario surface counts or bindings differ")
        scenario_manifest = scenario_surface.get("manifest")
        if (
            not isinstance(scenario_manifest, dict)
            or scenario_manifest.get("anonymous_http_status") != 200
            or scenario_manifest.get("bytes", 0) <= 0
            or len(scenario_manifest.get("sha256", "")) != 64
            or len(scenario_manifest.get("manifest_sha256", "")) != 64
        ):
            raise SystemExit("public scenario manifest metadata is invalid")
    capability_value = payload.get("capability_surface")
    capability_surface: dict[str, Any] = (
        capability_value if isinstance(capability_value, dict) else {}
    )
    if schema_version == "1.3.0":
        capability_markers = capability_surface.get("required_markers")
        if (
            capability_surface.get("url") != f"{EXPECTED_URL}/capabilities"
            or capability_surface.get("anonymous_http_status") != 200
            or capability_surface.get("capability_count") != 10
            or capability_surface.get("bytes", 0) <= 0
            or len(capability_surface.get("sha256", "")) != 64
            or not isinstance(capability_markers, list)
            or len(capability_markers) != len(set(capability_markers))
            or not all(
                isinstance(marker, str) and marker for marker in capability_markers
            )
        ):
            raise SystemExit("public capability surface metadata is invalid")
        if (
            len(scenario_surface.get("detail_binding_set_sha256", "")) != 64
            or len(scenario_surface.get("observed_archive_set_sha256", "")) != 64
        ):
            raise SystemExit("public scenario observation-set metadata is invalid")

    live_detail = ""
    if args.live:
        body, status = _fetch(EXPECTED_URL)
        if status != 200:
            raise SystemExit(f"live anonymous HTTP status differs: {status}")
        decoded = body.decode("utf-8")
        missing = [marker for marker in markers if marker not in decoded]
        if missing:
            raise SystemExit(f"live public site markers missing: {missing}")

        manifest_body, manifest_status = _fetch(manifest["url"])
        if (
            manifest_status != 200
            or len(manifest_body) != manifest["bytes"]
            or hashlib.sha256(manifest_body).hexdigest() != manifest["sha256"]
        ):
            raise SystemExit("live public review manifest differs from deployment receipt")
        manifest_payload = json.loads(manifest_body)

        archive_body, archive_status = _fetch(archive["url"])
        if (
            archive_status != 200
            or len(archive_body) != archive["bytes"]
            or hashlib.sha256(archive_body).hexdigest() != archive["sha256"]
        ):
            raise SystemExit("live public review archive differs from deployment receipt")
        if (
            manifest_payload["source_archive"]["bytes"] != archive["bytes"]
            or manifest_payload["source_archive"]["sha256"] != archive["sha256"]
        ):
            raise SystemExit("public review manifest does not bind the downloaded archive")
        with zipfile.ZipFile(io.BytesIO(archive_body)) as handle:
            if handle.testzip() is not None:
                raise SystemExit("live public review archive failed ZIP integrity")
        if schema_version in {"1.2.0", "1.3.0"}:
            scenario_manifest_body, scenario_manifest_status = _fetch(
                scenario_surface["manifest"]["url"]
            )
            if (
                scenario_manifest_status != 200
                or len(scenario_manifest_body) != scenario_surface["manifest"]["bytes"]
                or hashlib.sha256(scenario_manifest_body).hexdigest()
                != scenario_surface["manifest"]["sha256"]
            ):
                raise SystemExit("live public scenario manifest differs from receipt")
            scenario_payload = json.loads(scenario_manifest_body)
            if (
                scenario_payload.get("scenario_count") != 30
                or scenario_payload.get("manifest_sha256")
                != scenario_surface["manifest"]["manifest_sha256"]
            ):
                raise SystemExit("live public scenario manifest count or self-hash differs")
            bundles = scenario_payload.get("bundles")
            if not isinstance(bundles, list) or len(bundles) != 30:
                raise SystemExit("live public scenario bundle list differs")
            detail_bindings: list[dict[str, str]] = []
            download_observations: list[dict[str, object]] = []
            for item in bundles:
                if not isinstance(item, dict):
                    raise SystemExit("live public scenario bundle entry is invalid")
                slug = item.get("slug")
                pack_sha256 = item.get("pack_sha256")
                download_path = item.get("download_path")
                values = (slug, pack_sha256, download_path)
                if not all(isinstance(value, str) and value for value in values):
                    raise SystemExit("live public scenario bundle fields are invalid")
                assert isinstance(slug, str)
                assert isinstance(pack_sha256, str)
                assert isinstance(download_path, str)
                detail_body, detail_status = _fetch(f"{EXPECTED_URL}/replays/{slug}")
                if detail_status != 200 or pack_sha256 not in detail_body.decode("utf-8"):
                    raise SystemExit(f"live scenario detail differs: {slug}")
                detail_bindings.append({"slug": slug, "pack_sha256": pack_sha256})
                download_body, download_status = _fetch(
                    urljoin(f"{EXPECTED_URL}/", download_path.lstrip("/"))
                )
                if (
                    download_status != 200
                    or item.get("bytes") != len(download_body)
                    or item.get("sha256") != hashlib.sha256(download_body).hexdigest()
                ):
                    raise SystemExit(f"live scenario download differs: {slug}")
                download_observations.append(
                    {
                        "slug": slug,
                        "bytes": len(download_body),
                        "sha256": hashlib.sha256(download_body).hexdigest(),
                    }
                )
            if schema_version == "1.3.0" and (
                _hash(detail_bindings)
                != scenario_surface["detail_binding_set_sha256"]
                or _hash(download_observations)
                != scenario_surface["observed_archive_set_sha256"]
            ):
                raise SystemExit("live public scenario observation set differs")
        if schema_version == "1.3.0":
            capability_body, capability_status = _fetch(capability_surface["url"])
            capability_text = capability_body.decode("utf-8")
            missing_capabilities = [
                marker
                for marker in capability_surface["required_markers"]
                if marker not in capability_text
            ]
            if capability_status != 200 or missing_capabilities:
                raise SystemExit(
                    f"live public capability surface differs: {missing_capabilities}"
                )
        live_detail = (
            f" live_http_status={status} live_bytes={len(body)} "
            f"review_archive_bytes={len(archive_body)}"
        )

    print(
        f"verified=true version={site['version_number']} "
        f"deployment={site['deployment_id']} access=public "
        f"receipt_sha256={claimed_hash}{live_detail}"
    )


def _fetch(url: str) -> tuple[bytes, int]:
    last_error: Exception | None = None
    for attempt in range(5):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "FinReplay-deployment-verifier/1.3"},
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
