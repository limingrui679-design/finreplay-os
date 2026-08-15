"""Deterministic, non-disclosing checks for secrets in tracked repository text."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from re import Pattern

__all__ = ["SecretFinding", "scan_repository", "scan_text", "tracked_paths"]


@dataclass(frozen=True, slots=True)
class ScanRule:
    rule_id: str
    pattern: Pattern[str]


@dataclass(frozen=True, slots=True)
class SecretFinding:
    rule_id: str
    path: str
    line: int
    matched_value_sha256: str


_RULES = (
    ScanRule(
        "private_key_pem",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE" r" KEY-----"
        ),
    ),
    ScanRule("aws_access_key", re.compile(r"A(?:KI|SI)A[0-9A-Z]{16}")),
    ScanRule(
        "github_token",
        re.compile(r"(?:gh(?:p|o|u|s|r)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ),
    ScanRule("openai_api_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ScanRule("slack_token", re.compile(r"xox(?:b|p|a|r|s)-[A-Za-z0-9-]{10,}")),
    ScanRule("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ScanRule("stripe_live_key", re.compile(r"(?:s|r)k_live_[A-Za-z0-9]{16,}")),
    ScanRule(
        "credential_in_url",
        re.compile(r"https?://[^\s/:@]+:[^\s/@]+@[^\s/]+", re.IGNORECASE),
    ),
    ScanRule("local_user_absolute_path", re.compile(r"/Us" r"ers/[^\s\"'<>]+")),
)


def scan_repository(repository: Path) -> dict[str, object]:
    """Return a self-hashed scan without retaining any matched credential text."""

    repository = repository.expanduser().resolve()
    paths = tracked_paths(repository)
    findings: list[SecretFinding] = []
    text_file_count = 0
    scanned_bytes = 0
    binary_file_count = 0
    file_entries: list[dict[str, object]] = []
    for path in paths:
        content = path.read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            binary_file_count += 1
            continue
        text_file_count += 1
        scanned_bytes += len(content)
        relative = path.relative_to(repository).as_posix()
        file_entries.append(
            {
                "path": relative,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        findings.extend(scan_text(text, relative))
    result: dict[str, object] = {
        "schema_version": "1.0.0",
        "scanner_path": "src/finreplay/security.py",
        "scanner_sha256": hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest(),
        "tracked_file_count": len(paths),
        "scanned_text_file_count": text_file_count,
        "skipped_binary_file_count": binary_file_count,
        "scanned_text_bytes": scanned_bytes,
        "tracked_text_set_sha256": _hash(file_entries),
        "rule_ids": [rule.rule_id for rule in _RULES],
        "findings": [asdict(item) for item in findings],
        "clean": not findings,
        "claim_boundary": (
            "This deterministic local scan checks current Git-tracked UTF-8 text for a bounded "
            "set of high-confidence credential formats, credential-bearing URLs, private-key "
            "headers, and local macOS user paths. It never writes matched text to output. It is "
            "not a replacement for full-history gitleaks, provider-side revocation, binary-file "
            "inspection, or an independent security audit."
        ),
    }
    result["scan_sha256"] = _hash(result)
    return result


def tracked_paths(repository: Path) -> list[Path]:
    values = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    return sorted(repository / value.decode() for value in values if value)


def scan_text(text: str, relative_path: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for rule in _RULES:
        for match in rule.pattern.finditer(text):
            findings.append(
                SecretFinding(
                    rule_id=rule.rule_id,
                    path=relative_path,
                    line=text.count("\n", 0, match.start()) + 1,
                    matched_value_sha256=hashlib.sha256(match.group(0).encode()).hexdigest(),
                )
            )
    return findings


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
