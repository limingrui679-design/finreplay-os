"""SEC XBRL company-facts adapter with conservative filing knowledge time."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

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
_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,199}$")


class SECCompanyFactsAdapter:
    """Retrieve every structured XBRL fact for one SEC reporting entity."""

    metadata = AdapterMetadata(
        adapter_id="sec.xbrl.companyfacts",
        title="SEC XBRL company facts",
        publisher="U.S. Securities and Exchange Commission",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"
        ),
        allowed_hosts=("data.sec.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Use an accountable configured User-Agent and sequential requests below SEC "
            "fair-access limits; throttling is surfaced, not retried without a bound."
        ),
        pagination_policy="One company-level JSON response contains all exposed facts.",
        availability_rule=(
            "Use joined EDGAR acceptanceDateTime when supplied. Otherwise use 00:00 UTC on the "
            "calendar day after filed, a conservative no-leakage bound rather than midnight on "
            "the filing date."
        ),
        revision_behavior=(
            "Every fact retains its accession. Amendments and later comparative disclosures are "
            "separate fact events rather than silent overwrites."
        ),
        temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
        license_class=LicenseClass.REDISTRIBUTABLE,
        redistribution_note=(
            "SEC public XBRL facts may be reproduced with attribution and fair-access compliance; "
            "this classification is not legal advice."
        ),
    )

    def __init__(self, http: SafeHttpClient) -> None:
        self.http = http

    def fetch(
        self,
        cik: int,
        *,
        acceptance_times: Mapping[str, datetime] | None = None,
    ) -> AdapterBatch:
        normalized_cik = _normalize_cik(cik)
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{normalized_cik}.json"
        response, content, retrieved_at = self.http.get(
            url,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        content_type = _require_json_content_type(response.headers.get("Content-Type", ""))
        try:
            root = require_json_object(response.json(), "SEC companyfacts response")
        except (ValueError, TypeError) as error:
            raise SourceSchemaError("SEC companyfacts response is not valid JSON") from error
        returned_cik = str(root.get("cik", "")).zfill(10)
        if returned_cik != normalized_cik:
            raise SourceSchemaError(
                f"SEC returned CIK {returned_cik!r}, expected {normalized_cik!r}"
            )
        entity_name = root.get("entityName")
        if not isinstance(entity_name, str) or not entity_name.strip():
            raise SourceSchemaError("SEC companyfacts entityName is missing")
        digest = source_response_sha256(content)
        source_version = f"CIK{normalized_cik}:{digest[:20]}"
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
            vintage_as_of=retrieved_at,
            redistribution_note=self.metadata.redistribution_note,
        )
        records, exact_times, bounded_times, conflict_times, metadata_fallbacks = self._parse_facts(
            root.get("facts"),
            cik=normalized_cik,
            entity_name=entity_name,
            source=source,
            retrieved_at=retrieved_at,
            acceptance_times=acceptance_times or {},
        )
        warnings = (
            "Filer-reported XBRL facts are not independently verified economic truth or an "
            "investment signal.",
            f"Knowledge time used exact EDGAR acceptance for {exact_times} facts and conservative "
            f"next-day filing bounds for {bounded_times} facts; {conflict_times} facts had an "
            "acceptance/filed date conflict and were conservatively delayed.",
            f"Generated {metadata_fallbacks} display fallbacks for null SEC "
            "label/description fields.",
        )
        receipt = FetchReceipt(
            adapter_id=self.metadata.adapter_id,
            request_url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
            retrieved_at=retrieved_at,
            status_code=response.status_code,
            content_type=content_type,
            response_sha256=digest,
            response_bytes=len(content),
            record_count=len(records),
            source_version=source_version,
            temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
            historical_replay_eligible=True,
            warnings=warnings,
        )
        raw = RawArtifact(sha256=digest, content_type=content_type, content=content)
        return AdapterBatch(records=tuple(records), receipts=(receipt,), artifacts=(raw,))

    def _parse_facts(
        self,
        value: Any,
        *,
        cik: str,
        entity_name: str,
        source: SourceReference,
        retrieved_at: datetime,
        acceptance_times: Mapping[str, datetime],
    ) -> tuple[list[BitemporalRecord], int, int, int, int]:
        taxonomies = require_json_object(value, "SEC companyfacts facts")
        records: list[BitemporalRecord] = []
        seen_ids: set[str] = set()
        exact_times = 0
        bounded_times = 0
        conflict_times = 0
        metadata_fallbacks = 0
        for taxonomy, concepts_value in sorted(taxonomies.items()):
            _validate_name(taxonomy, "taxonomy")
            concepts = require_json_object(concepts_value, f"SEC taxonomy {taxonomy}")
            for concept, concept_value in sorted(concepts.items()):
                _validate_name(concept, "concept")
                concept_object = require_json_object(
                    concept_value, f"SEC concept {taxonomy}:{concept}"
                )
                label = concept_object.get("label")
                description = concept_object.get("description")
                if label is not None and not isinstance(label, str):
                    raise SourceSchemaError(f"SEC concept {taxonomy}:{concept} label must be text")
                if description is not None and not isinstance(description, str):
                    raise SourceSchemaError(
                        f"SEC concept {taxonomy}:{concept} description must be text"
                    )
                display_label = label or f"{taxonomy}:{concept}"
                display_description = description or ""
                metadata_fallbacks += int(not label) + int(not description)
                units = require_json_object(
                    concept_object.get("units"), f"SEC concept {taxonomy}:{concept} units"
                )
                for unit, facts_value in sorted(units.items()):
                    if not isinstance(unit, str) or not unit:
                        raise SourceSchemaError("SEC XBRL unit must be non-empty text")
                    if not isinstance(facts_value, list):
                        raise SourceSchemaError(
                            f"SEC facts for {taxonomy}:{concept}:{unit} must be a list"
                        )
                    for position, fact_value in enumerate(facts_value):
                        fact = require_json_object(
                            fact_value,
                            f"SEC fact {taxonomy}:{concept}:{unit}[{position}]",
                        )
                        record, time_method = self._fact_record(
                            fact,
                            taxonomy=taxonomy,
                            concept=concept,
                            label=display_label,
                            description=display_description,
                            source_label=label,
                            source_description=description,
                            unit=unit,
                            cik=cik,
                            entity_name=entity_name,
                            source=source,
                            retrieved_at=retrieved_at,
                            acceptance_times=acceptance_times,
                        )
                        if record.record_id in seen_ids:
                            raise SourceSchemaError(
                                f"duplicate SEC company fact identity: {record.record_id}"
                            )
                        seen_ids.add(record.record_id)
                        records.append(record)
                        exact_times += int(time_method == "acceptance_exact")
                        bounded_times += int(time_method == "filed_next_day_bound")
                        conflict_times += int(
                            time_method == "acceptance_conflict_filed_next_day_bound"
                        )
        if not records:
            raise SourceSchemaError("SEC companyfacts response contains no facts")
        return records, exact_times, bounded_times, conflict_times, metadata_fallbacks

    def _fact_record(
        self,
        fact: dict[str, Any],
        *,
        taxonomy: str,
        concept: str,
        label: str,
        description: str,
        source_label: str | None,
        source_description: str | None,
        unit: str,
        cik: str,
        entity_name: str,
        source: SourceReference,
        retrieved_at: datetime,
        acceptance_times: Mapping[str, datetime],
    ) -> tuple[BitemporalRecord, str]:
        for field in ("end", "val", "accn", "form", "filed"):
            if field not in fact:
                raise SourceSchemaError(f"SEC company fact is missing {field}")
        accession = fact["accn"]
        if not isinstance(accession, str) or _ACCESSION_PATTERN.fullmatch(accession) is None:
            raise SourceSchemaError(f"invalid SEC fact accession: {accession!r}")
        form = fact["form"]
        if not isinstance(form, str) or not form:
            raise SourceSchemaError("SEC fact form must be non-empty text")
        value = fact["val"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SourceSchemaError("SEC XBRL fact value must be numeric")
        if isinstance(value, float) and not math.isfinite(value):
            raise SourceSchemaError("SEC XBRL fact value must be finite")
        end = _parse_sec_date(fact["end"], "end")
        start_value = fact.get("start")
        start = end
        valid_to = None
        if start_value not in (None, ""):
            start = _parse_sec_date(start_value, "start")
            if start > end:
                raise SourceSchemaError("SEC XBRL fact start must not follow end")
            valid_to = end + timedelta(days=1)
        filed = _parse_sec_date(fact["filed"], "filed")
        accepted = acceptance_times.get(accession)
        if accepted is not None:
            if accepted.tzinfo is None or accepted.utcoffset() is None:
                raise ValueError(f"acceptance time for {accession} must be timezone-aware")
            accepted_utc = accepted.astimezone(UTC)
            if accepted_utc.date() < filed.date():
                available_at = filed + timedelta(days=1)
                confidence = 0.8
                time_method = "acceptance_conflict_filed_next_day_bound"
                rule = (
                    "EDGAR acceptanceDateTime precedes the XBRL filed date. Preserve both source "
                    "values and conservatively delay availability to 00:00 UTC on the next day."
                )
            else:
                available_at = accepted_utc
                confidence = 1.0
                time_method = "acceptance_exact"
                rule = "Joined official EDGAR acceptanceDateTime by accession."
        else:
            available_at = filed + timedelta(days=1)
            confidence = 0.9
            time_method = "filed_next_day_bound"
            rule = (
                "No acceptance timestamp joined; conservatively available at 00:00 UTC on the "
                "calendar day after the SEC filed date."
            )
        if available_at > retrieved_at:
            raise SourceSchemaError("SEC fact availability cannot be after retrieval time")
        identity_payload = {
            "taxonomy": taxonomy,
            "concept": concept,
            "unit": unit,
            "fact": fact,
        }
        identity_hash = hashlib.sha256(_canonical_json(identity_payload).encode()).hexdigest()
        payload = {
            "cik": cik,
            "entity_name": entity_name,
            "taxonomy": taxonomy,
            "concept": concept,
            "label": label,
            "description": description,
            "source_label": source_label,
            "source_description": source_description,
            "unit": unit,
            **fact,
            "knowledge_time_method": time_method,
        }
        record = BitemporalRecord(
            record_id=f"sec.xbrl.companyfacts:{cik}:{identity_hash}",
            entity_id=f"sec_cik:{cik}",
            source=source,
            interval=BitemporalInterval(
                valid_from=start,
                valid_to=valid_to,
                published_at=filed,
                available_at=available_at,
                ingested_at=retrieved_at,
                availability_rule=rule,
                availability_confidence=confidence,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload=payload,
        )
        return record, time_method


def _validate_name(value: Any, field: str) -> None:
    if not isinstance(value, str) or _NAME_PATTERN.fullmatch(value) is None:
        raise SourceSchemaError(f"SEC XBRL {field} name is invalid: {value!r}")


def _normalize_cik(cik: int) -> str:
    if not 1 <= cik <= 9_999_999_999:
        raise ValueError("CIK must be a positive integer with at most ten digits")
    return f"{cik:010d}"


def _parse_sec_date(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SourceSchemaError(f"SEC XBRL {field} must be a non-empty date")
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as error:
        raise SourceSchemaError(f"SEC XBRL {field} is not YYYY-MM-DD: {value!r}") from error


def _require_json_content_type(value: str) -> str:
    content_type = value.split(";", maxsplit=1)[0].lower()
    if content_type not in {"application/json", "text/json"}:
        raise SourceSchemaError(f"unexpected SEC content type: {content_type!r}")
    return content_type


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
