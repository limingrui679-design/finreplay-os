"""Revision-safe EIA Weekly Natural Gas Storage Report history adapter."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from email.utils import parsedate_to_datetime
from io import BytesIO
from typing import Any, Final, TypedDict, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import xlrd
from pydantic import HttpUrl, TypeAdapter
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from xlrd.biffh import XLRDError
from xlrd.compdoc import CompDocError

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
_CHICAGO = ZoneInfo("America/Chicago")
_OLE_SIGNATURE: Final = bytes.fromhex("d0cf11e0a1b11ae1")
_REVISIONS_URL = "https://ir.eia.gov/ngs/revisions.xls"
_HISTORY_URL = "https://ir.eia.gov/ngs/ngshistory.xls"
_EVALUATION_URL = "https://ir.eia.gov/ngs/wngsrevaluation_2024.pdf"
_VERIFIED_RELEASES = {
    date(2020, 3, 12): (date(2020, 3, 6), date(2020, 2, 28)),
    date(2020, 3, 19): (date(2020, 3, 13), date(2020, 3, 6)),
    date(2020, 3, 26): (date(2020, 3, 20), date(2020, 3, 13)),
}
_EXPECTED = {
    date(2020, 3, 6): {
        "regions": (426, 529, 97, 200, 791, 235, 556),
        "lower_48": 2_043,
        "prior_lower_48": 2_091,
        "net_change": -48,
        "coefficient_of_variation_percent": "0.5",
        "net_change_standard_error_bcf": "0.6",
    },
    date(2020, 3, 13): {
        "regions": (412, 512, 96, 199, 814, 247, 568),
        "lower_48": 2_034,
        "prior_lower_48": 2_043,
        "net_change": -9,
        "coefficient_of_variation_percent": "0.5",
        "net_change_standard_error_bcf": "0.8",
    },
    date(2020, 3, 20): {
        "regions": (398, 492, 92, 194, 829, 258, 571),
        "lower_48": 2_005,
        "prior_lower_48": 2_034,
        "net_change": -29,
        "coefficient_of_variation_percent": "0.5",
        "net_change_standard_error_bcf": "0.8",
    },
}


class _WorkingGasRow(TypedDict):
    east_bcf: int
    midwest_bcf: int
    mountain_bcf: int
    pacific_bcf: int
    south_central_bcf: int
    salt_bcf: int
    nonsalt_bcf: int
    lower_48_bcf: int
    explanation: str


class _StatisticalRow(TypedDict):
    coefficient_of_variation_percent_lower_48: str
    net_change_standard_error_bcf_lower_48: str


class EIAWNGSRWorkingGasHistoryAdapter:
    """Retrieve three March 2020 original WNGSR stocks from official history products."""

    availability_rule = (
        "The EIA 2020-22 WNGSR performance evaluation states that WNGSR is released each "
        "Thursday at 10:30 a.m. Eastern, that every 2020-22 release met the established "
        "schedule, and that the first remote-posture release was March 19, 2020 without "
        "publication disruption. The selected March 12, 19, and 26 non-holiday Thursdays are "
        "therefore eligible at 10:30 America/New_York. Current response headers are retrieval "
        "metadata only and are never backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="eia.wngsr.revision_safe_working_gas",
        title="EIA revision-safe WNGSR Lower 48 working-gas stocks",
        publisher="U.S. Energy Information Administration",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://ir.eia.gov/ngs/evaluation.html"
        ),
        allowed_hosts=("ir.eia.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only revisions.xls, ngshistory.xls, and the fixed 2020-22 performance "
            "evaluation once per validation; do not crawl WNGSR pages or archives."
        ),
        pagination_policy="Each official source is one complete response without pagination.",
        availability_rule=availability_rule,
        revision_behavior=(
            "revisions.xls explicitly preserves the original estimate before a published "
            "revision or reclassification. The selected rows have no published revision note "
            "and must equal the current ngshistory.xls rows. Response-level hashes bind the "
            "retrieved consolidated archives, which may grow after validation."
        ),
        temporal_coverage=TemporalCoverage.VINTAGE_NATIVE,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "EIA government data are reusable with acknowledgment, but complete XLS and PDF "
            "responses remain in local content-addressed storage. The repository retains only "
            "minimal selected facts, URLs, hashes, source semantics, and release timing."
        ),
    )

    def __init__(
        self,
        http: SafeHttpClient,
        *,
        release_dates: tuple[date, ...] = tuple(_VERIFIED_RELEASES),
    ) -> None:
        if release_dates != tuple(sorted(set(release_dates))):
            raise ValueError("EIA WNGSR release dates must be unique and chronological")
        if not release_dates or any(item not in _VERIFIED_RELEASES for item in release_dates):
            raise ValueError("release date is not in the verified EIA WNGSR calendar")
        self.http = http
        self.release_dates = release_dates

    def fetch(self) -> AdapterBatch:
        revisions_response, revisions_content, revisions_retrieved_at = (
            self.http.get_same_host_signed_redirect(
            _REVISIONS_URL,
            allowed_hosts=self.metadata.allowed_hosts,
                expected_redirect_path="/secure/ngs/revisions.xls",
            )
        )
        history_response, history_content, history_retrieved_at = (
            self.http.get_same_host_signed_redirect(
            _HISTORY_URL,
            allowed_hosts=self.metadata.allowed_hosts,
                expected_redirect_path="/secure/ngs/ngshistory.xls",
            )
        )
        evaluation_response, evaluation_content, evaluation_retrieved_at = self.http.get(
            _EVALUATION_URL,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        responses = (
            ("revisions", revisions_response, revisions_content, revisions_retrieved_at),
            ("history", history_response, history_content, history_retrieved_at),
            ("evaluation", evaluation_response, evaluation_content, evaluation_retrieved_at),
        )
        for kind, response, _content, _retrieved_at in responses:
            self._validate_response_url(response.request_url, kind=kind)

        revisions_type = self._content_type(
            revisions_response.headers.get("Content-Type"),
            expected="application/vnd.ms-excel",
            kind="revisions workbook",
        )
        history_type = self._content_type(
            history_response.headers.get("Content-Type"),
            expected="application/vnd.ms-excel",
            kind="history workbook",
        )
        evaluation_type = self._content_type(
            evaluation_response.headers.get("Content-Type"),
            expected="application/pdf",
            kind="performance evaluation",
        )
        revisions_last_modified = self._last_modified(
            revisions_response.headers.get("Last-Modified"),
            kind="revisions workbook",
            retrieved_at=revisions_retrieved_at,
        )
        history_last_modified = self._last_modified(
            history_response.headers.get("Last-Modified"),
            kind="history workbook",
            retrieved_at=history_retrieved_at,
        )
        evaluation_last_modified = self._last_modified(
            evaluation_response.headers.get("Last-Modified"),
            kind="performance evaluation",
            retrieved_at=evaluation_retrieved_at,
        )

        revisions_rows = self._parse_revisions_workbook(revisions_content)
        history_rows = self._parse_history_workbook(history_content)
        statistics = self._validate_evaluation(evaluation_content)
        for week_ending in {item[0] for item in _VERIFIED_RELEASES.values()}:
            revisions_row = revisions_rows[week_ending]
            history_row = history_rows[week_ending]
            if self._stock_values(revisions_row) != self._stock_values(history_row):
                raise SourceSchemaError(
                    "EIA WNGSR current history does not match the original selected estimate"
                )
            if revisions_row["explanation"]:
                raise SourceSchemaError(
                    "selected EIA WNGSR original estimate has a published revision note"
                )

        revisions_digest = source_response_sha256(revisions_content)
        history_digest = source_response_sha256(history_content)
        evaluation_digest = source_response_sha256(evaluation_content)
        retrieved_at = max(
            revisions_retrieved_at,
            history_retrieved_at,
            evaluation_retrieved_at,
        )
        records = tuple(
            self._record(
                release_date=release_date,
                row=revisions_rows[_VERIFIED_RELEASES[release_date][0]],
                statistics=statistics[_VERIFIED_RELEASES[release_date][0]],
                retrieved_at=retrieved_at,
                revisions_digest=revisions_digest,
                history_digest=history_digest,
                evaluation_digest=evaluation_digest,
                revisions_last_modified=revisions_last_modified,
                history_last_modified=history_last_modified,
                evaluation_last_modified=evaluation_last_modified,
            )
            for release_date in self.release_dates
        )
        batch_version = (
            f"EIA-WNGSR:2020-03:rev:{revisions_digest[:20]}:"
            f"hist:{history_digest[:20]}:eval:{evaluation_digest[:20]}"
        )
        warnings = (
            "revisions.xls and ngshistory.xls are growing consolidated archives; the receipt "
            "hashes the exact responses retrieved for this validation.",
            "The original-estimate workbook, current-history workbook, and fixed performance "
            "evaluation must agree on every selected fact and statistical measure.",
            "Current Last-Modified headers are retrieval metadata, not historical release-time "
            "evidence.",
            "The EIA sampling statistics remain source metadata and do not define a FinReplay "
            "stress interval or probability.",
        )
        receipts = (
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(
                    revisions_response.request_url
                ),
                retrieved_at=revisions_retrieved_at,
                status_code=revisions_response.status_code,
                content_type=revisions_type,
                response_sha256=revisions_digest,
                response_bytes=len(revisions_content),
                record_count=len(records),
                source_version=batch_version,
                temporal_coverage=TemporalCoverage.VINTAGE_NATIVE,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(history_response.request_url),
                retrieved_at=history_retrieved_at,
                status_code=history_response.status_code,
                content_type=history_type,
                response_sha256=history_digest,
                response_bytes=len(history_content),
                record_count=0,
                source_version=batch_version,
                temporal_coverage=TemporalCoverage.VINTAGE_NATIVE,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(
                    evaluation_response.request_url
                ),
                retrieved_at=evaluation_retrieved_at,
                status_code=evaluation_response.status_code,
                content_type=evaluation_type,
                response_sha256=evaluation_digest,
                response_bytes=len(evaluation_content),
                record_count=0,
                source_version=batch_version,
                temporal_coverage=TemporalCoverage.VINTAGE_NATIVE,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
        )
        artifacts = (
            RawArtifact(
                sha256=revisions_digest,
                content_type=revisions_type,
                content=revisions_content,
            ),
            RawArtifact(
                sha256=history_digest,
                content_type=history_type,
                content=history_content,
            ),
            RawArtifact(
                sha256=evaluation_digest,
                content_type=evaluation_type,
                content=evaluation_content,
            ),
        )
        return AdapterBatch(records=records, receipts=receipts, artifacts=artifacts)

    def _record(
        self,
        *,
        release_date: date,
        row: _WorkingGasRow,
        statistics: _StatisticalRow,
        retrieved_at: datetime,
        revisions_digest: str,
        history_digest: str,
        evaluation_digest: str,
        revisions_last_modified: datetime,
        history_last_modified: datetime,
        evaluation_last_modified: datetime,
    ) -> BitemporalRecord:
        week_ending, prior_week_ending = _VERIFIED_RELEASES[release_date]
        available_at = datetime.combine(
            release_date,
            time(10, 30),
            tzinfo=_NEW_YORK,
        ).astimezone(UTC)
        valid_from = datetime.combine(
            week_ending,
            time(9),
            tzinfo=_CHICAGO,
        ).astimezone(UTC)
        expected = _EXPECTED[week_ending]
        prior_value = expected["prior_lower_48"]
        if not isinstance(prior_value, int):
            raise SourceSchemaError("EIA WNGSR expected prior value is not an integer")
        prior = prior_value
        net_change = row["lower_48_bcf"] - prior
        source_version = (
            f"EIA-WNGSR:{release_date.isoformat()}:{week_ending.isoformat()}:"
            f"rev:{revisions_digest[:20]}:hist:{history_digest[:20]}:"
            f"eval:{evaluation_digest[:20]}"
        )
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(_REVISIONS_URL),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=revisions_digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.VINTAGE_NATIVE,
            vintage_as_of=available_at,
            redistribution_note=self.metadata.redistribution_note,
        )
        return BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{release_date:%Y%m%d}:"
                f"lower48_working_gas:{revisions_digest[:16]}"
            ),
            entity_id="eia_series:wngsr_working_gas_lower_48",
            source=source,
            interval=BitemporalInterval(
                valid_from=valid_from,
                published_at=available_at,
                available_at=available_at,
                ingested_at=retrieved_at,
                availability_rule=self.availability_rule,
                availability_confidence=1.0,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload={
                "release_date": release_date.isoformat(),
                "week_ending": week_ending.isoformat(),
                "prior_week_ending": prior_week_ending.isoformat(),
                "metric": "working_gas_in_underground_storage_lower_48",
                "value_bcf": row["lower_48_bcf"],
                "prior_value_bcf": prior,
                "reported_net_change_bcf": net_change,
                "east_bcf": row["east_bcf"],
                "midwest_bcf": row["midwest_bcf"],
                "mountain_bcf": row["mountain_bcf"],
                "pacific_bcf": row["pacific_bcf"],
                "south_central_bcf": row["south_central_bcf"],
                "salt_bcf": row["salt_bcf"],
                "nonsalt_bcf": row["nonsalt_bcf"],
                "five_region_rounding_difference_bcf": (
                    row["east_bcf"]
                    + row["midwest_bcf"]
                    + row["mountain_bcf"]
                    + row["pacific_bcf"]
                    + row["south_central_bcf"]
                    - row["lower_48_bcf"]
                ),
                "south_central_subregion_rounding_difference_bcf": (
                    row["salt_bcf"]
                    + row["nonsalt_bcf"]
                    - row["south_central_bcf"]
                ),
                "unit": "Billion Cubic Feet",
                "source_form": "EIA-912",
                "inventory_as_of_local": datetime.combine(
                    week_ending,
                    time(9),
                    tzinfo=_CHICAGO,
                ).isoformat(),
                "release_time_local": "10:30:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": "EDT",
                "official_release_at": available_at.isoformat(),
                "coefficient_of_variation_percent_lower_48": statistics[
                    "coefficient_of_variation_percent_lower_48"
                ],
                "net_change_standard_error_bcf_lower_48": statistics[
                    "net_change_standard_error_bcf_lower_48"
                ],
                "statistical_measures_define_finreplay_range": False,
                "published_revision_or_reclassification_note": None,
                "current_history_matches_original_estimate": True,
                "revision_history_semantics": (
                    "original estimate before any published revision or reclassification"
                ),
                "revisions_workbook_url": _REVISIONS_URL,
                "revisions_workbook_sha256": revisions_digest,
                "history_workbook_url": _HISTORY_URL,
                "history_workbook_sha256": history_digest,
                "performance_evaluation_url": _EVALUATION_URL,
                "performance_evaluation_sha256": evaluation_digest,
                "performance_evaluation_pages": 24,
                "revisions_last_modified_at": revisions_last_modified.isoformat(),
                "history_last_modified_at": history_last_modified.isoformat(),
                "evaluation_last_modified_at": evaluation_last_modified.isoformat(),
                "availability_method": (
                    "official_standard_release_time_plus_2020_schedule-performance proof"
                ),
            },
        )

    def _parse_revisions_workbook(self, content: bytes) -> dict[date, _WorkingGasRow]:
        book = self._open_workbook(content, kind="revisions")
        if book.sheet_names() != ["original_data"]:
            raise SourceSchemaError("EIA WNGSR revisions workbook sheet identity changed")
        sheet = book.sheet_by_name("original_data")
        if sheet.nrows < 253 or sheet.ncols != 11:
            raise SourceSchemaError("EIA WNGSR revisions workbook shape is incomplete")
        note = str(sheet.cell_value(0, 0))
        required_note = (
            "updated with the original estimate prior to the revision or reclassification"
        )
        if required_note not in " ".join(note.split()):
            raise SourceSchemaError("EIA WNGSR revisions workbook semantics changed")
        expected_header = [
            "Week ending",
            "Source",
            "East Region",
            "Midwest Region",
            "Mountain Region",
            "Pacific Region",
            "South Central Region",
            "Salt",
            "NonSalt",
            "Total Lower 48",
            "Explanation",
        ]
        if sheet.row_values(1, 0, 11) != expected_header:
            raise SourceSchemaError("EIA WNGSR revisions workbook header changed")
        return self._selected_rows(book, sheet, first_data_row=2, explanation_column=10)

    def _parse_history_workbook(self, content: bytes) -> dict[date, _WorkingGasRow]:
        book = self._open_workbook(content, kind="history")
        if book.sheet_names() != ["html_report_history", "weekly_net_changes"]:
            raise SourceSchemaError("EIA WNGSR history workbook sheet identity changed")
        sheet = book.sheet_by_name("html_report_history")
        if sheet.nrows < 542 or sheet.ncols != 10:
            raise SourceSchemaError("EIA WNGSR history workbook shape is incomplete")
        expected_header = [
            "Week ending",
            "Source",
            "East Region",
            "Midwest Region",
            "Mountain Region",
            "Pacific Region",
            "South Central Region",
            "Salt",
            "NonSalt",
            "Total Lower 48",
        ]
        if sheet.row_values(6, 0, 10) != expected_header:
            raise SourceSchemaError("EIA WNGSR history workbook header changed")
        return self._selected_rows(book, sheet, first_data_row=7, explanation_column=None)

    def _selected_rows(
        self,
        book: Any,
        sheet: Any,
        *,
        first_data_row: int,
        explanation_column: int | None,
    ) -> dict[date, _WorkingGasRow]:
        targets = {item[0] for item in _VERIFIED_RELEASES.values()}
        found: dict[date, _WorkingGasRow] = {}
        for row_index in range(first_data_row, sheet.nrows):
            raw_date = sheet.cell_value(row_index, 0)
            if not isinstance(raw_date, float):
                continue
            try:
                week_ending = xlrd.xldate_as_datetime(raw_date, book.datemode).date()
            except (OverflowError, TypeError, ValueError) as error:
                raise SourceSchemaError("EIA WNGSR workbook contains an invalid date") from error
            if week_ending not in targets:
                continue
            if week_ending in found:
                raise SourceSchemaError("EIA WNGSR workbook duplicates a selected week")
            if sheet.cell_value(row_index, 1) != "EIA-912":
                raise SourceSchemaError("EIA WNGSR selected row source identity changed")
            values = tuple(
                self._integer_cell(sheet.cell_value(row_index, column))
                for column in range(2, 10)
            )
            explanation = (
                str(sheet.cell_value(row_index, explanation_column)).strip()
                if explanation_column is not None
                else ""
            )
            row: _WorkingGasRow = {
                "east_bcf": values[0],
                "midwest_bcf": values[1],
                "mountain_bcf": values[2],
                "pacific_bcf": values[3],
                "south_central_bcf": values[4],
                "salt_bcf": values[5],
                "nonsalt_bcf": values[6],
                "lower_48_bcf": values[7],
                "explanation": explanation,
            }
            self._validate_row(week_ending, row)
            found[week_ending] = row
        if set(found) != targets:
            raise SourceSchemaError("EIA WNGSR workbook is missing a selected week")
        return found

    def _validate_row(self, week_ending: date, row: _WorkingGasRow) -> None:
        expected = _EXPECTED[week_ending]
        regions = (
            row["east_bcf"],
            row["midwest_bcf"],
            row["mountain_bcf"],
            row["pacific_bcf"],
            row["south_central_bcf"],
            row["salt_bcf"],
            row["nonsalt_bcf"],
        )
        if regions != expected["regions"] or row["lower_48_bcf"] != expected["lower_48"]:
            raise SourceSchemaError("EIA WNGSR selected working-gas values changed")
        if abs(sum(regions[:5]) - row["lower_48_bcf"]) > 2:
            raise SourceSchemaError("EIA WNGSR five-region rounding difference exceeds 2 Bcf")
        if abs(row["salt_bcf"] + row["nonsalt_bcf"] - row["south_central_bcf"]) > 2:
            raise SourceSchemaError(
                "EIA WNGSR South Central rounding difference exceeds 2 Bcf"
            )

    @staticmethod
    def _stock_values(row: _WorkingGasRow) -> tuple[int, ...]:
        return (
            row["east_bcf"],
            row["midwest_bcf"],
            row["mountain_bcf"],
            row["pacific_bcf"],
            row["south_central_bcf"],
            row["salt_bcf"],
            row["nonsalt_bcf"],
            row["lower_48_bcf"],
        )

    def _validate_evaluation(self, content: bytes) -> dict[date, _StatisticalRow]:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("EIA WNGSR performance evaluation is not a PDF")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 24:
                raise SourceSchemaError("EIA WNGSR performance evaluation must have 24 pages")
            page_text = {
                number: reader.pages[number - 1].extract_text() or ""
                for number in (8, 13, 15, 19)
            }
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError(
                "EIA WNGSR performance evaluation could not be parsed"
            ) from error
        if any(not text.strip() for text in page_text.values()):
            raise SourceSchemaError("EIA WNGSR required evaluation page has no text")
        normalized = {page: " ".join(text.split()) for page, text in page_text.items()}
        remote_markers = (
            "The remote telework posture did not disrupt WNGSR collection and publication.",
            "March 19, 2020",
            "have not resulted in any delayed releases or data breaches of WNGSR",
        )
        if any(marker not in normalized[8] for marker in remote_markers):
            raise SourceSchemaError("EIA WNGSR remote-publication evidence changed")
        schedule_markers = (
            "EIA releases the WNGSR each Thursday at 10:30 a.m. ET",
            "From 2020 to 2022, EIA released the WNGSR every week according to the "
            "established schedule.",
        )
        if any(marker not in normalized[13] for marker in schedule_markers):
            raise SourceSchemaError("EIA WNGSR release-schedule evidence changed")
        if "Table A4. Estimated coefficients of variation" not in normalized[15]:
            raise SourceSchemaError("EIA WNGSR coefficient table identity changed")
        if "Table A8. Estimated standard errors" not in normalized[19]:
            raise SourceSchemaError("EIA WNGSR standard-error table identity changed")

        output: dict[date, _StatisticalRow] = {}
        for week_ending in {item[0] for item in _VERIFIED_RELEASES.values()}:
            label = week_ending.strftime("%d-%b-%y")
            coefficient = self._table_last_value(normalized[15], label, percent=True)
            standard_error = self._table_last_value(normalized[19], label, percent=False)
            expected = _EXPECTED[week_ending]
            if coefficient != expected["coefficient_of_variation_percent"]:
                raise SourceSchemaError("EIA WNGSR selected coefficient of variation changed")
            if standard_error != expected["net_change_standard_error_bcf"]:
                raise SourceSchemaError("EIA WNGSR selected standard error changed")
            output[week_ending] = {
                "coefficient_of_variation_percent_lower_48": coefficient,
                "net_change_standard_error_bcf_lower_48": standard_error,
            }
        return output

    @staticmethod
    def _table_last_value(text: str, label: str, *, percent: bool) -> str:
        suffix = r"%" if percent else ""
        pattern = re.compile(
            rf"{re.escape(label)}(?:\s+[0-9]+\.[0-9]{re.escape(suffix)}){{7}}\s+"
            rf"([0-9]+\.[0-9]){re.escape(suffix)}"
        )
        matches = pattern.findall(text)
        if len(matches) != 1:
            raise SourceSchemaError("EIA WNGSR evaluation row is missing or duplicated")
        return cast(str, matches[0])

    @staticmethod
    def _open_workbook(content: bytes, *, kind: str) -> Any:
        if not content.startswith(_OLE_SIGNATURE):
            raise SourceSchemaError(f"EIA WNGSR {kind} response is not an XLS workbook")
        try:
            return xlrd.open_workbook(file_contents=content, on_demand=True)
        except (CompDocError, XLRDError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError(f"EIA WNGSR {kind} workbook could not be parsed") from error

    @staticmethod
    def _integer_cell(value: Any) -> int:
        if not isinstance(value, float) or not value.is_integer():
            raise SourceSchemaError("EIA WNGSR selected stock must be an integer Bcf value")
        normalized = int(value)
        if normalized <= 0 or normalized > 10_000:
            raise SourceSchemaError("EIA WNGSR selected stock is outside supported bounds")
        return normalized

    @staticmethod
    def _content_type(raw: str | None, *, expected: str, kind: str) -> str:
        content_type = (raw or "").split(";", maxsplit=1)[0].lower()
        if content_type != expected:
            raise SourceSchemaError(f"unexpected EIA WNGSR {kind} content type")
        return content_type

    @staticmethod
    def _last_modified(
        raw: str | None,
        *,
        kind: str,
        retrieved_at: datetime,
    ) -> datetime:
        if raw is None:
            raise SourceSchemaError(f"EIA WNGSR {kind} lacks Last-Modified")
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError) as error:
            raise SourceSchemaError(f"EIA WNGSR {kind} Last-Modified is invalid") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise SourceSchemaError(f"EIA WNGSR {kind} Last-Modified lacks a timezone")
        normalized = parsed.astimezone(UTC)
        if normalized > retrieved_at:
            raise SourceSchemaError(f"EIA WNGSR {kind} Last-Modified is in the future")
        return normalized

    @staticmethod
    def _validate_response_url(raw_url: str, *, kind: str) -> None:
        expected_path = {
            "revisions": "/ngs/revisions.xls",
            "history": "/ngs/ngshistory.xls",
            "evaluation": "/ngs/wngsrevaluation_2024.pdf",
        }[kind]
        parsed = urlparse(raw_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "ir.eia.gov"
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError(f"EIA WNGSR {kind} response URL does not match request")
