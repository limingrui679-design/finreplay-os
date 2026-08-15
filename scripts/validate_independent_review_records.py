#!/usr/bin/env python3
"""Validate independent-review records without treating schema validity as external proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = REPOSITORY / "verification/review/independent-review.schema.json"
DEFAULT_RECORDS = REPOSITORY / "verification/review/records"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("records", nargs="*", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()

    schema_path = args.schema.expanduser().resolve()
    schema = _load_object(schema_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    record_paths = (
        sorted(path.resolve() for path in args.records)
        if args.records
        else sorted(DEFAULT_RECORDS.glob("*.json"))
    )

    errors: list[str] = []
    review_ids: set[str] = set()
    for record_path in record_paths:
        try:
            record = _load_object(record_path)
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            errors.append(f"{record_path}: {exc}")
            continue
        validation_errors = sorted(
            validator.iter_errors(record), key=lambda item: list(item.absolute_path)
        )
        for error in validation_errors:
            pointer = "/" + "/".join(str(item) for item in error.absolute_path)
            errors.append(f"{record_path}:{pointer}: {error.message}")
        errors.extend(_semantic_errors(record_path, record))
        review_id = record.get("review_id")
        if isinstance(review_id, str):
            if review_id in review_ids:
                errors.append(f"{record_path}:/review_id: duplicate review_id {review_id!r}")
            review_ids.add(review_id)

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        raise SystemExit(f"independent-review record validation failed: errors={len(errors)}")

    schema_sha256 = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    print(
        f"schema_valid_records={len(record_paths)} schema_validation_only=true "
        f"schema_sha256={schema_sha256} "
        f"review_ids={','.join(sorted(review_ids)) if review_ids else 'none'}"
    )


def _semantic_errors(path: Path, record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    reviewer = record.get("reviewer")
    attestation = record.get("attestation")
    if isinstance(reviewer, dict) and isinstance(attestation, dict):
        if attestation.get("signed_by") != reviewer.get("public_name"):
            errors.append(f"{path}:/attestation/signed_by: must equal reviewer.public_name")
        profile = reviewer.get("public_profile_url")
        if isinstance(profile, str):
            parsed = urlsplit(profile)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append(f"{path}:/reviewer/public_profile_url: must be an HTTP(S) URL")

    environment = record.get("environment")
    if isinstance(environment, dict) and isinstance(attestation, dict):
        started = _datetime(environment.get("started_at"), path, "/environment/started_at", errors)
        completed = _datetime(
            environment.get("completed_at"), path, "/environment/completed_at", errors
        )
        signed = _datetime(attestation.get("signed_at"), path, "/attestation/signed_at", errors)
        if started is not None and completed is not None and started > completed:
            errors.append(f"{path}:/environment: started_at must not follow completed_at")
        if completed is not None and signed is not None and completed > signed:
            errors.append(f"{path}:/attestation/signed_at: must not precede completed_at")

    subject = record.get("subject")
    resolution = record.get("resolution")
    if isinstance(subject, dict):
        archive_name = subject.get("source_archive_name")
        if isinstance(archive_name, str) and Path(archive_name).name != archive_name:
            errors.append(f"{path}:/subject/source_archive_name: must be a basename")
        target_id = subject.get("target_id")
        other = subject.get("other_target_description")
        if target_id == "other-bounded-target" and not isinstance(other, str):
            errors.append(
                f"{path}:/subject/other_target_description: required for other-bounded-target"
            )
        if target_id != "other-bounded-target" and other not in (None,):
            errors.append(
                f"{path}:/subject/other_target_description: only allowed for other-bounded-target"
            )

    subject_revision = subject.get("revision") if isinstance(subject, dict) else None
    resolution_revision = resolution.get("revision") if isinstance(resolution, dict) else None
    for pointer, revision in (
        ("/subject/revision", subject_revision),
        ("/resolution/revision", resolution_revision),
    ):
        if isinstance(revision, str) and not _commit_exists(revision):
            errors.append(f"{path}:{pointer}: commit is absent from repository history")
    if (
        isinstance(subject_revision, str)
        and isinstance(resolution_revision, str)
        and _commit_exists(subject_revision)
        and _commit_exists(resolution_revision)
        and not _is_ancestor(subject_revision, resolution_revision)
    ):
        errors.append(
            f"{path}:/resolution/revision: must descend from or equal the reviewed revision"
        )

    if isinstance(subject, dict):
        archive_name = subject.get("source_archive_name")
        archive_sha256 = subject.get("source_archive_sha256")
        if isinstance(archive_name, str) and isinstance(archive_sha256, str):
            committed_archive = REPOSITORY / "web/public/review" / archive_name
            if committed_archive.is_file():
                observed = hashlib.sha256(committed_archive.read_bytes()).hexdigest()
                if observed != archive_sha256:
                    errors.append(
                        f"{path}:/subject/source_archive_sha256: differs from committed archive"
                    )
    return errors


def _datetime(
    value: object, path: Path, pointer: str, errors: list[str]
) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path}:{pointer}: invalid RFC 3339 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{path}:{pointer}: timezone offset is required")
        return None
    return parsed


def _commit_exists(revision: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
            cwd=REPOSITORY,
            capture_output=True,
        ).returncode
        == 0
    )


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=REPOSITORY,
            capture_output=True,
        ).returncode
        == 0
    )


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


if __name__ == "__main__":
    main()
