"""Final historical SOFR rate adapter with a conservative revision cutoff."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from pydantic import HttpUrl, TypeAdapter

from finreplay.adapters.base import (
    AdapterBatch,
    AdapterMetadata,
    AuthenticationMode,
    FetchReceipt,
    RawArtifact,
    SafeHttpClient,
    SourceSchemaError,
    require_json_object,
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
_VERIFIED_PUBLICATION_DATES = {
    date(2019, 9, 13): date(2019, 9, 16),
    date(2019, 9, 16): date(2019, 9, 17),
    date(2019, 9, 17): date(2019, 9, 18),
}


class NYFedSOFRHistoricalAdapter:
    """Retrieve one final historical SOFR rate from the official Markets API."""

    availability_rule = (
        "The New York Fed publishes SOFR at approximately 8:00 a.m. ET on the next business day "
        "and permits qualifying same-day revisions at approximately 2:30 p.m. ET. For the "
        "explicitly verified September 2019 calendar, FinReplay permits the final rate only from "
        "3:00 p.m. America/New_York on its publication business day."
    )
    metadata = AdapterMetadata(
        adapter_id="nyfed.sofr.final_historical_rate",
        title="New York Fed final historical Secured Overnight Financing Rate",
        publisher="Federal Reserve Bank of New York",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.newyorkfed.org/markets/reference-rates/additional-information-about-"
            "reference-rates"
        ),
        allowed_hosts=("markets.newyorkfed.org",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only explicitly approved effective dates sequentially; do not crawl or "
            "enumerate the historical endpoint."
        ),
        pagination_policy=(
            "Each request fixes one SOFR effective date and must return exactly one rate row."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "The New York Fed may revise SOFR at approximately 2:30 p.m. on its publication day "
            "when the rate change exceeds one basis point. The adapter waits until 3:00 p.m., "
            "requires an empty revision indicator for the selected rows, and normalizes only the "
            "final rate. Lagged ancillary summary statistics remain outside the fact payload."
        ),
        temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
        license_class=LicenseClass.REVIEW_REQUIRED,
        redistribution_note=(
            "Apply the current New York Fed Terms of Use, source identification, attribution, "
            "same-permissions, modification, and non-endorsement conditions. Repository scenarios "
            "retain only the final rate, provenance, and hashes; raw responses remain local."
        ),
    )

    endpoint = "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"

    def __init__(self, http: SafeHttpClient, *, effective_date: date) -> None:
        if effective_date not in _VERIFIED_PUBLICATION_DATES:
            raise ValueError("effective date is not in the verified SOFR publication calendar")
        self.http = http
        self.effective_date = effective_date
        self.publication_date = _VERIFIED_PUBLICATION_DATES[effective_date]

    def fetch(self) -> AdapterBatch:
        value = self.effective_date.isoformat()
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
            params={"startDate": value, "endDate": value, "type": "rate"},
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"application/json", "text/json"}:
            raise SourceSchemaError(f"unexpected SOFR history content type: {content_type!r}")
        try:
            decoded = content.decode("utf-8")
            root = require_json_object(
                json.loads(decoded, parse_float=Decimal, parse_int=Decimal),
                "SOFR history response",
            )
        except UnicodeDecodeError as error:
            raise SourceSchemaError("SOFR history response is not valid UTF-8") from error
        except (InvalidOperation, TypeError, ValueError) as error:
            raise SourceSchemaError("SOFR history response is not valid JSON") from error
        if set(root) != {"refRates"} or not isinstance(root["refRates"], list):
            raise SourceSchemaError("SOFR history response must contain only a refRates list")
        if len(root["refRates"]) != 1:
            raise SourceSchemaError("SOFR history response must contain exactly one rate row")
        row = require_json_object(root["refRates"][0], "SOFR history rate row")
        required = {
            "effectiveDate",
            "type",
            "percentRate",
            "percentPercentile1",
            "percentPercentile25",
            "percentPercentile75",
            "percentPercentile99",
            "revisionIndicator",
        }
        if set(row) != required:
            raise SourceSchemaError("SOFR history rate row fields do not match the approved schema")
        if row["effectiveDate"] != value or row["type"] != "SOFR":
            raise SourceSchemaError("SOFR history rate identity does not match the request")
        if row["revisionIndicator"] != "":
            raise SourceSchemaError("SOFR history selected rate has a non-empty revision indicator")
        rate = _basis_points(row["percentRate"], "percentRate")
        percentiles = tuple(
            _basis_points(row[field], field)
            for field in (
                "percentPercentile1",
                "percentPercentile25",
                "percentPercentile75",
                "percentPercentile99",
            )
        )
        if not percentiles[0] <= percentiles[1] <= rate <= percentiles[2] <= percentiles[3]:
            raise SourceSchemaError("SOFR rate and percentile order is inconsistent")
        digest = source_response_sha256(content)
        available_at = datetime.combine(
            self.publication_date,
            time(hour=15),
            tzinfo=_NEW_YORK,
        ).astimezone(UTC)
        if retrieved_at < available_at:
            raise SourceSchemaError("selected SOFR rate is not yet conservatively final")
        source_version = (
            f"SOFR:effective:{value}:final-at:{available_at.isoformat()}:"
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
            temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
            vintage_as_of=available_at,
            redistribution_note=self.metadata.redistribution_note,
        )
        record = BitemporalRecord(
            record_id=f"{self.metadata.adapter_id}:{value}",
            entity_id="nyfed_reference_rate:SOFR",
            source=source,
            interval=BitemporalInterval(
                valid_from=datetime.combine(self.effective_date, time.min, tzinfo=UTC),
                published_at=available_at,
                available_at=available_at,
                ingested_at=retrieved_at,
                availability_rule=self.availability_rule,
                availability_confidence=1.0,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload={
                "effective_date": value,
                "publication_business_date": self.publication_date.isoformat(),
                "rate_type": "SOFR",
                "reported_value_percent": f"{Decimal(rate) / 100:.2f}",
                "value_basis_points": rate,
                "unit": "Basis Points",
                "revision_indicator": "",
                "availability_method": (
                    "official_next_business_day_after_revision_window_1500_america_new_york"
                ),
                "ancillary_statistics_normalized": False,
            },
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
            temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
            historical_replay_eligible=True,
            warnings=(
                "SOFR finality is conservatively set to 3:00 p.m. ET after the same-day revision "
                "window, not the approximate 8:00 a.m. initial publication.",
                "The approved calendar covers only three explicitly selected September 2019 "
                "effective dates.",
                "Ancillary percentile statistics may have lagged updates and are validated but "
                "excluded from the normalized historical fact.",
                "Raw New York Fed responses remain local and reuse requires current terms and "
                "non-endorsement notices.",
            ),
        )
        return AdapterBatch(
            records=(record,),
            receipts=(receipt,),
            artifacts=(RawArtifact(sha256=digest, content_type=content_type, content=content),),
        )

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        expected = self.effective_date.isoformat()
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != "/api/rates/secured/sofr/search.json"
            or parsed.params
            or parsed.fragment
            or query
            != {"startDate": [expected], "endDate": [expected], "type": ["rate"]}
        ):
            raise SourceSchemaError("SOFR response URL does not match the requested rate")


def _basis_points(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise SourceSchemaError(f"SOFR {field} must be a finite percentage")
    try:
        numeric = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise SourceSchemaError(f"SOFR {field} must be a finite percentage") from error
    scaled = numeric * 100
    if not numeric.is_finite() or scaled != scaled.to_integral_value():
        raise SourceSchemaError(f"SOFR {field} must resolve to whole basis points")
    basis_points = int(scaled)
    if not -1_000 <= basis_points <= 10_000:
        raise SourceSchemaError(f"SOFR {field} is outside the supported range")
    return basis_points
