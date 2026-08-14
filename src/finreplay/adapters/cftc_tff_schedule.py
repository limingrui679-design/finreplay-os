"""Schedule-anchored CFTC TFF evidence for one fixed 2026 contract window."""

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from html.parser import HTMLParser
from io import BytesIO, StringIO
from itertools import pairwise
from urllib.parse import parse_qs, urlparse
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
    require_json_object,
    source_response_sha256,
)
from finreplay.adapters.cftc import CFTC_COT_BY_SLUG, CFTCCOTAdapter
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
_CONTRACT_CODE = "042601"
_API_ORDER = "report_date_as_yyyy_mm_dd ASC,cftc_contract_market_code ASC,id ASC"
_API_WHERE = (
    'cftc_contract_market_code="042601" AND report_date_as_yyyy_mm_dd in('
    '"2026-07-14T00:00:00.000","2026-07-21T00:00:00.000",'
    '"2026-07-28T00:00:00.000")'
)

_API_EXTRA_FIELDS = (
    "cftc_market_code",
    "commodity",
    "commodity_subgroup_name",
    "commodity_group_name",
    "contract_units",
    "change_in_open_interest_all",
    "change_in_asset_mgr_long",
    "change_in_asset_mgr_short",
    "change_in_asset_mgr_spread",
    "traders_tot_all",
    "traders_asset_mgr_long_all",
    "traders_asset_mgr_short_all",
    "traders_asset_mgr_spread",
)

_NUMERIC_FIELDS = (
    "open_interest_all",
    "dealer_positions_long_all",
    "dealer_positions_short_all",
    "dealer_positions_spread_all",
    "asset_mgr_positions_long",
    "asset_mgr_positions_short",
    "asset_mgr_positions_spread",
    "lev_money_positions_long",
    "lev_money_positions_short",
    "lev_money_positions_spread",
    "other_rept_positions_long",
    "other_rept_positions_short",
    "other_rept_positions_spread",
    "tot_rept_positions_long_all",
    "tot_rept_positions_short",
    "nonrept_positions_long_all",
    "nonrept_positions_short_all",
    "change_in_open_interest_all",
    "change_in_asset_mgr_long",
    "change_in_asset_mgr_short",
    "change_in_asset_mgr_spread",
    "traders_tot_all",
    "traders_asset_mgr_long_all",
    "traders_asset_mgr_short_all",
    "traders_asset_mgr_spread",
)
_SIGNED_FIELDS = frozenset(
    {
        "change_in_open_interest_all",
        "change_in_asset_mgr_long",
        "change_in_asset_mgr_short",
        "change_in_asset_mgr_spread",
    }
)
_POSITION_FIELDS = _NUMERIC_FIELDS[:17]
_TRADER_FIELDS = _NUMERIC_FIELDS[-4:]

_ANNUAL_TO_API = {
    "Market_and_Exchange_Names": "market_and_exchange_names",
    "Report_Date_as_YYYY-MM-DD": "report_date_as_yyyy_mm_dd",
    "CFTC_Contract_Market_Code": "cftc_contract_market_code",
    "CFTC_Market_Code": "cftc_market_code",
    "Open_Interest_All": "open_interest_all",
    "Dealer_Positions_Long_All": "dealer_positions_long_all",
    "Dealer_Positions_Short_All": "dealer_positions_short_all",
    "Dealer_Positions_Spread_All": "dealer_positions_spread_all",
    "Asset_Mgr_Positions_Long_All": "asset_mgr_positions_long",
    "Asset_Mgr_Positions_Short_All": "asset_mgr_positions_short",
    "Asset_Mgr_Positions_Spread_All": "asset_mgr_positions_spread",
    "Lev_Money_Positions_Long_All": "lev_money_positions_long",
    "Lev_Money_Positions_Short_All": "lev_money_positions_short",
    "Lev_Money_Positions_Spread_All": "lev_money_positions_spread",
    "Other_Rept_Positions_Long_All": "other_rept_positions_long",
    "Other_Rept_Positions_Short_All": "other_rept_positions_short",
    "Other_Rept_Positions_Spread_All": "other_rept_positions_spread",
    "Tot_Rept_Positions_Long_All": "tot_rept_positions_long_all",
    "Tot_Rept_Positions_Short_All": "tot_rept_positions_short",
    "NonRept_Positions_Long_All": "nonrept_positions_long_all",
    "NonRept_Positions_Short_All": "nonrept_positions_short_all",
    "Change_in_Open_Interest_All": "change_in_open_interest_all",
    "Change_in_Asset_Mgr_Long_All": "change_in_asset_mgr_long",
    "Change_in_Asset_Mgr_Short_All": "change_in_asset_mgr_short",
    "Change_in_Asset_Mgr_Spread_All": "change_in_asset_mgr_spread",
    "Traders_Tot_All": "traders_tot_all",
    "Traders_Asset_Mgr_Long_All": "traders_asset_mgr_long_all",
    "Traders_Asset_Mgr_Short_All": "traders_asset_mgr_short_all",
    "Traders_Asset_Mgr_Spread_All": "traders_asset_mgr_spread",
    "Contract_Units": "contract_units",
    "FutOnly_or_Combined": "futonly_or_combined",
}

_TEXT_EXPECTATIONS = {
    "market_and_exchange_names": "UST 2Y NOTE - CHICAGO BOARD OF TRADE",
    "contract_market_name": "UST 2Y NOTE",
    "cftc_contract_market_code": _CONTRACT_CODE,
    "cftc_market_code": "CBT",
    "commodity_name": "T-NOTES, 1-2 YEAR",
    "commodity": "T-NOTES, 1-2 YEAR",
    "commodity_subgroup_name": "Interest Rates - U.S. Treasury",
    "commodity_group_name": "FINANCIAL INSTRUMENTS",
    "contract_units": "(CONTRACTS OF $200,000 FACE VALUE)",
    "futonly_or_combined": "FutOnly",
}

_SCHEDULE_MARKERS = (
    "The Commitments of Traders reports are released at 3:30 p.m. Eastern time.",
    "The release usually includes data from the previous Tuesday.",
    "The following is a tentative schedule of releases through 2026.",
    "2026 Release Schedule",
    "July 06* 10 17 24 31",
)
_POLICY_MARKERS = (
    "provide a breakdown of each Tuesday's open interest",
    "the actual trader category or classification is based on the predominant business purpose "
    "self-reported by traders on the CFTC Form 40",
    "CFTC staff does not know specific reasons for traders' positions",
    "Generally speaking, there are three ways that a change in reported positions in the COT "
    "Report can happen:",
    "A new trader has submitted a Form 40",
    "An existing trader has left the market",
    "There is not a list of historical release dates",
    "No, historical data is not updated once published.",
)
_NOTES_MARKERS = (
    "TRADERS IN FINANCIAL FUTURES Explanatory Notes",
    "Dealer/Intermediary; Asset Manager/Institutional; Leveraged Funds; and Other Reportables.",
    '"Spreading" is a computed amount equal to offsetting long and short positions held by a '
    "trader.",
    "The sum of the numbers of traders in each separate category typically exceeds the total "
    "number of reportable traders.",
    "staff classifies traders, not their trading activity",
    "cannot know with certainty that all of that trader's activity is speculative",
)


@dataclass(frozen=True, slots=True)
class _ReportSpec:
    report_date: date
    release_date: date
    identity: str
    report_week: str
    numeric_values: tuple[int, ...]

    @property
    def expected_numeric(self) -> dict[str, int]:
        return dict(zip(_NUMERIC_FIELDS, self.numeric_values, strict=True))


_REPORT_SPECS = (
    _ReportSpec(
        report_date=date(2026, 7, 14),
        release_date=date(2026, 7, 17),
        identity="260714042601F",
        report_week="2026 Report Week 28",
        numeric_values=(
            4_465_199,
            120_633,
            579_515,
            30_735,
            2_448_262,
            575_447,
            548_339,
            389_534,
            2_061_300,
            305_388,
            394_485,
            209_869,
            2_003,
            4_239_379,
            4_312_596,
            225_820,
            152_603,
            4_262,
            -27_675,
            -8_497,
            322,
            506,
            161,
            64,
            108,
        ),
    ),
    _ReportSpec(
        report_date=date(2026, 7, 21),
        release_date=date(2026, 7, 24),
        identity="260721042601F",
        report_week="2026 Report Week 29",
        numeric_values=(
            4_335_075,
            116_660,
            580_373,
            27_723,
            2_407_459,
            586_571,
            546_120,
            358_297,
            1_953_425,
            265_129,
            390_094,
            218_932,
            1_985,
            4_113_467,
            4_180_258,
            221_608,
            154_817,
            -130_124,
            -40_803,
            11_124,
            -2_219,
            499,
            162,
            58,
            106,
        ),
    ),
    _ReportSpec(
        report_date=date(2026, 7, 28),
        release_date=date(2026, 7, 31),
        identity="260728042601F",
        report_week="2026 Report Week 30",
        numeric_values=(
            4_406_588,
            107_074,
            588_944,
            28_111,
            2_425_700,
            580_688,
            565_815,
            456_470,
            2_020_764,
            234_030,
            372_079,
            219_002,
            2_080,
            4_191_359,
            4_239_434,
            215_229,
            167_154,
            71_513,
            18_241,
            -5_883,
            19_695,
            501,
            161,
            62,
            107,
        ),
    ),
)
_REPORT_BY_DATE = {spec.report_date: spec for spec in _REPORT_SPECS}


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0 and data.strip():
            self.parts.append(data)


class CFTCTFFScheduledReleaseAdapter(CFTCCOTAdapter):
    """Validate three UST 2Y TFF rows against five official CFTC artifacts."""

    annual_endpoint = "https://www.cftc.gov/files/dea/history/fut_fin_txt_2026.zip"
    schedule_endpoint = (
        "https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm"
    )
    policy_endpoint = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"
    notes_endpoint = (
        "https://www.cftc.gov/sites/default/files/idc/groups/public/"
        "%40commitmentsoftraders/documents/file/tfmexplanatorynotes.pdf"
    )
    availability_rule = (
        "CFTC's current 2026 release schedule lists the selected unstarred Fridays and states "
        "that COT reports are released at 3:30 p.m. Eastern time using the previous Tuesday's "
        "data. FinReplay maps each selected Tuesday to that official scheduled time and "
        "validates America/New_York. The schedule calls itself tentative and CFTC provides no "
        "row-level actual-publication log, so this is scheduled availability, not independent "
        "confirmation of the actual second of publication."
    )

    def __init__(self, http: SafeHttpClient) -> None:
        super().__init__(http, CFTC_COT_BY_SLUG["tff_futures_only"])
        self.metadata = AdapterMetadata(
            adapter_id="cftc.cot.tff_scheduled_ust2y",
            title="CFTC scheduled TFF UST 2-year open-interest boundary evidence",
            publisher="U.S. Commodity Futures Trading Commission",
            documentation_url=_HTTP_URL_ADAPTER.validate_python(self.policy_endpoint),
            allowed_hosts=("publicreporting.cftc.gov", "www.cftc.gov"),
            authentication=AuthenticationMode.NONE,
            rate_limit_policy=(
                "Issue exactly five sequential, bounded requests: one three-row Socrata query, "
                "one current-year compressed file, the release schedule, the COT policy page, "
                "and the TFF explanatory PDF. No source is crawled or enumerated."
            ),
            pagination_policy=(
                "The API query is fixed to one contract and three report dates with limit 3 and "
                "a deterministic three-key order. The complete 2026 annual Futures Only TFF ZIP "
                "is separately filtered to the same compound keys; missing, duplicate, or extra "
                "selected rows fail closed."
            ),
            availability_rule=self.availability_rule,
            revision_behavior=(
                "CFTC states that historical COT data are not updated once published. All five "
                "retrievals are content-addressed; the growing current-year ZIP is bound by a "
                "stable semantic digest of only the three selected rows. Any change to a pinned "
                "historical value fails closed instead of silently rewriting the scenario."
            ),
            temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
            license_class=LicenseClass.REDISTRIBUTABLE,
            redistribution_note=(
                "CFTC government information is public domain and may be copied or distributed "
                "with appropriate CFTC acknowledgement. Do not imply CFTC endorsement, and "
                "recheck current policy for any separately identified third-party material."
            ),
        )

    def fetch(self) -> AdapterBatch:
        api_response, api_content, api_retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
            params={"$limit": 3, "$where": _API_WHERE, "$order": _API_ORDER},
        )
        annual_response, annual_content, annual_retrieved_at = self.http.get(
            self.annual_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        schedule_response, schedule_content, schedule_retrieved_at = self.http.get(
            self.schedule_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        policy_response, policy_content, policy_retrieved_at = self.http.get(
            self.policy_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        notes_response, notes_content, notes_retrieved_at = self.http.get(
            self.notes_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )

        self._validate_api_url(api_response.request_url)
        self._validate_plain_url(annual_response.request_url, self.annual_endpoint)
        self._validate_plain_url(schedule_response.request_url, self.schedule_endpoint)
        self._validate_plain_url(policy_response.request_url, self.policy_endpoint)
        self._validate_plain_url(notes_response.request_url, self.notes_endpoint)

        api_content_type = self._require_content_type(
            api_response.headers,
            {"application/json", "text/json"},
            "API JSON",
        )
        annual_content_type = self._require_content_type(
            annual_response.headers,
            {"application/zip", "application/x-zip-compressed", "application/octet-stream"},
            "annual ZIP",
        )
        schedule_content_type = self._require_content_type(
            schedule_response.headers,
            {"text/html", "application/xhtml+xml"},
            "release-schedule HTML",
        )
        policy_content_type = self._require_content_type(
            policy_response.headers,
            {"text/html", "application/xhtml+xml"},
            "policy HTML",
        )
        notes_content_type = self._require_content_type(
            notes_response.headers,
            {"application/pdf"},
            "explanatory PDF",
        )

        api_rows = self._parse_api(api_response, api_content)
        annual_rows = self._parse_annual_zip(annual_content)
        api_crosscheck = [self._crosscheck_row(row) for row in api_rows]
        if api_crosscheck != annual_rows:
            raise SourceSchemaError("CFTC TFF API and annual compressed rows do not match")

        schedule_text = self._parse_html(schedule_content, "release schedule")
        self._require_markers(schedule_text, _SCHEDULE_MARKERS, "release schedule")
        policy_text = self._parse_html(policy_content, "COT policy")
        self._require_markers(policy_text, _POLICY_MARKERS, "COT policy")
        notes_pages = self._parse_notes_pdf(notes_content)

        api_digest = source_response_sha256(api_content)
        annual_digest = source_response_sha256(annual_content)
        schedule_digest = source_response_sha256(schedule_content)
        policy_digest = source_response_sha256(policy_content)
        notes_digest = source_response_sha256(notes_content)
        annual_semantic_digest = _canonical_sha256(annual_rows)
        schedule_semantic_digest = _canonical_sha256(list(_SCHEDULE_MARKERS))
        policy_semantic_digest = _canonical_sha256(list(_POLICY_MARKERS))
        notes_semantic_digest = _canonical_sha256(list(_NOTES_MARKERS))
        source_version = (
            f"CFTC-TFF:042601:2026-07-14..2026-07-28:api:{api_digest[:20]}:"
            f"annual:{annual_semantic_digest[:16]}:schedule:{schedule_semantic_digest[:16]}:"
            f"policy:{policy_semantic_digest[:16]}:notes:{notes_semantic_digest[:16]}"
        )
        retrieved_at = max(
            api_retrieved_at,
            annual_retrieved_at,
            schedule_retrieved_at,
            policy_retrieved_at,
            notes_retrieved_at,
        )
        records = tuple(
            self._record(
                row,
                retrieved_at=retrieved_at,
                request_url=api_response.request_url,
                api_digest=api_digest,
                annual_semantic_digest=annual_semantic_digest,
                schedule_semantic_digest=schedule_semantic_digest,
                policy_semantic_digest=policy_semantic_digest,
                notes_semantic_digest=notes_semantic_digest,
                notes_pages=len(notes_pages),
                source_version=source_version,
            )
            for row in api_rows
        )
        warnings = (
            "The 3:30 p.m. Eastern timestamp is the exact time stated by CFTC's current 2026 "
            "schedule for the selected unstarred dates; the page calls the schedule tentative "
            "and CFTC exposes no row-level actual-publication log.",
            "The API and growing current-year annual ZIP agree on every selected position, "
            "change, trader-count, unit, mode, contract, and report-date field validated here.",
            "CFTC says historical COT data are not updated once published; pinned values still "
            "fail closed if either official representation changes.",
            "Trader categories reflect predominant business purpose and may change because of "
            "Form 40 reclassification, entry, or exit; they do not prove trading intent.",
            "Open interest is a count of outstanding futures contracts, not volume, executions, "
            "accounts, users, P&L, or an economic-exposure estimate.",
            "The $200,000 contract face-value label is retained as source text and is not "
            "multiplied into notional exposure or interpreted as capital at risk.",
            "No directional position, probability, confidence interval, causal effect, or "
            "forecast is inferred from these observations.",
        )
        response_items = (
            (
                api_response,
                api_content,
                api_retrieved_at,
                api_content_type,
                api_digest,
                3,
            ),
            (
                annual_response,
                annual_content,
                annual_retrieved_at,
                annual_content_type,
                annual_digest,
                0,
            ),
            (
                schedule_response,
                schedule_content,
                schedule_retrieved_at,
                schedule_content_type,
                schedule_digest,
                0,
            ),
            (
                policy_response,
                policy_content,
                policy_retrieved_at,
                policy_content_type,
                policy_digest,
                0,
            ),
            (
                notes_response,
                notes_content,
                notes_retrieved_at,
                notes_content_type,
                notes_digest,
                0,
            ),
        )
        receipts = tuple(
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
                retrieved_at=response_retrieved_at,
                status_code=response.status_code,
                content_type=content_type,
                response_sha256=digest,
                response_bytes=len(content),
                record_count=record_count,
                source_version=source_version,
                temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
                historical_replay_eligible=True,
                warnings=warnings,
            )
            for response, content, response_retrieved_at, content_type, digest, record_count in (
                response_items
            )
        )
        artifacts = tuple(
            RawArtifact(sha256=digest, content_type=content_type, content=content)
            for _response, content, _retrieved_at, content_type, digest, _count in response_items
        )
        return AdapterBatch(records=records, receipts=receipts, artifacts=artifacts)

    def _parse_api(self, response: object, content: bytes) -> list[dict[str, object]]:
        try:
            raw_rows = response.json()  # type: ignore[attr-defined]
        except (TypeError, ValueError) as error:
            raise SourceSchemaError("CFTC TFF API response is not valid JSON") from error
        if not isinstance(raw_rows, list) or len(raw_rows) != len(_REPORT_SPECS):
            raise SourceSchemaError("CFTC TFF API must return exactly three selected rows")
        if json.loads(content) != raw_rows:
            raise SourceSchemaError("CFTC TFF API detached content does not match decoded JSON")
        rows: list[dict[str, object]] = []
        identities: set[str] = set()
        dates: set[date] = set()
        for position, value in enumerate(raw_rows):
            row = self._normalize_row(value, position)
            missing = set(_API_EXTRA_FIELDS) - set(row)
            if missing:
                raise SourceSchemaError(f"CFTC TFF API row is missing fields: {sorted(missing)}")
            self._validate_semantics(row, position)
            report_date = self._report_date(row["report_date_as_yyyy_mm_dd"]).date()
            spec = _REPORT_BY_DATE.get(report_date)
            if spec is None or report_date in dates:
                raise SourceSchemaError("CFTC TFF API report-date identity does not match")
            dates.add(report_date)
            identity = self._text(row, "id", position)
            if identity in identities:
                raise SourceSchemaError("CFTC TFF API contains a duplicate source ID")
            identities.add(identity)
            self._validate_pinned_row(row, position, spec)
            rows.append(row)
        rows.sort(key=lambda item: str(item["report_date_as_yyyy_mm_dd"]))
        if [self._report_date(row["report_date_as_yyyy_mm_dd"]).date() for row in rows] != [
            spec.report_date for spec in _REPORT_SPECS
        ]:
            raise SourceSchemaError("CFTC TFF API selected report dates do not match")
        self._validate_change_sequence(rows)
        return rows

    def _validate_pinned_row(
        self,
        row: dict[str, object],
        position: int,
        spec: _ReportSpec,
    ) -> None:
        if self._text(row, "id", position) != spec.identity:
            raise SourceSchemaError("CFTC TFF API source ID does not match pinned history")
        if self._text(row, "yyyy_report_week_ww", position) != spec.report_week:
            raise SourceSchemaError("CFTC TFF API report-week identity does not match")
        for text_field, expected_text in _TEXT_EXPECTATIONS.items():
            if self._text(row, text_field, position) != expected_text:
                raise SourceSchemaError(f"CFTC TFF API field {text_field} does not match")
        for numeric_field, expected_number in spec.expected_numeric.items():
            actual = _integer(
                row[numeric_field],
                field=numeric_field,
                signed=numeric_field in _SIGNED_FIELDS,
            )
            if actual != expected_number:
                raise SourceSchemaError(
                    f"CFTC TFF API field {numeric_field} changed: {actual} != {expected_number}"
                )
        total_traders = _integer(row["traders_tot_all"], field="traders_tot_all")
        if any(_integer(row[field], field=field) > total_traders for field in _TRADER_FIELDS):
            raise SourceSchemaError("CFTC TFF category trader count exceeds total traders")

    @staticmethod
    def _validate_change_sequence(rows: list[dict[str, object]]) -> None:
        for previous, current in pairwise(rows):
            previous_oi = _integer(previous["open_interest_all"], field="open_interest_all")
            current_oi = _integer(current["open_interest_all"], field="open_interest_all")
            change = _integer(
                current["change_in_open_interest_all"],
                field="change_in_open_interest_all",
                signed=True,
            )
            if current_oi - previous_oi != change:
                raise SourceSchemaError("CFTC TFF reported open-interest change is inconsistent")

    def _parse_annual_zip(self, content: bytes) -> list[dict[str, object]]:
        try:
            with zipfile.ZipFile(BytesIO(content), mode="r", allowZip64=False) as archive:
                infos = archive.infolist()
                if len(infos) != 1 or infos[0].filename != "FinFutYY.txt":
                    raise SourceSchemaError("CFTC annual ZIP member identity does not match")
                info = infos[0]
                if info.is_dir() or info.flag_bits & 0x1:
                    raise SourceSchemaError("CFTC annual ZIP member is invalid or encrypted")
                if info.file_size <= 0 or info.file_size > 10_000_000:
                    raise SourceSchemaError("CFTC annual ZIP member size is outside bounds")
                if info.compress_size <= 0 or info.file_size / info.compress_size > 200:
                    raise SourceSchemaError("CFTC annual ZIP compression ratio is unsafe")
                raw_csv = archive.read(info)
        except SourceSchemaError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            raise SourceSchemaError("CFTC annual ZIP could not be parsed") from error
        try:
            decoded = raw_csv.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("CFTC annual TFF file is not valid UTF-8") from error
        reader = csv.DictReader(StringIO(decoded, newline=""))
        headers = reader.fieldnames
        if headers is None or len(headers) != len(set(headers)):
            raise SourceSchemaError("CFTC annual TFF CSV headers are missing or duplicated")
        missing = set(_ANNUAL_TO_API) - set(headers)
        if missing:
            raise SourceSchemaError(f"CFTC annual TFF CSV is missing fields: {sorted(missing)}")
        selected: list[dict[str, object]] = []
        keys: set[tuple[str, str]] = set()
        for source_row in reader:
            source = require_json_object(source_row, "CFTC annual TFF row")
            code = _required_text(source.get("CFTC_Contract_Market_Code"), "annual contract")
            report_date = _required_text(source.get("Report_Date_as_YYYY-MM-DD"), "annual date")
            if code != _CONTRACT_CODE or report_date not in {
                spec.report_date.isoformat() for spec in _REPORT_SPECS
            }:
                continue
            key = (code, report_date)
            if key in keys:
                raise SourceSchemaError("CFTC annual TFF file has duplicate selected rows")
            keys.add(key)
            normalized: dict[str, object] = {}
            for annual_field, api_field in _ANNUAL_TO_API.items():
                value = source.get(annual_field)
                if api_field == "report_date_as_yyyy_mm_dd":
                    normalized[api_field] = _required_text(value, annual_field)
                elif api_field in _NUMERIC_FIELDS:
                    normalized[api_field] = _integer(
                        value,
                        field=annual_field,
                        signed=api_field in _SIGNED_FIELDS,
                    )
                else:
                    normalized[api_field] = _required_text(value, annual_field)
            selected.append(normalized)
        selected.sort(key=lambda item: str(item["report_date_as_yyyy_mm_dd"]))
        if len(selected) != len(_REPORT_SPECS):
            raise SourceSchemaError("CFTC annual TFF file lacks exactly three selected rows")
        return selected

    @staticmethod
    def _crosscheck_row(row: dict[str, object]) -> dict[str, object]:
        normalized: dict[str, object] = {}
        for api_field in _ANNUAL_TO_API.values():
            if api_field in normalized:
                continue
            value = row[api_field]
            if api_field == "report_date_as_yyyy_mm_dd":
                normalized[api_field] = CFTCCOTAdapter._report_date(value).date().isoformat()
            elif api_field in _NUMERIC_FIELDS:
                normalized[api_field] = _integer(
                    value,
                    field=api_field,
                    signed=api_field in _SIGNED_FIELDS,
                )
            else:
                normalized[api_field] = _required_text(value, api_field)
        return normalized

    @staticmethod
    def _parse_html(content: bytes, label: str) -> str:
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceSchemaError(f"CFTC {label} is not valid UTF-8") from error
        parser = _VisibleTextParser()
        try:
            parser.feed(decoded)
            parser.close()
        except (AssertionError, ValueError) as error:
            raise SourceSchemaError(f"CFTC {label} HTML is structurally invalid") from error
        text = _normalize_text(" ".join(parser.parts))
        if not text:
            raise SourceSchemaError(f"CFTC {label} HTML has no visible text")
        return text

    @staticmethod
    def _require_markers(text: str, markers: tuple[str, ...], label: str) -> None:
        missing = [marker for marker in markers if marker not in text]
        if missing:
            raise SourceSchemaError(f"CFTC {label} semantic markers do not match")

    @staticmethod
    def _parse_notes_pdf(content: bytes) -> tuple[str, ...]:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("CFTC TFF explanatory notes are not a PDF")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 4:
                raise SourceSchemaError("CFTC TFF explanatory PDF page count does not match")
            pages: list[str] = []
            for page in reader.pages:
                geometry = (
                    round(float(page.mediabox.width), 2),
                    round(float(page.mediabox.height), 2),
                )
                if geometry != (612.0, 792.0) or page.rotation != 0:
                    raise SourceSchemaError("CFTC TFF explanatory PDF geometry does not match")
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError("CFTC TFF explanatory PDF has a blank text page")
                pages.append(_normalize_text(extracted))
        except SourceSchemaError:
            raise
        except (OSError, TypeError, ValueError, PdfReadError) as error:
            raise SourceSchemaError("CFTC TFF explanatory PDF could not be parsed") from error
        joined = _normalize_text(" ".join(pages))
        CFTCTFFScheduledReleaseAdapter._require_markers(joined, _NOTES_MARKERS, "TFF notes")
        return tuple(pages)

    def _record(
        self,
        row: dict[str, object],
        *,
        retrieved_at: datetime,
        request_url: str,
        api_digest: str,
        annual_semantic_digest: str,
        schedule_semantic_digest: str,
        policy_semantic_digest: str,
        notes_semantic_digest: str,
        notes_pages: int,
        source_version: str,
    ) -> BitemporalRecord:
        report_date = self._report_date(row["report_date_as_yyyy_mm_dd"]).date()
        spec = _REPORT_BY_DATE[report_date]
        release_at = _scheduled_release_at(spec)
        source_snapshot_through = max(_scheduled_release_at(item) for item in _REPORT_SPECS)
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=api_digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
            vintage_as_of=source_snapshot_through,
            redistribution_note=self.metadata.redistribution_note,
        )
        numeric = {
            field: _integer(row[field], field=field, signed=field in _SIGNED_FIELDS)
            for field in _NUMERIC_FIELDS
        }
        return BitemporalRecord(
            record_id=f"{self.metadata.adapter_id}:{spec.identity}",
            entity_id=f"cftc_contract:{_CONTRACT_CODE}",
            source=source,
            interval=BitemporalInterval(
                valid_from=datetime.combine(report_date, time.min, tzinfo=UTC),
                published_at=release_at,
                available_at=release_at,
                ingested_at=retrieved_at,
                availability_rule=self.availability_rule,
                availability_confidence=0.98,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload={
                "report_date": report_date.isoformat(),
                "report_week": spec.report_week,
                "source_row_id": spec.identity,
                "official_scheduled_release_date": spec.release_date.isoformat(),
                "official_scheduled_release_time_local": "15:30:00",
                "official_scheduled_release_timezone": "America/New_York",
                "official_scheduled_release_timezone_abbreviation": "EDT",
                "official_scheduled_release_at": release_at.isoformat(),
                "source_snapshot_through_scheduled_release_at": (
                    source_snapshot_through.isoformat()
                ),
                "actual_row_publication_log_available": False,
                "schedule_self_describes_as_tentative": True,
                "contract_market_name": _TEXT_EXPECTATIONS["contract_market_name"],
                "market_and_exchange_names": _TEXT_EXPECTATIONS["market_and_exchange_names"],
                "cftc_contract_market_code": _CONTRACT_CODE,
                "cftc_market_code": _TEXT_EXPECTATIONS["cftc_market_code"],
                "commodity_name": _TEXT_EXPECTATIONS["commodity_name"],
                "commodity_group_name": _TEXT_EXPECTATIONS["commodity_group_name"],
                "commodity_subgroup_name": _TEXT_EXPECTATIONS["commodity_subgroup_name"],
                "report_mode": "FutOnly",
                "metric": "open_interest_all_futures_only",
                "open_interest_contracts": numeric["open_interest_all"],
                "reported_change_from_prior_week_contracts": numeric["change_in_open_interest_all"],
                "position_breakdown_contracts": {
                    field: numeric[field] for field in _POSITION_FIELDS[1:]
                },
                "asset_manager_weekly_changes_contracts": {
                    "long": numeric["change_in_asset_mgr_long"],
                    "short": numeric["change_in_asset_mgr_short"],
                    "spreading": numeric["change_in_asset_mgr_spread"],
                },
                "trader_counts": {field: numeric[field] for field in _TRADER_FIELDS},
                "contract_units_source_text": _TEXT_EXPECTATIONS["contract_units"],
                "contract_face_value_notional_conversion_performed": False,
                "api_annual_crosscheck_verified": True,
                "historical_rows_pinned": True,
                "cftc_historical_data_not_updated_statement_present": True,
                "classification_and_intent_caveats_validated": True,
                "api_response_sha256": api_digest,
                "annual_selected_rows_semantic_sha256": annual_semantic_digest,
                "schedule_semantic_sha256": schedule_semantic_digest,
                "policy_semantic_sha256": policy_semantic_digest,
                "tff_notes_semantic_sha256": notes_semantic_digest,
                "tff_notes_pdf_pages": notes_pages,
                "availability_method": "official_current_schedule_exact_time_no_actual_row_log",
                "unit": "Futures Contracts",
            },
        )

    @staticmethod
    def _require_content_type(
        headers: Mapping[str, str],
        allowed: set[str],
        label: str,
    ) -> str:
        content_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in allowed:
            raise SourceSchemaError(f"unexpected CFTC {label} content type: {content_type!r}")
        return content_type

    @staticmethod
    def _validate_plain_url(actual: str, expected: str) -> None:
        parsed = urlparse(actual)
        target = urlparse(expected)
        if (
            parsed.scheme != "https"
            or parsed.hostname != target.hostname
            or parsed.path != target.path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("CFTC supporting response URL does not match request")

    def _validate_api_url(self, actual: str) -> None:
        parsed = urlparse(actual)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "publicreporting.cftc.gov"
            or parsed.path != f"/resource/{self.spec.view_id}.json"
            or parsed.params
            or parsed.fragment
        ):
            raise SourceSchemaError("CFTC TFF API response URL does not match request")
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        expected = {"$limit": ["3"], "$where": [_API_WHERE], "$order": [_API_ORDER]}
        if query != expected:
            raise SourceSchemaError("CFTC TFF API response query does not match request")


def _scheduled_release_at(spec: _ReportSpec) -> datetime:
    if spec.report_date.weekday() != 1 or spec.release_date.weekday() != 4:
        raise SourceSchemaError("CFTC selected report/release weekdays do not match")
    if (spec.release_date - spec.report_date).days != 3:
        raise SourceSchemaError("CFTC selected report/release date gap does not match")
    release_local = datetime.combine(spec.release_date, time(15, 30), tzinfo=_NEW_YORK)
    if release_local.tzname() != "EDT":
        raise SourceSchemaError("CFTC selected release timezone is not EDT")
    return release_local.astimezone(UTC)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceSchemaError(f"CFTC field {field} must be non-empty text")
    return value.strip()


def _integer(value: object, *, field: str, signed: bool = False) -> int:
    if isinstance(value, bool):
        raise SourceSchemaError(f"CFTC field {field} must be an integer")
    text = str(value).strip()
    if not text or (text[0] in "+-" and not signed):
        raise SourceSchemaError(f"CFTC field {field} must be a non-negative integer")
    digits = text[1:] if text[0] in "+-" else text
    if not digits.isascii() or not digits.isdigit():
        raise SourceSchemaError(f"CFTC field {field} must be an integer")
    parsed = int(text)
    if not signed and parsed < 0:
        raise SourceSchemaError(f"CFTC field {field} must be non-negative")
    return parsed


def _normalize_text(value: str) -> str:
    translation: dict[int, str] = {
        ord("\N{RIGHT SINGLE QUOTATION MARK}"): "'",
        ord("\N{LEFT SINGLE QUOTATION MARK}"): "'",
        ord("\N{LEFT DOUBLE QUOTATION MARK}"): '"',
        ord("\N{RIGHT DOUBLE QUOTATION MARK}"): '"',
        ord("\N{EN DASH}"): "-",
        ord("\N{EM DASH}"): "-",
    }
    translated = unicodedata.normalize("NFKC", value).translate(translation)
    return " ".join(translated.split())


def _canonical_sha256(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
