from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import (
    ChatResponse,
    EarningsCallRecord,
    Evidence,
    PeriodResolution,
    SourceLocator,
    TranscriptConversationTurn,
)


MCP_SCHEMA_VERSION = "1.1"
MCP_TOOL_CONTRACT_VERSION = "2.0"
MCPStatus = Literal["answered", "refused", "needs_clarification"]


class VerifiedCitation(BaseModel):
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


class TranscriptDisplaySource(BaseModel):
    citation_index: int
    speaker: str | None = None
    section: str | None = None
    source_content: str
    source_url: str | None = None
    locator: SourceLocator
    content_hash: str | None = None


class TranscriptDisplay(BaseModel):
    title: str
    period: str | None = None
    speakers: list[str] = Field(default_factory=list)
    content: str
    sources: list[TranscriptDisplaySource] = Field(default_factory=list)


class TranscriptBlockContent(BaseModel):
    section: str | None = None
    text: str
    paragraph_id: str | None = None
    source_id: str
    content_hash: str | None = None
    source_url: str | None = None


class TranscriptBlockItem(BaseModel):
    period: str | None = None
    fiscal_label: str | None = None
    speaker: str | None = None
    speakers: list[str] = Field(default_factory=list)
    title: str
    score: float = Field(ge=0, le=1)
    content: TranscriptBlockContent


class TranscriptBlockResponse(BaseModel):
    schema_version: Literal["1.1"]
    status: Literal["retrieved", "refused", "needs_clarification"]
    co_code: str | None
    period: str | None
    items: list[TranscriptBlockItem] = Field(default_factory=list)
    verified: bool
    warnings: list[str] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)
    clarification_question: str | None = None


class TranscriptConversationResponse(BaseModel):
    status: Literal["retrieved", "refused", "needs_clarification"]
    company_code: str | None = None
    quarter: str | None = None
    conversations: list[TranscriptConversationTurn] = Field(default_factory=list)
    next_cursor: int | None = Field(default=None, ge=0)
    message: str | None = None


class EarningsCallListResponse(BaseModel):
    status: Literal["retrieved", "refused", "needs_clarification"]
    company_code: str | None = None
    earnings_calls: list[EarningsCallRecord] = Field(default_factory=list)
    message: str | None = None


class MultiPeriodEarningsCallGroup(BaseModel):
    quarter: str
    period: str
    event_date: str | None = None
    source_id: str
    coverage_mode: Literal["topic_retrieval", "broad_facet_retrieval"]
    coverage_queries: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class MultiPeriodEarningsCallResponse(BaseModel):
    status: Literal["retrieved", "refused", "needs_clarification"]
    company_code: str | None = None
    quarters: list[MultiPeriodEarningsCallGroup] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: str | None = None


class VerifiedRAGResponse(BaseModel):
    schema_version: Literal["1.1"]
    status: MCPStatus
    answer: str
    co_code: str | None
    display: TranscriptDisplay | None
    citations: list[VerifiedCitation]
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


class EvidenceToolResponse(BaseModel):
    schema_version: Literal["1.1"]
    status: Literal["retrieved", "refused", "needs_clarification"]
    co_code: str | None
    period: str | None
    evidence: list[Evidence]
    verified: bool
    verification: dict[str, Any]
    warnings: list[str]
    latency_ms: float = Field(ge=0)
    clarification_question: str | None
    period_resolution: PeriodResolution | None


def clarification_response(message: str, latency_ms: float) -> VerifiedRAGResponse:
    return VerifiedRAGResponse(
        schema_version=MCP_SCHEMA_VERSION,
        status="needs_clarification",
        answer="",
        co_code=None,
        display=None,
        citations=[],
        routes=[],
        trace_id=None,
        verification={"passed": False, "reason": "needs_clarification"},
        verified=False,
        confidence=0.0,
        verification_notes=[message],
        warnings=[],
        data_versions=[],
        latency_ms=latency_ms,
        clarification_question=message,
        period_resolution=None,
    )


def response_confidence(result: ChatResponse) -> float:
    if result.verification.get("passed") is not True:
        return 0.0
    lowest = result.verification.get("reliability_policy", {}).get("lowest_evidence_score")
    return max(0.0, min(float(lowest if lowest is not None else 1.0), 1.0))


def build_transcript_display(
    result: ChatResponse, citations: list[VerifiedCitation]
) -> TranscriptDisplay | None:
    if result.verification.get("passed") is not True or not citations:
        return None
    periods = list(dict.fromkeys(item.period for item in citations if item.period))
    period = periods[0] if len(periods) == 1 else None
    speakers = list(
        dict.fromkeys(
            str(speaker)
            for item in citations
            for speaker in (
                item.metadata.get("speakers")
                or ([item.metadata["speaker"]] if item.metadata.get("speaker") else [])
            )
        )
    )
    title_period = period or "多期間"
    return TranscriptDisplay(
        title=f"{result.co_code} {title_period} 法說會",
        period=period,
        speakers=speakers,
        content=result.answer,
        sources=[
            TranscriptDisplaySource(
                citation_index=item.index,
                speaker=(str(item.metadata["speaker"]) if item.metadata.get("speaker") else None),
                section=(str(item.metadata["section"]) if item.metadata.get("section") else None),
                source_content=item.quoted_text,
                source_url=item.live_url,
                locator=item.locator,
                content_hash=item.content_hash,
            )
            for item in citations
        ],
    )
