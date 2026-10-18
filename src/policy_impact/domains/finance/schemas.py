"""Contracts shared by acquisition, reports, and report follow-up."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


SessionMode = Literal["explicit_period", "latest_completed"]
QualityPolicy = Literal["strict", "allow_partial"]


class FinanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Period(FinanceModel):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("period end must not precede start")
        return self


class MarketBar(FinanceModel):
    date: date
    open: float | None = Field(default=None, gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def prices(self):
        prices = [self.close] + ([self.open] if self.open is not None else [])
        if self.high < max(*prices, self.low) or self.low > min(prices):
            raise ValueError("OHLC prices are inconsistent")
        return self


class FXConversion(FinanceModel):
    """Optional recorded conversion, never inferred from the display currency."""

    from_currency: str = Field(pattern=r"^[A-Z]{3}$")
    to_currency: str = Field(pattern=r"^[A-Z]{3}$")
    rate: float = Field(gt=0)
    as_of: AwareDatetime
    source_url: str = Field(min_length=1)


class MarketSeries(FinanceModel):
    symbol: str
    name: str
    provider: str
    unit: str = "点"
    timezone: str = "Asia/Shanghai"
    price_basis: str = "price_index"
    source_url: str
    retrieved_at: datetime
    bars: list[MarketBar]
    asset_class: str | None = None
    quote_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    calendar_id: str | None = None
    session_date: date | None = None
    expected_session_date: date | None = None
    expected_previous_session_date: date | None = None
    session_close_at: AwareDatetime | None = None
    session_date_basis: str = "unverified"
    session_verified: bool = False
    session_evidence: dict[str, Any] = Field(default_factory=dict)
    daily_cutoff_basis: str | None = None
    fx_conversion: FXConversion | None = None

    @model_validator(mode="after")
    def session_metadata(self):
        if self.session_date is not None and not any(bar.date == self.session_date for bar in self.bars):
            raise ValueError("session_date must refer to an actual market bar")
        if (self.expected_previous_session_date is not None and self.expected_session_date is not None
                and self.expected_previous_session_date >= self.expected_session_date):
            raise ValueError("expected previous session must precede expected session")
        if self.session_verified and (
            self.session_date is None or self.expected_session_date is None
            or self.session_close_at is None or self.session_date_basis == "unverified"
        ):
            raise ValueError("verified session needs actual/expected dates, close time and basis")
        return self


class SourceDocument(FinanceModel):
    document_id: str
    title: str
    url: str
    source_id: str
    publisher: str
    kind: Literal["macro_release", "institution_view", "policy_release"]
    published_at: date
    retrieved_at: datetime
    text: str
    content_hash: str


class SourceStatus(FinanceModel):
    source_id: str
    status: Literal["ok", "partial", "empty", "error", "disabled"]
    count: int = Field(ge=0)
    detail: str = ""
    latest_date: str = ""
    elapsed_ms: float = Field(default=0, ge=0)


class FinanceSnapshot(FinanceModel):
    schema_version: Literal["finance.snapshot.v1"] = "finance.snapshot.v1"
    snapshot_id: str
    collected_at: datetime
    period: Period
    series: list[MarketSeries] = Field(default_factory=list)
    documents: list[SourceDocument] = Field(default_factory=list)
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    session_mode: SessionMode = "explicit_period"
    document_period: Period | None = None
    as_of: AwareDatetime | None = None
    quality_policy: QualityPolicy = "allow_partial"


class EvidenceQuote(FinanceModel):
    evidence_ref: str
    quote: str = Field(min_length=8, max_length=320)


class ReportSection(FinanceModel):
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=2500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    evidence_quotes: list[EvidenceQuote] = Field(default_factory=list, max_length=8)


class DocumentUse(FinanceModel):
    document_id: str
    usage: Literal["historical_context", "retrospective_review"]


Mechanism = Literal["rates_discounting", "growth_earnings", "dollar_channel", "metals_carry", "supply_demand", "other", "unidentified"]


class HypothesisTest(FinanceModel):
    mechanism: Mechanism
    window: Period
    variables: list[str] = Field(min_length=1, max_length=5)
    weakening_condition: str = Field(min_length=8, max_length=400)


class AssetAssessment(FinanceModel):
    symbol: str
    verdict: Literal["unidentified", "hypothesis_only"]
    mechanism: Mechanism = "unidentified"
    hypothesis: str = Field(min_length=1, max_length=400)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)
    document_uses: list[DocumentUse] = Field(default_factory=list, max_length=8)
    counterevidence: str = Field(min_length=1, max_length=400)
    counter_observation_dates: list[date] = Field(default_factory=list, max_length=3)
    missing_evidence: list[str] = Field(min_length=1, max_length=5)
    falsification_test: str = Field(min_length=1, max_length=400)
    historical_test: HypothesisTest | None = None


class FinanceAnalysis(FinanceModel):
    overview: str = Field(min_length=1, max_length=1800)
    sections: list[ReportSection] = Field(min_length=1, max_length=8)
    watch_next: list[str] = Field(default_factory=list, max_length=6)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    asset_assessments: list[AssetAssessment] = Field(default_factory=list, max_length=8)


class FinanceVerification(FinanceModel):
    supported: bool
    issues: list[str] = Field(default_factory=list, max_length=8)
