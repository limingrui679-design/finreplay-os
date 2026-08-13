"""Archived U.S. Department of Labor weekly initial-claims adapter."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, time
from email.utils import parsedate_to_datetime
from io import BytesIO
from typing import TypedDict
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import HttpUrl, TypeAdapter
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from finreplay.adapters.base import (
    AdapterBatch,
    AdapterMetadata,
    AuthenticationMode,
    FetchReceipt,
    RawArtifact,
    SafeHttpClient,
    SourceSchemaError,
    source_response_sha256,
)
from finreplay.contracts import (
    BitemporalInterval,
    BitemporalRecord,
    EvidenceClass,
    LicenseClass,
    SourceReference,
    TemporalCoverage,
)

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)
_NEW_YORK = ZoneInfo("America/New_York")
_INTEGER = re.compile(r"^[1-9][0-9]{0,2}(?:,[0-9]{3})*$|^[1-9][0-9]*$")
_EMBARGO = re.compile(
    r"TRANSMISSION OF MATERIALS IN THIS RELEASE IS EMBARGOED UNTIL "
    r"8:30 A\.M\. \(Eastern\) "
    r"(?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday), "
    r"(?P<release_date>[A-Z][a-z]+ [0-9]{1,2}, [0-9]{4})"
)
_HEADLINE = re.compile(
    r"In the week ending (?P<week_ending>[A-Z][a-z]+ [0-9]{1,2}), the advance "
    r"figure for seasonally adjusted initial claims was "
    r"(?P<value>[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+), "
    r"(?:an|a) (?P<direction>increase|decrease) of "
    r"(?P<change>[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+) from the previous week's "
    r"(?P<prior_status>revised|unrevised) level"
    r"(?: of (?P<inline_prior>[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+))?\."
)
_PRIOR_REVISION = re.compile(
    r"The previous week's level was revised (?P<direction>up|down) by "
    r"(?P<revision>[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+) from "
    r"(?P<old>[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+) to "
    r"(?P<new>[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)\."
)
_RELEASE_NUMBER = re.compile(r"Release Number: (?P<number>USDL [0-9]{2}-[0-9]{3}-NAT)\b")
_ANNUAL_REVISION_MARKER = (
    "This week's release reflects the annual revision to the weekly unemployment claims "
    "seasonal adjustment factors."
)
_VERIFIED_RELEASES = {
    date(2020, 3, 12): (
        date(2020, 3, 7),
        "eta20200432.pdf",
        "USDL 20-432-NAT",
        False,
    ),
    date(2020, 3, 19): (
        date(2020, 3, 14),
        "20200480.pdf",
        "USDL 20-480-NAT",
        True,
    ),
    date(2020, 3, 26): (
        date(2020, 3, 21),
        "20200510.pdf",
        "USDL 20-510-NAT",
        False,
    ),
}


class _ParsedClaims(TypedDict):
    release_at: datetime
    week_ending: date
    value_persons: int
    prior_level_persons: int
    reported_change_persons: int
    direction: str
    prior_level_status: str
    prior_revision_old_persons: int | None
    prior_revision_new_persons: int | None
    prior_revision_delta_persons: int | None
    release_number: str
    annual_revision_release: bool


class DOLWeeklyClaimsArchiveAdapter:
    """Retrieve one explicitly approved DOL weekly-claims release PDF."""

    availability_rule = (
        "Each archived DOL PDF states an 8:30 a.m. Eastern embargo end on its named Thursday. "
        "FinReplay converts that timestamp with America/New_York and uses the later of the "
        "embargo end or the official PDF Last-Modified timestamp, so the exact archived bytes "
        "are never backdated before their server metadata."
    )
    metadata = AdapterMetadata(
        adapter_id="dol.eta.archived_weekly_initial_claims",
        title="DOL archived weekly seasonally adjusted initial claims",
        publisher="U.S. Department of Labor Employment and Training Administration",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.dol.gov/newsroom/releases/eta"
        ),
        allowed_hosts=("www.dol.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved March 2020 PDFs sequentially; do not "
            "crawl or enumerate the weekly-claims archive."
        ),
        pagination_policy="Each selected release is one complete nine-page PDF.",
        availability_rule=availability_rule,
        revision_behavior=(
            "Each PDF is retained as a versioned release snapshot. A later release's revision "
            "of the prior week is stored only in that later record and never overwrites the "
            "earlier advance estimate."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "DOL says federal materials are generally public domain with attribution and no "
            "implied endorsement, but not all site materials are necessarily federal works and "
            "the DOL seal is protected. Full PDFs remain in local content-addressed storage; "
            "the repository retains minimal facts, attribution, URLs, and hashes only."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified DOL weekly-claims calendar")
        self.http = http
        self.release_date = release_date
        (
            self.week_ending,
            self.filename,
            self.release_number,
            self.annual_revision_release,
        ) = _VERIFIED_RELEASES[release_date]
        self.endpoint = (
            "https://www.dol.gov/sites/dolgov/files/OPA/newsreleases/ui-claims/"
            f"{self.filename}"
        )

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type != "application/pdf":
            raise SourceSchemaError(
                f"unexpected DOL weekly-claims content type: {content_type!r}"
            )
        parsed = self._parse_pdf(content)
        last_modified = self._last_modified(response.headers.get("Last-Modified"))
        available_at = max(parsed["release_at"], last_modified)
        if retrieved_at < available_at:
            raise SourceSchemaError("selected DOL weekly-claims release is not yet knowable")
        digest = source_response_sha256(content)
        source_version = (
            f"DOL-UI:{self.release_date.isoformat()}:{self.release_number.replace(' ', '-')}:"
            f"sha256:{digest[:24]}"
        )
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            vintage_as_of=last_modified,
            redistribution_note=self.metadata.redistribution_note,
        )
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.release_date:%Y%m%d}:"
                "seasonally_adjusted_initial_claims"
            ),
            entity_id="dol_ui_claims:united_states",
            source=source,
            interval=BitemporalInterval(
                valid_from=datetime.combine(self.week_ending, time.min, tzinfo=UTC),
                published_at=parsed["release_at"],
                available_at=available_at,
                ingested_at=retrieved_at,
                availability_rule=self.availability_rule,
                availability_confidence=1.0,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload={
                "release_date": self.release_date.isoformat(),
                "week_ending": self.week_ending.isoformat(),
                "metric": "seasonally_adjusted_initial_claims",
                "value_persons": parsed["value_persons"],
                "prior_level_persons": parsed["prior_level_persons"],
                "reported_change_persons": parsed["reported_change_persons"],
                "reported_direction": parsed["direction"],
                "prior_level_status": parsed["prior_level_status"],
                "prior_level_revision_old_persons": parsed[
                    "prior_revision_old_persons"
                ],
                "prior_level_revision_new_persons": parsed[
                    "prior_revision_new_persons"
                ],
                "prior_level_revision_delta_persons": parsed[
                    "prior_revision_delta_persons"
                ],
                "release_number": parsed["release_number"],
                "release_time_eastern": "08:30:00",
                "pdf_last_modified_at": last_modified.isoformat(),
                "availability_method": "max_explicit_embargo_end_and_pdf_last_modified",
                "annual_revision_release": parsed["annual_revision_release"],
                "snapshot_semantics": "advance value reported in this archived release",
                "arithmetic_verified": True,
                "unit": "Persons",
            },
        )
        warnings = (
            "The exact archived PDF becomes eligible at the later of its stated embargo end "
            "and official Last-Modified timestamp.",
            "The March 19 release applies annual seasonal-factor revisions, so adjacent release "
            "snapshots are not treated as a calibrated stationary forecasting sample.",
            "A later release's revised prior-week value remains in the later snapshot and does "
            "not overwrite the earlier advance estimate.",
            "DOL describes weekly administrative claims as volatile and subject to revision.",
            "Only the explicitly verified three-date March 2020 calendar is supported.",
            "Full archived PDFs remain local download evidence.",
        )
        receipt = FetchReceipt(
            adapter_id=self.metadata.adapter_id,
            request_url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
            retrieved_at=retrieved_at,
            status_code=response.status_code,
            content_type=content_type,
            response_sha256=digest,
            response_bytes=len(content),
            record_count=1,
            source_version=source_version,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            historical_replay_eligible=True,
            warnings=warnings,
        )
        artifact = RawArtifact(sha256=digest, content_type=content_type, content=content)
        return AdapterBatch(records=(record,), receipts=(receipt,), artifacts=(artifact,))

    def _parse_pdf(self, content: bytes) -> _ParsedClaims:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("DOL weekly-claims release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 9:
                raise SourceSchemaError(
                    "DOL weekly-claims release must contain exactly nine pages"
                )
            first_page = reader.pages[0].extract_text()
            final_page = reader.pages[8].extract_text()
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("DOL weekly-claims PDF could not be parsed") from error
        if not isinstance(first_page, str) or not first_page.strip():
            raise SourceSchemaError("DOL weekly-claims first page has no extractable text")
        if not isinstance(final_page, str) or not final_page.strip():
            raise SourceSchemaError("DOL weekly-claims technical-notes page has no text")
        first = _normalize(first_page)
        final = _normalize(final_page)
        required_first_markers = (
            "News Release",
            "UNEMPLOYMENT INSURANCE WEEKLY CLAIMS",
            "SEASONALLY ADJUSTED DATA",
        )
        required_final_markers = (
            "TECHNICAL NOTES",
            "ETA 538, Advance Weekly Initial and Continued Claims Report",
            "The following week initial claims and continued claims are revised",
            "U.S. Department of Labor Employment and Training Administration",
        )
        if any(marker not in first for marker in required_first_markers):
            raise SourceSchemaError("DOL weekly-claims first-page identity does not match")
        if any(marker not in final for marker in required_final_markers):
            raise SourceSchemaError("DOL weekly-claims technical notes do not match")
        embargo_matches = list(_EMBARGO.finditer(first))
        headline_matches = list(_HEADLINE.finditer(first))
        number_matches = list(_RELEASE_NUMBER.finditer(final))
        if len(embargo_matches) != 1:
            raise SourceSchemaError("DOL release must contain exactly one embargo timestamp")
        if len(headline_matches) != 1:
            raise SourceSchemaError("DOL release must contain exactly one initial-claims headline")
        if len(number_matches) != 1:
            raise SourceSchemaError("DOL release must contain exactly one release number")
        embargo = embargo_matches[0]
        headline = headline_matches[0]
        release_date = _display_date(embargo.group("release_date"), "release date")
        if release_date != self.release_date:
            raise SourceSchemaError("DOL PDF release date does not match requested date")
        if embargo.group("weekday") != calendar.day_name[release_date.weekday()]:
            raise SourceSchemaError("DOL embargo weekday does not match release date")
        if number_matches[0].group("number") != self.release_number:
            raise SourceSchemaError("DOL PDF release number does not match verified calendar")
        week_ending = _display_date(
            f"{headline.group('week_ending')}, {release_date.year}",
            "week ending",
        )
        if week_ending != self.week_ending:
            raise SourceSchemaError("DOL headline week ending does not match verified calendar")
        value = _positive_integer(headline.group("value"), "headline value")
        change = _positive_integer(headline.group("change"), "headline change")
        inline_prior = headline.group("inline_prior")
        revision_old: int | None = None
        revision_new: int | None = None
        revision_delta: int | None = None
        if inline_prior is not None:
            prior = _positive_integer(inline_prior, "inline prior level")
        else:
            boundary = first.find("The 4-week moving average", headline.end())
            if boundary < 0:
                raise SourceSchemaError("DOL initial-claims paragraph boundary is missing")
            revision_matches = list(_PRIOR_REVISION.finditer(first[headline.end() : boundary]))
            if len(revision_matches) != 1:
                raise SourceSchemaError(
                    "DOL release must identify exactly one revised prior claims level"
                )
            revision = revision_matches[0]
            revision_old = _positive_integer(revision.group("old"), "old prior level")
            revision_new = _positive_integer(revision.group("new"), "new prior level")
            revision_amount = _positive_integer(
                revision.group("revision"), "prior-level revision"
            )
            revision_delta = (
                revision_amount
                if revision.group("direction") == "up"
                else -revision_amount
            )
            if revision_old + revision_delta != revision_new:
                raise SourceSchemaError("DOL prior-level revision does not reconcile")
            prior = revision_new
        signed_change = change if headline.group("direction") == "increase" else -change
        if prior + signed_change != value:
            raise SourceSchemaError("DOL initial-claims headline arithmetic does not reconcile")
        annual_marker_present = _ANNUAL_REVISION_MARKER in first
        if annual_marker_present != self.annual_revision_release:
            raise SourceSchemaError("DOL annual-revision marker does not match verified calendar")
        release_at = datetime.combine(release_date, time(8, 30), tzinfo=_NEW_YORK).astimezone(
            UTC
        )
        return {
            "release_at": release_at,
            "week_ending": week_ending,
            "value_persons": value,
            "prior_level_persons": prior,
            "reported_change_persons": signed_change,
            "direction": headline.group("direction"),
            "prior_level_status": headline.group("prior_status"),
            "prior_revision_old_persons": revision_old,
            "prior_revision_new_persons": revision_new,
            "prior_revision_delta_persons": revision_delta,
            "release_number": number_matches[0].group("number"),
            "annual_revision_release": annual_marker_present,
        }

    def _last_modified(self, raw_value: str | None) -> datetime:
        if raw_value is None:
            raise SourceSchemaError("DOL weekly-claims response lacks Last-Modified")
        try:
            parsed = parsedate_to_datetime(raw_value)
        except (TypeError, ValueError) as error:
            raise SourceSchemaError("DOL weekly-claims Last-Modified is invalid") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise SourceSchemaError("DOL weekly-claims Last-Modified lacks a timezone")
        normalized = parsed.astimezone(UTC)
        if normalized.date() != self.release_date:
            raise SourceSchemaError(
                "DOL weekly-claims Last-Modified falls outside the verified release date"
            )
        return normalized

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        expected_path = f"/sites/dolgov/files/OPA/newsreleases/ui-claims/{self.filename}"
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("DOL weekly-claims response URL does not match request")


def _normalize(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def _display_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%B %d, %Y").replace(tzinfo=UTC).date()
    except ValueError as error:
        raise SourceSchemaError(f"DOL {label} is invalid") from error


def _positive_integer(value: str, label: str) -> int:
    if _INTEGER.fullmatch(value) is None:
        raise SourceSchemaError(f"DOL {label} must be a positive integer")
    parsed = int(value.replace(",", ""))
    if parsed <= 0 or parsed > 100_000_000:
        raise SourceSchemaError(f"DOL {label} is outside the supported range")
    return parsed
