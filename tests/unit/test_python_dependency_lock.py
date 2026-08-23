from __future__ import annotations

import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
LOCK_SCRIPT = REPOSITORY / "scripts/verify_python_dependency_lock.py"
SELECTOR_SCRIPT = REPOSITORY / "scripts/select_bootstrap_python.py"


def test_committed_python_lock_matches_the_verified_environment() -> None:
    completed = subprocess.run(
        [sys.executable, str(LOCK_SCRIPT)],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "python_lock_valid=true" in completed.stdout
    assert "locked_packages=81" in completed.stdout
    assert "lock_sha256=" in completed.stdout


def test_lock_parser_rejects_ranges_and_duplicates(tmp_path: Path) -> None:
    script = runpy.run_path(str(LOCK_SCRIPT), run_name="finreplay_lock_test")
    parse = cast(Callable[[Path], dict[str, str]], script["_locked_requirements"])
    ranged = tmp_path / "ranged.lock"
    ranged.write_text("numpy>=2\n", encoding="utf-8")
    duplicate = tmp_path / "duplicate.lock"
    duplicate.write_text("numpy==2.2.6\nNumPy==2.2.6\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exact name==version"):
        parse(ranged)
    with pytest.raises(ValueError, match="duplicate package"):
        parse(duplicate)


def test_lock_covers_every_direct_runtime_and_development_requirement() -> None:
    script = runpy.run_path(str(LOCK_SCRIPT), run_name="finreplay_lock_coverage_test")
    parse = cast(Callable[[Path], dict[str, str]], script["_locked_requirements"])
    project_names = cast(
        Callable[[Path], set[str]], script["_project_requirement_names"]
    )

    locked = parse(REPOSITORY / "requirements-dev.lock")
    required = project_names(REPOSITORY / "pyproject.toml")

    assert required <= locked.keys()
    assert len(required) == 25


def test_every_direct_pin_satisfies_its_declared_range() -> None:
    script = runpy.run_path(str(LOCK_SCRIPT), run_name="finreplay_lock_range_test")
    parse = cast(Callable[[Path], dict[str, str]], script["_locked_requirements"])
    requirements = cast(Callable[[Path], dict[str, Any]], script["_project_requirements"])

    locked = parse(REPOSITORY / "requirements-dev.lock")
    declared = requirements(REPOSITORY / "pyproject.toml")

    assert all(declared[name].specifier.contains(locked[name]) for name in declared)


def test_bootstrap_selector_accepts_only_tested_python_lines() -> None:
    script = runpy.run_path(str(SELECTOR_SCRIPT), run_name="finreplay_python_selector_test")
    supported = cast(Callable[[int, int], bool], script["_supported_version"])

    assert supported(3, 11)
    assert supported(3, 12)
    assert not supported(3, 10)
    assert not supported(3, 13)
