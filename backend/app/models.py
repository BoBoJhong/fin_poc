from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceType(StrEnum):
    FINANCIAL_REPORT = "financial_report"
    TRANSCRIPT = "transcript"
    URL = "url"
    DATABASE = "database"
    GRAPH = "graph"


class SourceLocator(BaseModel):
    page: int | None = None
    paragraph_id: str | None = None
    timestamp: str | None = None
    table: str | None = None
    primary_key: str | None = None
    columns: list[str] = Field(default_factory=list)
    graph_path: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    evidence_id: str
    co_code: str
    source_id: str
    source_type: SourceType
    title: str
    content: str
    score: float = Field(ge=0, le=1)
    period: str | None = None
    locator: SourceLocator = Field(default_factory=SourceLocator)
    captured_at: str | None = None
    content_hash: str | None = None
    data_version: str = "demo-v1"
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    index: int
    evidence_id: str
    co_code: str
    source_id: str
    title: str
    source_type: SourceType
    locator: SourceLocator
    quoted_text: str
    period: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    # Optional for backwards compatibility. New clients identify the company in query.
    co_code: str | None = Field(default=None, min_length=2, max_length=32)
    conversation_id: str | None = None


class CompanySummary(BaseModel):
    co_code: str
    company_name: str
    industry: str | None = None
    aliases: list[str] = Field(default_factory=list)


class CompanyCandidate(BaseModel):
    company: CompanySummary
    score: float = Field(ge=0, le=1)
    match_method: str
    matched_term: str | None = None


class FiscalCalendar(BaseModel):
    co_code: str
    fiscal_year_end_month: int = Field(ge=1, le=12)
    timezone: str = "UTC"
    source: str = "company_master"


class PeriodResolution(BaseModel):
    input: str | None = None
    resolved_period: str | None = None
    period_type: str = "fiscal_quarter"
    as_of: str
    method: str
    confidence: float = Field(ge=0, le=1)
    available_periods: list[str] = Field(default_factory=list)
    fiscal_calendar: FiscalCalendar | None = None


class TranscriptSpeaker(BaseModel):
    name: str
    title: str | None = None


class TranscriptConversationTurn(BaseModel):
    speaker: TranscriptSpeaker
    content: str


class TranscriptConversationPage(BaseModel):
    company_code: str
    period: str
    quarter: str
    event_date: str | None = None
    conversations: list[TranscriptConversationTurn] = Field(default_factory=list)
    next_cursor: int | None = Field(default=None, ge=0)
    source_id: str
    source_url: str | None = None


class EarningsCallRecord(BaseModel):
    company_code: str
    period: str
    quarter: str
    event_date: str | None = None
    source_id: str


class ChatResponse(BaseModel):
    answer: str
    co_code: str
    citations: list[Citation]
    trace_id: str
    routes: list[str]
    verification: dict[str, Any]
    data_versions: list[str]
    period_resolution: PeriodResolution | None = None


class SourcePreview(BaseModel):
    source_id: str
    co_code: str
    source_type: SourceType
    title: str
    snapshot_html: str | None = None
    live_url: str | None = None
    text: str | None = None
    locator: SourceLocator = Field(default_factory=SourceLocator)
    captured_at: str | None = None
    content_hash: str | None = None
    database_record: dict[str, Any] | None = None
    graph: dict[str, Any] | None = None


class ToolEnvelope(BaseModel):
    evidence: list[Evidence] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
