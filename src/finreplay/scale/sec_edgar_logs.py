"""Fail-closed inventory and archive handling for official SEC EDGAR access logs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationInfo, model_validator

SEC_EDGAR_LOG_LANDING_URL = (
    "https://www.sec.gov/data-research/sec-markets-data/edgar-log-file-data-sets"
)
SEC_EDGAR_LOG_HEADER_2003_2017 = (
    "ip",
    "date",
    "time",
    "zone",
    "cik",
    "accession",
    "extention",
    "code",
    "size",
    "idx",
    "norefer",
    "noagent",
    "find",
    "crawler",
    "browser",
)

_LIST_URL_PATTERN = re.compile(r"^https://www\.sec\.gov/files/edgar(?P<year>20\d{2})\.html$")
_ARCHIVE_PATH_PATTERN = re.compile(
    r"^/dera/data/Public-EDGAR-log-file-data/(?P<year>20\d{2})/"
    r"Qtr(?P<quarter>[1-4])/log(?P<day>20\d{6})\.zip$"
)
_ARCHIVE_LABEL_PATTERN = re.compile(r"^log(?P<day>20\d{6})\.zip$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SECLogPartition(_StrictModel):
    """One non-guessed daily archive link from an official SEC year page."""

    partition_date: date
    source_url: HttpUrl
    list_page_url: HttpUrl

    @model_validator(mode="after")
    def validate_official_partition(self) -> SECLogPartition:
        list_match = _LIST_URL_PATTERN.fullmatch(str(self.list_page_url))
        if list_match is None:
            raise ValueError("SEC log list page must be an exact official annual URL")
        parsed = urlparse(str(self.source_url))
        if parsed.scheme != "https" or parsed.hostname != "www.sec.gov":
            raise ValueError("SEC log archive must use HTTPS on www.sec.gov")
        if parsed.query or parsed.fragment or parsed.params:
            raise ValueError("SEC log archive URL cannot contain extra components")
        archive_match = _ARCHIVE_PATH_PATTERN.fullmatch(parsed.path)
        if archive_match is None:
            raise ValueError("SEC log archive path does not match the official daily layout")
        archive_day = _parse_compact_date(archive_match.group("day"))
        expected_quarter = (archive_day.month - 1) // 3 + 1
        if archive_day != self.partition_date:
            raise ValueError("SEC log partition date does not match its archive name")
        if int(archive_match.group("year")) != archive_day.year:
            raise ValueError("SEC log archive year does not match its date")
        if int(archive_match.group("quarter")) != expected_quarter:
            raise ValueError("SEC log archive quarter does not match its date")
        if int(list_match.group("year")) != archive_day.year:
            raise ValueError("SEC log annual list does not match the archive year")
        return self


class SECLogInventoryLock(_StrictModel):
    """Self-hashed set of daily links observed on one official SEC annual page."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    source_landing_url: HttpUrl
    list_page_url: HttpUrl
    list_page_sha256: str = Field(pattern=_SHA256_PATTERN)
    retrieved_at: datetime
    partitions: tuple[SECLogPartition, ...] = Field(min_length=1, max_length=366)
    claim_boundary: str = Field(min_length=100, max_length=3_000)
    lock_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> SECLogInventoryLock:
        if str(self.source_landing_url) != SEC_EDGAR_LOG_LANDING_URL:
            raise ValueError("SEC log inventory landing URL mismatch")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("SEC log inventory retrieval time must be timezone-aware")
        partition_dates = tuple(item.partition_date for item in self.partitions)
        source_urls = tuple(str(item.source_url) for item in self.partitions)
        if partition_dates != tuple(sorted(partition_dates)):
            raise ValueError("SEC log partitions must be sorted by date")
        if len(set(partition_dates)) != len(partition_dates):
            raise ValueError("SEC log partition dates must be unique")
        if len(set(source_urls)) != len(source_urls):
            raise ValueError("SEC log partition URLs must be unique")
        if any(item.list_page_url != self.list_page_url for item in self.partitions):
            raise ValueError("every SEC log partition must bind the same annual list page")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("SEC log inventory lock_sha256 mismatch")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> SECLogInventoryLock:
        """Normalize, validate, and self-hash an annual inventory."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


@dataclass(frozen=True, slots=True)
class SECLogExtractedCSV:
    """Measured bytes produced by a complete, CRC-checked official extraction."""

    partition_date: date
    csv_path: Path
    member_name: str
    zip_bytes: int
    zip_sha256: str
    csv_bytes: int
    csv_sha256: str
    csv_crc32: str
    physical_line_count: int
    header: tuple[str, ...]
    archive_member_names: tuple[str, ...]
    readme_bytes: int | None
    readme_sha256: str | None
    readme_crc32: str | None


@dataclass(frozen=True, slots=True)
class _InventoryLink:
    label: str
    href: str


class _InventoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[_InventoryLink] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        values = dict(attrs)
        href = values.get("href")
        if href is not None:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        self.links.append(_InventoryLink(label="".join(self._text).strip(), href=self._href))
        self._href = None
        self._text = []


def parse_sec_log_inventory(
    content: bytes,
    *,
    list_page_url: str,
    retrieved_at: datetime,
) -> SECLogInventoryLock:
    """Parse only explicitly listed daily ZIP links; never synthesize missing dates."""

    list_match = _LIST_URL_PATTERN.fullmatch(list_page_url)
    if list_match is None:
        raise ValueError("SEC log list page must be an exact official annual URL")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("SEC log annual list must be UTF-8") from error
    parser = _InventoryParser()
    parser.feed(text)
    partitions: list[SECLogPartition] = []
    for link in parser.links:
        label_match = _ARCHIVE_LABEL_PATTERN.fullmatch(link.label)
        href_name = Path(urlparse(link.href).path).name
        href_match = _ARCHIVE_LABEL_PATTERN.fullmatch(href_name)
        if label_match is None and href_match is None:
            continue
        if label_match is None or href_match is None:
            raise ValueError("SEC log ZIP link label and href must both name a daily archive")
        if label_match.group("day") != href_match.group("day"):
            raise ValueError("SEC log ZIP link label disagrees with its href")
        day = _parse_compact_date(label_match.group("day"))
        partitions.append(
            SECLogPartition.model_validate(
                {
                    "partition_date": day,
                    "source_url": urljoin(list_page_url, link.href),
                    "list_page_url": list_page_url,
                }
            )
        )
    if not partitions:
        raise ValueError("SEC log annual list contains no valid daily archives")
    ordered = tuple(sorted(partitions, key=lambda item: item.partition_date))
    return SECLogInventoryLock.create(
        {
            "schema_version": "1.0.0",
            "source_landing_url": SEC_EDGAR_LOG_LANDING_URL,
            "list_page_url": list_page_url,
            "list_page_sha256": hashlib.sha256(content).hexdigest(),
            "retrieved_at": retrieved_at,
            "partitions": [item.model_dump(mode="json") for item in ordered],
            "claim_boundary": (
                "This lock proves which daily SEC EDGAR access-log ZIP links appeared on one "
                "retrieved official annual list page. It does not prove that every calendar day "
                "exists, that linked bytes are unchanged, that logs capture all SEC.gov traffic, "
                "that each row is accurate, or that a request represents a unique human, filing, "
                "investment decision, customer, deployment, or real-world impact. Archive bytes, "
                "CSV rows, and derived Parquet partitions require separate measured receipts."
            ),
        }
    )


def load_sec_log_inventory_lock(path: Path) -> SECLogInventoryLock:
    try:
        return SECLogInventoryLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid SEC log inventory lock: {path}") from error


def extract_sec_log_archive(
    archive_path: Path,
    *,
    partition_date: date,
    destination: Path,
    max_uncompressed_bytes: int = 50_000_000_000,
    max_readme_bytes: int = 1_000_000,
) -> SECLogExtractedCSV:
    """Extract one exact daily CSV atomically while measuring all source bytes."""

    if max_uncompressed_bytes <= 0:
        raise ValueError("max_uncompressed_bytes must be positive")
    if max_readme_bytes <= 0:
        raise ValueError("max_readme_bytes must be positive")
    archive_path = archive_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not archive_path.is_file():
        raise ValueError(f"SEC log archive does not exist: {archive_path}")
    expected_member = f"log{partition_date:%Y%m%d}.csv"
    zip_sha256 = _file_sha256(archive_path)
    zip_bytes = archive_path.stat().st_size
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            member_names = tuple(member.filename for member in members)
            if len(set(member_names)) != len(member_names):
                raise ValueError("SEC log ZIP member names must be unique")
            allowed_names = {expected_member, "README.txt"}
            if set(member_names) not in ({expected_member}, allowed_names):
                raise ValueError(
                    "SEC log ZIP members must be the dated CSV and optional README.txt"
                )
            if any(member.flag_bits & 0x1 for member in members):
                raise ValueError("encrypted SEC log ZIP members are not supported")
            if any(member.is_dir() for member in members):
                raise ValueError("SEC log ZIP members must be files")
            info = archive.getinfo(expected_member)
            if info.file_size <= 0 or info.file_size > max_uncompressed_bytes:
                raise ValueError("SEC log ZIP uncompressed size exceeds the configured boundary")
            readme_info = next(
                (member for member in members if member.filename == "README.txt"), None
            )
            readme_bytes: int | None = None
            readme_sha256: str | None = None
            readme_crc32: str | None = None
            if readme_info is not None:
                if readme_info.file_size <= 0 or readme_info.file_size > max_readme_bytes:
                    raise ValueError("SEC log README size exceeds the configured boundary")
                readme_content = archive.read(readme_info)
                if len(readme_content) != readme_info.file_size:
                    raise ValueError("SEC log README byte count does not match ZIP metadata")
                readme_bytes = len(readme_content)
                readme_sha256 = hashlib.sha256(readme_content).hexdigest()
                readme_crc32 = f"{readme_info.CRC:08x}"
            digest = hashlib.sha256()
            csv_bytes = 0
            newline_count = 0
            last_byte = b""
            header_buffer = bytearray()
            header_complete = False
            with archive.open(info, "r") as source, temporary.open("wb") as output:
                while chunk := source.read(8 * 1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    csv_bytes += len(chunk)
                    newline_count += chunk.count(b"\n")
                    last_byte = chunk[-1:]
                    if not header_complete:
                        newline = chunk.find(b"\n")
                        if newline == -1:
                            header_buffer.extend(chunk)
                        else:
                            header_buffer.extend(chunk[:newline])
                            header_complete = True
                        if len(header_buffer) > 4_096:
                            raise ValueError("SEC log CSV header exceeds 4096 bytes")
                output.flush()
                os.fsync(output.fileno())
            if csv_bytes != info.file_size:
                raise ValueError("SEC log CSV byte count does not match ZIP metadata")
            if not header_complete:
                raise ValueError("SEC log CSV has no complete header line")
            try:
                header = tuple(header_buffer.rstrip(b"\r").decode("ascii").split(","))
            except UnicodeDecodeError as error:
                raise ValueError("SEC log CSV header must be ASCII") from error
            if header != SEC_EDGAR_LOG_HEADER_2003_2017:
                raise ValueError("SEC log CSV header does not match the 2003-2017 contract")
            physical_line_count = newline_count + (1 if last_byte != b"\n" else 0)
            if physical_line_count < 2:
                raise ValueError("SEC log CSV must contain at least one data row")
            os.replace(temporary, destination)
        return SECLogExtractedCSV(
            partition_date=partition_date,
            csv_path=destination,
            member_name=expected_member,
            zip_bytes=zip_bytes,
            zip_sha256=zip_sha256,
            csv_bytes=csv_bytes,
            csv_sha256=digest.hexdigest(),
            csv_crc32=f"{info.CRC:08x}",
            physical_line_count=physical_line_count,
            header=header,
            archive_member_names=tuple(sorted(member_names)),
            readme_bytes=readme_bytes,
            readme_sha256=readme_sha256,
            readme_crc32=readme_crc32,
        )
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise ValueError(f"invalid SEC log ZIP: {archive_path}") from error
    finally:
        if temporary.exists():
            temporary.unlink()


def _parse_compact_date(value: str) -> date:
    try:
        return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
    except ValueError as error:
        raise ValueError(f"invalid SEC log partition date: {value}") from error


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
