"""Shared contracts and safe HTTP transport for official public data."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from finreplay.contracts import (
    BitemporalRecord,
    LicenseClass,
    TemporalCoverage,
)


class AdapterError(RuntimeError):
    """Base error for a source-specific retrieval or parsing failure."""


class ResponseLimitError(AdapterError):
    """Raised before an upstream response can exhaust local resources."""


class SourceSchemaError(AdapterError):
    """Raised when a live source no longer satisfies the documented contract."""


class AuthenticationMode(StrEnum):
    NONE = "none"
    OPTIONAL_KEY = "optional_key"
    REQUIRED_KEY = "required_key"
    BYO_CREDENTIALS = "bring_your_own_credentials"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AdapterMetadata(_StrictModel):
    """Machine-readable source, revision, license, and pagination behavior."""

    adapter_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    title: str = Field(min_length=3, max_length=200)
    publisher: str = Field(min_length=2, max_length=200)
    documentation_url: HttpUrl
    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    authentication: AuthenticationMode
    rate_limit_policy: str = Field(min_length=3, max_length=1000)
    pagination_policy: str = Field(min_length=3, max_length=1000)
    availability_rule: str = Field(min_length=3, max_length=1000)
    revision_behavior: str = Field(min_length=3, max_length=1000)
    temporal_coverage: TemporalCoverage
    license_class: LicenseClass
    redistribution_note: str = Field(min_length=3, max_length=1000)


class FetchReceipt(_StrictModel):
    """Content-addressed evidence for exactly one upstream HTTP response."""

    adapter_id: str
    request_url: HttpUrl
    retrieved_at: datetime
    status_code: int = Field(ge=100, le=599)
    content_type: str = Field(min_length=1, max_length=200)
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_bytes: int = Field(ge=0)
    record_count: int = Field(ge=0)
    source_version: str = Field(min_length=1, max_length=300)
    temporal_coverage: TemporalCoverage
    historical_replay_eligible: bool
    warnings: tuple[str, ...]

    @model_validator(mode="after")
    def validate_temporal_claim(self) -> FetchReceipt:
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if self.temporal_coverage is TemporalCoverage.LATEST_ONLY:
            if self.historical_replay_eligible:
                raise ValueError("latest_only receipts cannot be historical-replay eligible")
            if not self.warnings:
                raise ValueError("latest_only receipts require an explicit warning")
        return self


class RawArtifact(_StrictModel):
    """Raw response held in memory until written to content-addressed local storage."""

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str
    content: bytes

    @model_validator(mode="after")
    def hash_matches_content(self) -> RawArtifact:
        actual = hashlib.sha256(self.content).hexdigest()
        if actual != self.sha256:
            raise ValueError(f"raw artifact hash mismatch: {actual} != {self.sha256}")
        return self


class AdapterBatch(_StrictModel):
    """Validated records with every response needed to reproduce their provenance."""

    records: tuple[BitemporalRecord, ...]
    receipts: tuple[FetchReceipt, ...] = Field(min_length=1)
    artifacts: tuple[RawArtifact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reconcile_counts_and_hashes(self) -> AdapterBatch:
        if sum(receipt.record_count for receipt in self.receipts) != len(self.records):
            raise ValueError("receipt record counts do not equal parsed record count")
        receipt_hashes = {receipt.response_sha256 for receipt in self.receipts}
        artifact_hashes = {artifact.sha256 for artifact in self.artifacts}
        if receipt_hashes != artifact_hashes:
            raise ValueError("receipt and raw-artifact hash sets differ")
        return self


@dataclass(frozen=True, slots=True)
class HttpResponseSnapshot:
    """Decoded response bytes detached from transport content-encoding state."""

    status_code: int
    headers: Mapping[str, str]
    request_url: str
    content: bytes

    def json(self) -> Any:
        return json.loads(self.content)


class SafeHttpClient:
    """HTTPS-only bounded transport with no implicit redirect to an unapproved host."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 50_000_000,
        trust_environment: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("a non-empty accountable user agent is required")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self.max_response_bytes = max_response_bytes
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            trust_env=trust_environment,
        )

    def __enter__(self) -> SafeHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
        params: Mapping[str, str | int] | None = None,
    ) -> tuple[HttpResponseSnapshot, bytes, datetime]:
        self._validate_url(url, allowed_hosts)
        retrieved_at = datetime.now(UTC)
        with self._client.stream("GET", url, params=params) as response:
            if response.is_redirect:
                raise AdapterError(
                    f"redirects are disabled for source integrity: {response.status_code}"
                )
            if response.status_code != 200:
                raise AdapterError(f"upstream returned HTTP {response.status_code}")
            detached, content = self._read_successful_response(
                response,
                canonical_request_url=str(response.request.url),
            )
        return detached, content, retrieved_at

    def get_same_host_signed_redirect(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
        expected_redirect_path: str,
    ) -> tuple[HttpResponseSnapshot, bytes, datetime]:
        """Follow one narrowly validated CloudFront-style signed redirect on the same host."""

        self._validate_url(url, allowed_hosts)
        initial = urlparse(url)
        if initial.query or initial.fragment or initial.params:
            raise AdapterError("signed-redirect canonical URL must not contain extra components")
        if not expected_redirect_path.startswith("/"):
            raise ValueError("expected_redirect_path must be absolute")
        retrieved_at = datetime.now(UTC)
        with self._client.stream("GET", url) as response:
            if response.status_code != 302:
                if response.is_redirect:
                    raise AdapterError(
                        f"unsupported signed redirect status: {response.status_code}"
                    )
                raise AdapterError(
                    f"signed-redirect source returned HTTP {response.status_code}"
                )
            location = response.headers.get("Location")
        if location is None:
            raise AdapterError("signed redirect lacks Location")
        self._validate_signed_redirect(
            location,
            allowed_hosts=allowed_hosts,
            expected_host=initial.hostname,
            expected_path=expected_redirect_path,
        )
        with self._client.stream("GET", location) as response:
            if response.is_redirect:
                raise AdapterError("signed source attempted more than one redirect")
            if response.status_code != 200:
                raise AdapterError(
                    f"signed redirect target returned HTTP {response.status_code}"
                )
            detached, content = self._read_successful_response(
                response,
                canonical_request_url=url,
            )
        return detached, content, retrieved_at

    def _read_successful_response(
        self,
        response: httpx.Response,
        *,
        canonical_request_url: str,
    ) -> tuple[HttpResponseSnapshot, bytes]:
        content_length = response.headers.get("Content-Length")
        if content_length is not None and int(content_length) > self.max_response_bytes:
            raise ResponseLimitError(
                f"declared response size {content_length} exceeds {self.max_response_bytes}"
            )
        chunks: list[bytes] = []
        received = 0
        for chunk in response.iter_bytes():
            received += len(chunk)
            if received > self.max_response_bytes:
                raise ResponseLimitError(
                    f"response exceeded {self.max_response_bytes} bytes while streaming"
                )
            chunks.append(chunk)
        content = b"".join(chunks)
        # iter_bytes() has already decoded content encodings. A detached httpx.Response
        # retaining Content-Encoding would decode twice, so preserve metadata in a neutral
        # immutable snapshot instead.
        detached = HttpResponseSnapshot(
            status_code=response.status_code,
            headers=httpx.Headers(response.headers),
            request_url=canonical_request_url,
            content=content,
        )
        return detached, content

    @classmethod
    def _validate_signed_redirect(
        cls,
        location: str,
        *,
        allowed_hosts: tuple[str, ...],
        expected_host: str | None,
        expected_path: str,
    ) -> None:
        cls._validate_url(location, allowed_hosts)
        parsed = urlparse(location)
        if (
            parsed.hostname != expected_host
            or parsed.path != expected_path
            or parsed.params
            or parsed.fragment
        ):
            raise AdapterError("signed redirect target does not match the approved endpoint")
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        required = {"Policy", "Signature", "Key-Pair-Id"}
        if set(query) != required or any(
            len(values) != 1 or not values[0] for values in query.values()
        ):
            raise AdapterError("signed redirect query does not match the approved schema")

    @staticmethod
    def _validate_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise AdapterError("official data transport must use HTTPS")
        if parsed.hostname not in allowed_hosts:
            raise AdapterError(f"host {parsed.hostname!r} is not in the adapter allowlist")
        if parsed.username is not None or parsed.password is not None:
            raise AdapterError("credentials must not be embedded in source URLs")


def source_response_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def require_json_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourceSchemaError(f"{context} must be a JSON object")
    return value
