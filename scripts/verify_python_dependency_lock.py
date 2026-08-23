#!/usr/bin/env python3
"""Verify the exact Python development lock against the current environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.version import Version

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = REPOSITORY / "requirements-dev.lock"
MINIMUM_LOCKED_PACKAGES = 80
_PIN = re.compile(r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;]+)")
_REQUIREMENT_NAME = re.compile(r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args()

    lock_path = args.lock.expanduser().resolve()
    locked = _locked_requirements(lock_path)
    if len(locked) < MINIMUM_LOCKED_PACKAGES:
        raise SystemExit(
            f"Python dependency lock is unexpectedly small: {len(locked)} packages"
        )

    declared = _project_requirements(REPOSITORY / "pyproject.toml")
    missing_direct = sorted(declared.keys() - locked.keys())
    if missing_direct:
        raise SystemExit(f"Python dependency lock omits direct requirements: {missing_direct}")
    incompatible_direct = sorted(
        f"{name}: locked={locked[name]} declared={requirement}"
        for name, requirement in declared.items()
        if Version(locked[name]) not in requirement.specifier
    )
    if incompatible_direct:
        raise SystemExit(
            "Python dependency lock violates direct requirement ranges: "
            f"{incompatible_direct}"
        )

    installed = _installed_versions()
    missing_installed = sorted(name for name in locked if name not in installed)
    mismatched = sorted(
        f"{name}: locked={version} installed={installed[name]}"
        for name, version in locked.items()
        if name in installed and installed[name] != version
    )
    if missing_installed or mismatched:
        raise SystemExit(
            "Python dependency environment differs from requirements-dev.lock: "
            f"missing={missing_installed} mismatched={mismatched}"
        )

    digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    print(
        f"python_lock_valid=true locked_packages={len(locked)} "
        f"direct_requirements={len(declared)} python={sys.version_info.major}."
        f"{sys.version_info.minor} lock_sha256={digest}"
    )


def _locked_requirements(path: Path) -> dict[str, str]:
    locked: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.fullmatch(line)
        if match is None:
            raise ValueError(f"lock line {line_number} is not one exact name==version pin")
        name = _canonical_name(match.group("name"))
        if name in locked:
            raise ValueError(f"lock contains duplicate package: {name}")
        locked[name] = match.group("version")
    return locked


def _project_requirement_names(path: Path) -> set[str]:
    return set(_project_requirements(path))


def _project_requirements(path: Path) -> dict[str, Requirement]:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    project = _object(payload.get("project"), "project")
    optional = _object(project.get("optional-dependencies"), "optional-dependencies")
    requirements = [
        *_strings(project.get("dependencies"), "project.dependencies"),
        *_strings(optional.get("dev"), "project.optional-dependencies.dev"),
    ]
    declared: dict[str, Requirement] = {}
    for requirement_text in requirements:
        match = _REQUIREMENT_NAME.match(requirement_text)
        if match is None:
            raise ValueError(f"could not parse direct requirement: {requirement_text}")
        requirement = Requirement(requirement_text)
        name = _canonical_name(requirement.name)
        if name in declared:
            raise ValueError(f"duplicate direct requirement: {name}")
        declared[name] = requirement
    return declared


def _installed_versions() -> dict[str, str]:
    installed: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        raw_name = distribution.metadata["Name"]
        if not raw_name:
            continue
        name = _canonical_name(raw_name)
        version = distribution.version
        previous = installed.get(name)
        if previous is not None and previous != version:
            raise SystemExit(
                f"multiple installed versions found for {name}: {previous}, {version}"
            )
        installed[name] = version
    return installed


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a table")
    return value


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be a string list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{label} must be a string list")
        result.append(item)
    return result


if __name__ == "__main__":
    main()
