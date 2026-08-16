#!/usr/bin/env python3
"""Run FinReplay's complete internal quality gates and write a self-hashed receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from finreplay.security import scan_repository

REPOSITORY = Path(__file__).resolve().parents[1]
_PASSED_PATTERN = re.compile(r"(?P<count>[0-9,]+) passed")
_HOSTILE_TEST_PREFIXES = {
    "hostile_archive_and_expansion_limits": (
        "tests/unit/test_census_ft900.py::test_ft900_rejects_schema_and_crosscheck_failures"
    ),
    "hostile_csv_container_and_schema": (
        "tests/unit/test_sec_edgar_log_scale.py::test_extract_archive_fails_closed"
    ),
    "hostile_json_and_content_type": (
        "tests/unit/test_sec_submissions.py::test_sec_json_and_content_type_fail_closed"
    ),
    "unsafe_archive_paths": (
        "tests/unit/test_replaystudio.py::test_file_entries_require_canonical_relative_posix_paths"
    ),
    "cli_build_verify": (
        "tests/unit/test_replaystudio.py::test_cli_builds_archives_and_verifies_replaypack"
    ),
    "python_api_build_verify": (
        "tests/unit/test_api.py::test_python_api_builds_archives_loads_and_verifies"
    ),
    "no_key_no_network_demo": (
        "tests/integration/test_no_key_demo.py::"
        "test_no_key_no_network_demo_rebuilds_through_cli_and_api"
    ),
}
_EVIDENCE_PATHS = (
    ".github/workflows/security.yml",
    ".github/workflows/verify.yml",
    "pyproject.toml",
    "scripts/run_internal_quality_gates.py",
    "scripts/scan_tracked_secrets.py",
    "scripts/validate_independent_review_records.py",
    "scripts/verify_no_key_demo.py",
    "src/finreplay/api.py",
    "src/finreplay/security.py",
    "verification/claims/public-claims.json",
    "verification/evidence/capitalallocator-benchmark.json",
    "verification/evidence/executionlab-golden.json",
    "verification/evidence/replaystudio-browser-check.json",
    "verification/scale/sec-edgar/latest-query-benchmark-receipt.json",
    "verification/scenarios/latest-summary.json",
    "verification/review/independent-review.schema.json",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/evidence/internal-quality-gates.json"),
    )
    args = parser.parse_args()
    _require_clean_checkout()
    revision = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    commit_time = _git("show", "-s", "--format=%cI", "HEAD")
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    with tempfile.TemporaryDirectory(prefix="finreplay-quality-") as temporary:
        temporary_root = Path(temporary)
        collection = _run(
            "test_collection",
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        )
        node_ids = tuple(
            line.strip()
            for line in collection["stdout"].splitlines()
            if "::" in line and not line.startswith("<")
        )
        if len(node_ids) < 100:
            raise SystemExit("fewer than 100 tests were collected")
        hostile_counts = {
            gate_id: sum(node_id.startswith(prefix) for node_id in node_ids)
            for gate_id, prefix in _HOSTILE_TEST_PREFIXES.items()
        }
        if not all(hostile_counts.values()):
            raise SystemExit(f"required hostile/interface test locator missing: {hostile_counts}")

        lint = _run("lint", [str(_tool("ruff")), "check", "."])
        typing = _run("typing", [str(_tool("mypy")), "src", "tests", "scripts"])
        coverage_path = temporary_root / "coverage.json"
        tests = _run(
            "tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "--cov=finreplay",
                f"--cov-report=json:{coverage_path}",
                "--cov-report=term",
            ],
        )
        test_match = _PASSED_PATTERN.search(tests["stdout"])
        if test_match is None:
            raise SystemExit("could not parse the passing test count")
        passed_test_count = int(test_match.group("count").replace(",", ""))
        if passed_test_count != len(node_ids):
            raise SystemExit(
                f"collected/passed test count differs: {len(node_ids)} vs {passed_test_count}"
            )
        coverage_json = _load_json(coverage_path)
        coverage_meta = coverage_json["meta"]
        coverage_totals = coverage_json["totals"]
        if coverage_meta.get("branch_coverage") is not True:
            raise SystemExit("coverage run did not enable branch measurement")
        combined_percent = float(coverage_totals["percent_covered"])
        if combined_percent < 90.0:
            raise SystemExit(f"branch-aware combined coverage below 90%: {combined_percent}")
        branch_total = int(coverage_totals["num_branches"])
        branch_covered = int(coverage_totals["covered_branches"])
        branch_percent = 100.0 * branch_covered / branch_total if branch_total else 100.0

        dependency = _run(
            "dependency_audit",
            [str(_tool("pip-audit")), "--local", "--format", "json"],
            summary_from="stderr",
        )
        dependency_payload = json.loads(dependency["stdout"])
        dependencies = dependency_payload.get("dependencies")
        if not isinstance(dependencies, list):
            raise SystemExit("pip-audit JSON did not contain a dependency list")
        audited_dependencies = [
            item for item in dependencies if isinstance(item.get("version"), str)
        ]
        skipped_dependencies = [
            {
                "name": item.get("name"),
                "skip_reason": item.get("skip_reason"),
            }
            for item in dependencies
            if not isinstance(item.get("version"), str)
        ]
        project_version = tomllib.loads(
            (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        expected_local_skip = {
            "name": "finreplay-os",
            "skip_reason": (
                "Dependency not found on PyPI and could not be audited: "
                f"finreplay-os ({project_version})"
            ),
        }
        if skipped_dependencies not in ([], [expected_local_skip]):
            raise SystemExit(f"unexpected pip-audit skipped dependencies: {skipped_dependencies}")
        vulnerabilities = [
            {"name": item["name"], "version": item["version"], "vulns": item["vulns"]}
            for item in audited_dependencies
            if item.get("vulns")
        ]
        if vulnerabilities:
            raise SystemExit("pip-audit reported known vulnerabilities")

        secret_scan = scan_repository(REPOSITORY)
        if secret_scan["clean"] is not True:
            raise SystemExit("tracked secret/privacy scan reported findings")

        no_key = _run("no_key_demo", [sys.executable, "scripts/verify_no_key_demo.py"])
        for marker in (
            "credentials_present=0",
            "network_attempts=0",
            "cli=true api=true",
            "engines=7 claims=5",
        ):
            if marker not in no_key["stdout"]:
                raise SystemExit(f"no-key demo omitted required marker: {marker}")

        replaystudio = _run(
            "replaystudio_reconstruction",
            [sys.executable, "scripts/verify_replaystudio_golden.py"],
        )
        browser = _run(
            "replaystudio_browser_receipt",
            [sys.executable, "scripts/verify_replaystudio_browser_receipt.py"],
        )
        executionlab = _run(
            "executionlab_benchmark",
            [sys.executable, "scripts/verify_executionlab_golden.py"],
        )
        capitalallocator = _run(
            "capitalallocator_benchmark",
            [sys.executable, "scripts/verify_capitalallocator_benchmark.py"],
        )
        scenarios = _run(
            "scenario_catalog",
            [sys.executable, "scripts/verify_scenario_catalog.py"],
        )
        independent_reviews = _run(
            "independent_review_record_catalog",
            [sys.executable, "scripts/validate_independent_review_records.py"],
        )
        if "schema_validation_only=true" not in independent_reviews["stdout"]:
            raise SystemExit("independent-review catalog omitted its schema-only boundary")
        rebuilt_claims = temporary_root / "public-claims.json"
        claims = _run(
            "public_claim_registry",
            [
                sys.executable,
                "scripts/build_public_claim_registry.py",
                "--output",
                str(rebuilt_claims),
            ],
        )
        committed_claims = REPOSITORY / "verification/claims/public-claims.json"
        if rebuilt_claims.read_bytes() != committed_claims.read_bytes():
            raise SystemExit("public claim registry did not rebuild byte-identically")

    command_results = {
        item["gate_id"]: _public_command_result(item)
        for item in (
            collection,
            lint,
            typing,
            tests,
            dependency,
            no_key,
            replaystudio,
            browser,
            executionlab,
            capitalallocator,
            scenarios,
            independent_reviews,
            claims,
        )
    }
    payload: dict[str, Any] = {
        "schema_version": "1.1.0",
        "evidence_kind": "clean_commit_internal_quality_gate_run",
        "generated_at": generated_at,
        "subject": {
            "code_revision": revision,
            "tree_sha256": tree,
            "commit_time": commit_time,
            "clean_worktree_before_run": True,
        },
        "runtime": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
            "python_command": ".venv/bin/python",
        },
        "tests": {
            "collected": len(node_ids),
            "passed": passed_test_count,
            "failed": 0,
            "node_ids_sha256": _hash(node_ids),
            "test_files": len({node_id.split("::", 1)[0] for node_id in node_ids}),
            "required_test_threshold": 100,
            "representative_hostile_and_interface_test_counts": hostile_counts,
        },
        "coverage": {
            "branch_measurement_enabled": True,
            "combined_percent": round(combined_percent, 6),
            "required_combined_percent": 90.0,
            "statements": int(coverage_totals["num_statements"]),
            "covered_lines": int(coverage_totals["covered_lines"]),
            "missing_lines": int(coverage_totals["missing_lines"]),
            "branches": branch_total,
            "covered_branches": branch_covered,
            "missing_branches": int(coverage_totals["missing_branches"]),
            "branch_only_percent_reported_for_context": round(branch_percent, 6),
        },
        "dependency_audit": {
            "tool": "pip-audit",
            "package_count": len(audited_dependencies),
            "known_vulnerability_count": 0,
            "skipped_package_count": len(skipped_dependencies),
            "skipped_packages": skipped_dependencies,
            "packages": [
                {"name": item["name"], "version": item["version"]}
                for item in audited_dependencies
            ],
            "claim_boundary": (
                "This is the pip-audit result at generated_at for the complete resolved local "
                "environment, including the installer and development dependencies. "
                "The local FinReplay package is advisory-audited when its exact release coordinate "
                "is available to pip-audit; otherwise it is explicitly listed as skipped, while "
                "its code remains covered by the recorded tests, typing, lint, and scans. "
                "Vulnerability databases can change after this receipt."
            ),
        },
        "secret_and_privacy_scan": secret_scan,
        "security_ci": {
            "gitleaks": "configured_as_pinned_GitHub_Action_not_executed_locally_in_this_receipt",
            "codeql": "configured_as_pinned_GitHub_Action_not_executed_locally_in_this_receipt",
            "dependency_review": (
                "configured_as_pinned_GitHub_Action_for_pull_requests_not_executed_locally"
            ),
        },
        "product_and_reproducibility": {
            "no_key_no_network_demo_passed": True,
            "python_api_passed": True,
            "cli_passed": True,
            "fresh_static_pack_and_zip_reconstruction_passed": True,
            "responsive_accessibility_browser_receipt_verified": True,
            "all_five_evidence_labels_verified": True,
            "scenario_catalog_verified": True,
            "independent_review_record_catalog_schema_checked": True,
            "public_claim_registry_rebuilt_byte_identically": True,
            "fixed_benchmark_receipts_verified": True,
            "billion_row_benchmark_bound_by_full_test_suite": True,
        },
        "subject_artifacts": [_artifact(path) for path in _EVIDENCE_PATHS],
        "commands": command_results,
        "all_required_gates_passed": True,
        "claim_boundary": (
            "This receipt proves the listed internal checks passed on one clean Git commit in the "
            "recorded local environment. Coverage is the standard combined statement-plus-branch "
            "percentage with branch measurement enabled; the branch-only percentage is reported "
            "separately for context. The targeted tracked-text scan is not full-history gitleaks, "
            "and configured CI services were not locally executed. Browser evidence is maintainer "
            "recorded. This receipt does not prove source authenticity, external security or "
            "accessibility certification, hosted deployment, independent review, adoption, users, "
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
        f"passed=true revision={revision[:12]} tests={passed_test_count} "
        f"coverage={combined_percent:.2f}% vulnerabilities=0 secrets=0 "
        f"receipt_sha256={payload['receipt_sha256']}"
    )


def _run(
    gate_id: str,
    command: list[str],
    *,
    summary_from: str = "stdout",
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
    )
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        tail = "\n".join((completed.stdout + "\n" + completed.stderr).splitlines()[-30:])
        raise SystemExit(f"quality gate failed: {gate_id}\n{tail}")
    selected = completed.stderr if summary_from == "stderr" else completed.stdout
    summary = next(
        (line.strip() for line in reversed(selected.splitlines()) if line.strip()),
        "passed with no textual output",
    )
    return {
        "gate_id": gate_id,
        "command": [_display_argument(item) for item in command],
        "exit_code": completed.returncode,
        "elapsed_seconds": round(elapsed, 6),
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "summary": summary[:500],
        "stdout": completed.stdout,
    }


def _public_command_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "stdout"}


def _display_argument(value: str) -> str:
    path = Path(value)
    if value == sys.executable:
        return ".venv/bin/python"
    try:
        return path.resolve().relative_to(REPOSITORY).as_posix()
    except (OSError, ValueError):
        return value


def _tool(name: str) -> Path:
    path = Path(sys.executable).with_name(name)
    if not path.is_file():
        raise SystemExit(f"required quality tool is missing: {name}")
    return path


def _artifact(relative: str) -> dict[str, object]:
    path = REPOSITORY / relative
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _require_clean_checkout() -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise SystemExit("quality receipt must start from a clean worktree")


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


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
