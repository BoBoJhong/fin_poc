from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import ChatResponse, PeriodResolution, SourceLocator


HTTP_SCHEMA_VERSION = "1.1"


class HttpCitation(BaseModel):
    index: int
    evidence_id: str
    co_code: str
    source_id: str
    title: str
    source_type: str
    locator: SourceLocator
    quoted_text: str
    period: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    live_url: str | None = None
    content_hash: str | None = None
    captured_at: str | None = None


class HttpTranscriptDisplaySource(BaseModel):
    citation_index: int
    speaker: str | None = None
    section: str | None = None
    source_content: str
    source_url: str | None = None
    locator: SourceLocator
    content_hash: str | None = None


class HttpTranscriptDisplay(BaseModel):
    title: str
    period: str | None = None
    speakers: list[str] = Field(default_factory=list)
    content: str
    sources: list[HttpTranscriptDisplaySource] = Field(default_factory=list)


class HttpRAGResponse(BaseModel):
    schema_version: Literal["1.1"]
    status: Literal["answered", "refused", "needs_clarification"]
    answer: str
    co_code: str | None
    display: HttpTranscriptDisplay | None
    citations: list[HttpCitation]
    routes: list[str]
    trace_id: str | None
    verification: dict[str, Any]
    verified: bool
    confidence: float = Field(ge=0, le=1)
    verification_notes: list[str]
    warnings: list[str]
    data_versions: list[str]
    latency_ms: float = Field(ge=0)
    clarification_question: str | None
    period_resolution: PeriodResolution | None


def response_confidence(result: ChatResponse) -> float:
    if result.verification.get("passed") is not True:
        return 0.0
    lowest = result.verification.get("reliability_policy", {}).get("lowest_evidence_score")
    return max(0.0, min(float(lowest if lowest is not None else 1.0), 1.0))
