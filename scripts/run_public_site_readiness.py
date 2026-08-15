#!/usr/bin/env python3
"""Verify the committed public site from a fresh archive and write a receipt."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", default="HEAD")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/evidence/public-site-readiness.json"),
    )
    args = parser.parse_args()

    revision = _git("rev-parse", f"{args.subject}^{{commit}}")
    tree = _git("rev-parse", f"{revision}^{{tree}}")
    committed_at = _git("show", "-s", "--format=%cI", revision)
    artifacts = _site_artifacts(revision)
    if not artifacts:
        raise SystemExit("subject commit contains no tracked web files")

    npm = shutil.which("npm")
    node = shutil.which("node")
    if npm is None or node is None:
        raise SystemExit("node and npm are required")

    with tempfile.TemporaryDirectory(prefix="finreplay-public-site-") as temporary:
        root = Path(temporary)
        _extract_site_archive(revision, root)
        site = root / "source" / "web"
        commands = {
            "clean_install": _run([npm, "ci"], site),
            "lint": _run([npm, "run", "lint"], site),
            "production_build_and_render_tests": _run([npm, "test"], site),
            "dependency_audit": _run([npm, "audit", "--json"], site),
        }
        audit = json.loads(commands["dependency_audit"].pop("stdout"))
        commands["dependency_audit"]["stdout_sha256"] = _text_hash(
            json.dumps(audit, sort_keys=True, separators=(",", ":"))
        )

        page = (site / "app/page.tsx").read_text(encoding="utf-8")
        rendered_test_output = commands["production_build_and_render_tests"].pop("stdout")
        test_pass_match = re.search(r"^# pass (?P<count>\d+)$", rendered_test_output, re.MULTILINE)
        test_fail_match = re.search(r"^# fail (?P<count>\d+)$", rendered_test_output, re.MULTILINE)
        if test_pass_match is None or test_fail_match is None:
            raise SystemExit("could not parse rendered site test result")
        rendered_passed = int(test_pass_match.group("count"))
        rendered_failed = int(test_fail_match.group("count"))
        commands["production_build_and_render_tests"]["stdout_sha256"] = _text_hash(
            rendered_test_output
        )

        hosting = _load_json(site / ".openai/hosting.json")
        review_manifest = _load_json(
            site / "public/review/finreplay-review-manifest.json"
        )
        og = (site / "public/og.png").read_bytes()
        og_width, og_height = _png_dimensions(og)
        vulnerabilities = audit["metadata"]["vulnerabilities"]
        assertions = {
            "clean_install_passed": commands["clean_install"]["exit_code"] == 0,
            "lint_passed": commands["lint"]["exit_code"] == 0,
            "production_build_and_render_tests_passed": (
                commands["production_build_and_render_tests"]["exit_code"] == 0
                and rendered_passed == 2
                and rendered_failed == 0
            ),
            "dependency_audit_clean": vulnerabilities["total"] == 0,
            "thirty_scenarios_in_source": len(re.findall(r"\{ id: \d+", page)) == 30,
            "nineteen_breaches_in_source": page.count('tone: "breach"') == 19,
            "external_review_stays_pending": (
                review_manifest.get("evidence_status")
                == "internally_proven_external_review_pending"
            ),
            "no_database_or_object_storage_binding": (
                hosting.get("d1") is None and hosting.get("r2") is None
            ),
            "hosting_project_is_configured": (
                hosting.get("project_id")
                == "appgprj_6a8002cc2d308191a2cf9478863ce83e"
            ),
            "no_public_url_claim_in_hosting_config": (
                "http" not in json.dumps(hosting)
            ),
            "social_card_is_png": (og_width, og_height) == (1731, 909),
        }
        if not all(assertions.values()):
            raise SystemExit(f"public site readiness assertion failed: {assertions}")

    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "evidence_kind": "public_site_local_release_readiness",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "subject": {
            "code_revision": revision,
            "tree_sha256": tree,
            "committed_at": committed_at,
        },
        "toolchain": {
            "node": _version([node, "--version"]),
            "npm": _version([npm, "--version"]),
        },
        "source_artifacts": {
            "file_count": len(artifacts),
            "total_bytes": sum(item["bytes"] for item in artifacts),
            "set_sha256": _hash(artifacts),
            "files": artifacts,
        },
        "commands": commands,
        "rendered_tests": {"passed": rendered_passed, "failed": rendered_failed},
        "dependency_audit": {
            "total_dependencies": audit["metadata"]["dependencies"]["total"],
            "vulnerabilities": vulnerabilities,
        },
        "site_content": {
            "scenario_count": 30,
            "visible_breach_count": 19,
            "og_png_bytes": len(og),
            "og_png_sha256": hashlib.sha256(og).hexdigest(),
            "og_png_width": og_width,
            "og_png_height": og_height,
        },
        "assertions": assertions,
        "site_state": {
            "hosted": False,
            "public_url": None,
            "independent_review_completed": False,
        },
        "all_required_gates_passed": True,
        "claim_boundary": (
            "This receipt proves that the committed read-only site installs from its lockfile, "
            "lints, builds, renders its asserted evidence surface, and has zero npm-audit "
            "findings at the recorded time. It does not prove hosting, public availability, "
            "external review, users, adoption, deployment, or real-world impact."
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
        f"ready=true revision={revision[:12]} files={len(artifacts)} "
        f"rendered_tests={rendered_passed} vulnerabilities={vulnerabilities['total']} "
        f"receipt_sha256={payload['receipt_sha256']}"
    )


def _extract_site_archive(revision: str, destination: Path) -> None:
    archive_bytes = subprocess.run(
        ["git", "archive", "--format=tar", "--prefix=source/", revision, "web"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise SystemExit("site archive contains an unsafe path")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise SystemExit("site archive file could not be read")
                target.write_bytes(source.read())
                target.chmod(member.mode & 0o777)
            else:
                raise SystemExit("site archive contains a non-file entry")


def _site_artifacts(revision: str) -> list[dict[str, object]]:
    paths = _git("ls-tree", "-r", "--name-only", revision, "web").splitlines()
    artifacts = []
    for relative in paths:
        content = subprocess.run(
            ["git", "show", f"{revision}:{relative}"],
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
        ).stdout
        artifacts.append(
            {
                "path": relative,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return artifacts


def _run(arguments: list[str], cwd: Path) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(arguments, cwd=cwd, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        details = (completed.stdout + "\n" + completed.stderr)[-4000:]
        raise SystemExit(f"command failed ({arguments[1:]}):\n{details}")
    return {
        "argv": [Path(arguments[0]).name, *arguments[1:]],
        "exit_code": completed.returncode,
        "elapsed_seconds": round(elapsed, 6),
        "stdout": completed.stdout,
        "stderr_sha256": _text_hash(completed.stderr),
    }


def _version(arguments: list[str]) -> str:
    return subprocess.run(
        arguments, check=True, capture_output=True, text=True
    ).stdout.strip()


def _png_dimensions(content: bytes) -> tuple[int, int]:
    if content[:8] != b"\x89PNG\r\n\x1a\n" or content[12:16] != b"IHDR":
        raise SystemExit("social card is not a valid PNG")
    return struct.unpack(">II", content[16:24])


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


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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
