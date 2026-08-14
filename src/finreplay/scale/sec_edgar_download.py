"""Accountable, resumable retrieval for official SEC EDGAR access-log archives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationInfo, model_validator

from finreplay.scale.sec_edgar_logs import (
    SECLogInventoryLock,
    SECLogPartition,
    parse_sec_log_inventory,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CONTENT_RANGE_PATTERN = re.compile(r"^bytes (?P<start>[0-9]+)-(?P<end>[0-9]+)/(?P<total>[0-9]+)$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SECLogDownloadReceipt(_StrictModel):
    """Self-hashed observation of a download, resume, restart, or existing local file."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    partition_date: date
    listed_url: HttpUrl
    source_url: HttpUrl
    list_page_url: HttpUrl
    observation_started_at: datetime
    observation_completed_at: datetime
    mode: Literal["downloaded", "resumed", "restarted", "verified_existing"]
    transfer_status_code: int | None = Field(default=None, ge=100, le=599)
    resumed_from_bytes: int = Field(ge=0)
    network_response_bytes: int = Field(ge=0)
    network_response_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    content_range: str | None = Field(default=None, max_length=200)
    declared_total_bytes: int | None = Field(default=None, gt=0)
    source_etag: str | None = Field(default=None, min_length=1, max_length=500)
    source_last_modified: str | None = Field(default=None, min_length=1, max_length=200)
    archive_filename: str
    archive_bytes: int = Field(gt=0)
    archive_sha256: str = Field(pattern=_SHA256_PATTERN)
    user_agent_sha256: str = Field(pattern=_SHA256_PATTERN)
    claim_boundary: str = Field(min_length=250, max_length=3_000)
    receipt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_receipt(self, info: ValidationInfo) -> SECLogDownloadReceipt:
        SECLogPartition.model_validate(
            {
                "partition_date": self.partition_date,
                "listed_url": self.listed_url,
                "source_url": self.source_url,
                "list_page_url": self.list_page_url,
            }
        )
        _require_aware(self.observation_started_at, "observation_started_at")
        _require_aware(self.observation_completed_at, "observation_completed_at")
        if self.observation_completed_at < self.observation_started_at:
            raise ValueError("download observation completion cannot precede its start")
        if self.archive_filename != f"log{self.partition_date:%Y%m%d}.zip":
            raise ValueError("SEC log download filename does not match partition_date")
        response_values = (
            self.transfer_status_code,
            self.network_response_sha256,
            self.declared_total_bytes,
        )
        if self.mode == "verified_existing":
            if any(value is not None for value in response_values):
                raise ValueError("verified_existing cannot claim an observed HTTP response")
            if self.network_response_bytes != 0 or self.resumed_from_bytes != 0:
                raise ValueError("verified_existing cannot claim transferred or resumed bytes")
            if self.content_range is not None:
                raise ValueError("verified_existing cannot have Content-Range")
        else:
            if any(value is None for value in response_values):
                raise ValueError("network download modes require complete response evidence")
            if self.declared_total_bytes != self.archive_bytes:
                raise ValueError("declared response total differs from archive bytes")
            if self.mode == "downloaded":
                if self.transfer_status_code != 200 or self.resumed_from_bytes != 0:
                    raise ValueError("downloaded mode requires a complete HTTP 200 response")
                if self.network_response_bytes != self.archive_bytes:
                    raise ValueError("downloaded response bytes differ from archive bytes")
                if self.network_response_sha256 != self.archive_sha256:
                    raise ValueError("downloaded response hash differs from archive hash")
                if self.content_range is not None:
                    raise ValueError("downloaded HTTP 200 response cannot have Content-Range")
            elif self.mode == "resumed":
                if self.transfer_status_code != 206 or self.resumed_from_bytes <= 0:
                    raise ValueError("resumed mode requires a partial HTTP 206 response")
                if self.network_response_bytes != self.archive_bytes - self.resumed_from_bytes:
                    raise ValueError("resumed response byte accounting mismatch")
                if self.content_range is None:
                    raise ValueError("resumed HTTP 206 response requires Content-Range")
            elif self.mode == "restarted":
                if self.transfer_status_code != 200 or self.resumed_from_bytes <= 0:
                    raise ValueError("restarted mode requires HTTP 200 after a partial file")
                if self.network_response_bytes != self.archive_bytes:
                    raise ValueError("restarted response bytes differ from archive bytes")
                if self.network_response_sha256 != self.archive_sha256:
                    raise ValueError("restarted response hash differs from archive hash")
                if self.content_range is not None:
                    raise ValueError("restarted HTTP 200 response cannot have Content-Range")
        payload = self.model_dump(mode="json", exclude={"receipt_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.receipt_sha256:
            raise ValueError("SEC log download receipt_sha256 mismatch")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> SECLogDownloadReceipt:
        values = dict(payload)
        values.pop("receipt_sha256", None)
        normalized = cls.model_validate(
            {**values, "receipt_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"receipt_sha256"})
        return cls.model_validate({**normalized, "receipt_sha256": _hash(normalized)})


class _PartialState(_StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    source_url: HttpUrl
    user_agent_sha256: str = Field(pattern=_SHA256_PATTERN)
    first_requested_at: datetime
    declared_total_bytes: int = Field(gt=0)
    source_etag: str | None = Field(default=None, min_length=1, max_length=500)
    source_last_modified: str | None = Field(default=None, min_length=1, max_length=200)
    state_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_state(self, info: ValidationInfo) -> _PartialState:
        _require_aware(self.first_requested_at, "first_requested_at")
        payload = self.model_dump(mode="json", exclude={"state_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.state_sha256:
            raise ValueError("SEC log partial state_sha256 mismatch")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> _PartialState:
        normalized = cls.model_validate(
            {**payload, "state_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"state_sha256"})
        return cls.model_validate({**normalized, "state_sha256": _hash(normalized)})


def fetch_sec_log_inventory(
    *,
    list_page_url: str,
    user_agent: str,
    client: httpx.Client | None = None,
    max_response_bytes: int = 5_000_000,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
) -> tuple[SECLogInventoryLock, bytes]:
    """Retrieve and self-hash one small official annual list without following redirects."""

    user_agent = _validate_user_agent(user_agent)
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be positive")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds cannot be negative")
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=httpx.Timeout(120.0, connect=30.0),
        follow_redirects=False,
        trust_env=True,
    )
    try:
        for attempt in range(max_attempts):
            try:
                content, retrieved_at = _fetch_inventory_bytes(
                    active_client,
                    list_page_url=list_page_url,
                    user_agent=user_agent,
                    max_response_bytes=max_response_bytes,
                )
                break
            except httpx.HTTPError as error:
                if attempt + 1 == max_attempts:
                    raise ValueError("SEC log annual list request failed") from error
                time.sleep(retry_delay_seconds * (attempt + 1))
    finally:
        if owns_client:
            active_client.close()
    return (
        parse_sec_log_inventory(
            content,
            list_page_url=list_page_url,
            retrieved_at=retrieved_at,
        ),
        content,
    )


def _fetch_inventory_bytes(
    client: httpx.Client,
    *,
    list_page_url: str,
    user_agent: str,
    max_response_bytes: int,
) -> tuple[bytes, datetime]:
    retrieved_at = datetime.now(UTC)
    with client.stream(
        "GET",
        list_page_url,
        headers={"User-Agent": user_agent, "Accept-Encoding": "identity"},
    ) as response:
        if response.is_redirect:
            raise ValueError("SEC log annual list redirects are disabled")
        if response.status_code != 200:
            raise ValueError(f"SEC log annual list returned HTTP {response.status_code}")
        _require_identity_encoding(response)
        content_length = _optional_positive_int(
            response.headers.get("Content-Length"), "Content-Length"
        )
        if content_length is not None and content_length > max_response_bytes:
            raise ValueError("SEC log annual list exceeds max_response_bytes")
        chunks: list[bytes] = []
        received = 0
        for chunk in response.iter_bytes():
            received += len(chunk)
            if received > max_response_bytes:
                raise ValueError("SEC log annual list exceeded max_response_bytes")
            chunks.append(chunk)
    content = b"".join(chunks)
    if content_length is not None and len(content) != content_length:
        raise ValueError("SEC log annual list response was incomplete")
    return content, retrieved_at


def download_sec_log_archive(
    partition: SECLogPartition,
    *,
    destination: Path,
    user_agent: str,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
    client: httpx.Client | None = None,
    max_archive_bytes: int = 5_000_000_000,
) -> SECLogDownloadReceipt:
    """Download one archive atomically and resume only against a self-hashed sidecar."""

    user_agent = _validate_user_agent(user_agent)
    user_agent_sha256 = hashlib.sha256(user_agent.encode()).hexdigest()
    if expected_sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ValueError("expected_sha256 must be a lowercase SHA-256")
    if expected_bytes is not None and expected_bytes <= 0:
        raise ValueError("expected_bytes must be positive")
    if max_archive_bytes <= 0:
        raise ValueError("max_archive_bytes must be positive")
    destination = destination.expanduser().resolve()
    expected_name = f"log{partition.partition_date:%Y%m%d}.zip"
    if destination.name != expected_name:
        raise ValueError("SEC log archive destination name must match its partition")
    destination.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    if destination.is_file():
        archive_bytes = destination.stat().st_size
        archive_sha256 = _file_sha256(destination)
        _check_expected_archive(
            archive_bytes,
            archive_sha256,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
        )
        return _download_receipt(
            partition=partition,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            mode="verified_existing",
            status_code=None,
            resumed_from_bytes=0,
            network_response_bytes=0,
            network_response_sha256=None,
            content_range=None,
            declared_total_bytes=None,
            source_etag=None,
            source_last_modified=None,
            archive_bytes=archive_bytes,
            archive_sha256=archive_sha256,
            user_agent_sha256=user_agent_sha256,
        )
    partial = destination.with_name(f".{destination.name}.part")
    state_path = destination.with_name(f".{destination.name}.part.json")
    restart = destination.with_name(f".{destination.name}.restart")
    if partial.exists() != state_path.exists():
        raise ValueError(
            "SEC log partial archive and sidecar must either both exist or both be absent"
        )
    state: _PartialState | None = None
    resumed_from = 0
    if partial.exists():
        state = _load_partial_state(state_path)
        if str(state.source_url) != str(partition.source_url):
            raise ValueError("SEC log partial state source URL mismatch")
        if state.user_agent_sha256 != user_agent_sha256:
            raise ValueError("SEC log partial state user-agent identity mismatch")
        resumed_from = partial.stat().st_size
        if resumed_from <= 0 or resumed_from >= state.declared_total_bytes:
            raise ValueError("SEC log partial archive size is outside its resumable range")
    headers = {"User-Agent": user_agent, "Accept-Encoding": "identity"}
    if state is not None:
        if state.source_etag is not None:
            headers["Range"] = f"bytes={resumed_from}-"
            headers["If-Range"] = state.source_etag
        elif state.source_last_modified is not None:
            headers["Range"] = f"bytes={resumed_from}-"
            headers["If-Range"] = state.source_last_modified
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=httpx.Timeout(300.0, connect=30.0),
        follow_redirects=False,
        trust_env=True,
    )
    try:
        with active_client.stream("GET", str(partition.source_url), headers=headers) as response:
            if response.is_redirect:
                raise ValueError("SEC log archive redirects are disabled")
            status_code = response.status_code
            if state is None and status_code != 200:
                raise ValueError(f"SEC log archive returned HTTP {status_code}")
            if state is not None and status_code not in (200, 206):
                raise ValueError(f"SEC log archive resume returned HTTP {status_code}")
            _require_identity_encoding(response)
            source_etag = response.headers.get("ETag")
            source_last_modified = response.headers.get("Last-Modified")
            content_range = response.headers.get("Content-Range")
            content_length = _required_positive_int(
                response.headers.get("Content-Length"), "Content-Length"
            )
            if status_code == 206:
                if state is None:
                    raise ValueError("unexpected SEC log partial response without local state")
                total_bytes = _validate_content_range(
                    content_range,
                    expected_start=resumed_from,
                    content_length=content_length,
                )
                if total_bytes != state.declared_total_bytes:
                    raise ValueError("SEC log resumed total differs from partial state")
                if state.source_etag is not None and source_etag != state.source_etag:
                    raise ValueError("SEC log resumed ETag differs from partial state")
                if (
                    state.source_last_modified is not None
                    and source_last_modified != state.source_last_modified
                ):
                    raise ValueError("SEC log resumed Last-Modified differs from partial state")
                mode: Literal["downloaded", "resumed", "restarted"] = "resumed"
                target = partial
                file_mode = "ab"
            else:
                total_bytes = content_length
                mode = "downloaded" if state is None else "restarted"
                target = partial if state is None else restart
                file_mode = "wb"
                content_range = None
            if total_bytes > max_archive_bytes:
                raise ValueError("SEC log archive exceeds max_archive_bytes")
            if expected_bytes is not None and total_bytes != expected_bytes:
                raise ValueError("SEC log declared total differs from expected_bytes")
            if state is None:
                state = _PartialState.create(
                    {
                        "schema_version": "1.0.0",
                        "source_url": partition.source_url,
                        "user_agent_sha256": user_agent_sha256,
                        "first_requested_at": started_at,
                        "declared_total_bytes": total_bytes,
                        "source_etag": source_etag,
                        "source_last_modified": source_last_modified,
                    }
                )
                _write_model_atomic(state, state_path)
            segment_digest = hashlib.sha256()
            segment_bytes = 0
            with target.open(file_mode) as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
                    segment_digest.update(chunk)
                    segment_bytes += len(chunk)
                    if segment_bytes > content_length:
                        raise ValueError("SEC log archive response exceeded Content-Length")
                output.flush()
                os.fsync(output.fileno())
            if segment_bytes != content_length:
                raise ValueError("SEC log archive response was incomplete")
            if target.stat().st_size != total_bytes:
                raise ValueError("SEC log completed archive size differs from declared total")
            archive_sha256 = _file_sha256(target)
            _check_expected_archive(
                total_bytes,
                archive_sha256,
                expected_bytes=expected_bytes,
                expected_sha256=expected_sha256,
            )
            os.replace(target, destination)
            if partial.exists():
                partial.unlink()
            if restart.exists():
                restart.unlink()
            state_path.unlink()
    except httpx.HTTPError as error:
        raise ValueError(
            "SEC log archive request failed; resumable partial bytes were kept"
        ) from error
    finally:
        if owns_client:
            active_client.close()
    return _download_receipt(
        partition=partition,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        mode=mode,
        status_code=status_code,
        resumed_from_bytes=resumed_from,
        network_response_bytes=segment_bytes,
        network_response_sha256=segment_digest.hexdigest(),
        content_range=content_range,
        declared_total_bytes=total_bytes,
        source_etag=source_etag,
        source_last_modified=source_last_modified,
        archive_bytes=total_bytes,
        archive_sha256=archive_sha256,
        user_agent_sha256=user_agent_sha256,
    )


def write_sec_log_download_receipt(receipt: SECLogDownloadReceipt, path: Path) -> None:
    _write_model_atomic(receipt, path.expanduser().resolve())


def load_sec_log_download_receipt(path: Path) -> SECLogDownloadReceipt:
    try:
        return SECLogDownloadReceipt.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid SEC log download receipt: {path}") from error


def _download_receipt(
    *,
    partition: SECLogPartition,
    started_at: datetime,
    completed_at: datetime,
    mode: str,
    status_code: int | None,
    resumed_from_bytes: int,
    network_response_bytes: int,
    network_response_sha256: str | None,
    content_range: str | None,
    declared_total_bytes: int | None,
    source_etag: str | None,
    source_last_modified: str | None,
    archive_bytes: int,
    archive_sha256: str,
    user_agent_sha256: str,
) -> SECLogDownloadReceipt:
    return SECLogDownloadReceipt.create(
        {
            "schema_version": "1.0.0",
            "partition_date": partition.partition_date,
            "listed_url": partition.listed_url,
            "source_url": partition.source_url,
            "list_page_url": partition.list_page_url,
            "observation_started_at": started_at,
            "observation_completed_at": completed_at,
            "mode": mode,
            "transfer_status_code": status_code,
            "resumed_from_bytes": resumed_from_bytes,
            "network_response_bytes": network_response_bytes,
            "network_response_sha256": network_response_sha256,
            "content_range": content_range,
            "declared_total_bytes": declared_total_bytes,
            "source_etag": source_etag,
            "source_last_modified": source_last_modified,
            "archive_filename": f"log{partition.partition_date:%Y%m%d}.zip",
            "archive_bytes": archive_bytes,
            "archive_sha256": archive_sha256,
            "user_agent_sha256": user_agent_sha256,
            "claim_boundary": (
                "This receipt records one bounded local observation of an official SEC archive "
                "download, byte-range continuation, full restart, or already-existing file. The "
                "accountable User-Agent is represented only by its SHA-256 so contact information "
                "is not published. A verified_existing receipt proves no network transfer, and a "
                "resumed receipt describes only the final HTTP segment plus the fully hashed final "
                "file. HTTP metadata does not prove completeness or correctness of SEC's source "
                "logs; ZIP CRC, CSV row, Parquet, scale, and semantic claims require separate "
                "receipts. No receipt establishes unique users, deployment, adoption, or impact."
            ),
        }
    )


def _validate_content_range(value: str | None, *, expected_start: int, content_length: int) -> int:
    if value is None:
        raise ValueError("SEC log HTTP 206 response lacks Content-Range")
    match = _CONTENT_RANGE_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("SEC log Content-Range is malformed")
    start = int(match.group("start"))
    end = int(match.group("end"))
    total = int(match.group("total"))
    if start != expected_start or end < start or end >= total:
        raise ValueError("SEC log Content-Range coordinates are inconsistent")
    if end - start + 1 != content_length:
        raise ValueError("SEC log Content-Range length differs from Content-Length")
    return total


def _require_identity_encoding(response: httpx.Response) -> None:
    encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        raise ValueError("SEC log response ignored the requested identity content encoding")


def _load_partial_state(path: Path) -> _PartialState:
    try:
        return _PartialState.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("invalid SEC log partial sidecar") from error


def _write_model_atomic(model: BaseModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    os.close(descriptor)
    try:
        temporary.write_text(
            json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with temporary.open("rb") as output:
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _check_expected_archive(
    archive_bytes: int,
    archive_sha256: str,
    *,
    expected_bytes: int | None,
    expected_sha256: str | None,
) -> None:
    if expected_bytes is not None and archive_bytes != expected_bytes:
        raise ValueError("SEC log archive byte count differs from expected_bytes")
    if expected_sha256 is not None and archive_sha256 != expected_sha256:
        raise ValueError("SEC log archive hash differs from expected_sha256")


def _validate_user_agent(value: str) -> str:
    normalized = value.strip()
    if len(normalized) < 10:
        raise ValueError("SEC requests require a descriptive accountable User-Agent")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("SEC User-Agent cannot contain line breaks")
    return normalized


def _required_positive_int(value: str | None, name: str) -> int:
    parsed = _optional_positive_int(value, name)
    if parsed is None:
        raise ValueError(f"SEC log response lacks {name}")
    return parsed


def _optional_positive_int(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"SEC log response has invalid {name}") from error
    if parsed <= 0:
        raise ValueError(f"SEC log response has non-positive {name}")
    return parsed


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
