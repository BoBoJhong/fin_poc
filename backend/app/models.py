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


class ChatRequest(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    co_code: str = Field(min_length=2, max_length=32)
    conversation_id: str | None = None


class CompanySummary(BaseModel):
    co_code: str
    company_name: str
    industry: str | None = None
    aliases: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    co_code: str
    citations: list[Citation]
    trace_id: str
    routes: list[str]
    verification: dict[str, Any]
    data_versions: list[str]


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
