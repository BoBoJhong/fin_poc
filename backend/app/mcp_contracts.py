from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import Evidence, SourceLocator, TranscriptConversationTurn


MCP_SCHEMA_VERSION = "2.0"
MCP_TOOL_CONTRACT_VERSION = "2.0"
MCPStatus = Literal["answered", "refused", "needs_clarification"]
RetrievalStatus = Literal["retrieved", "refused", "needs_clarification"]


class PublicCitation(BaseModel):
    index: int
    source_id: str
    source_type: str
    excerpt: str
    period: str | None
    locator: SourceLocator
    source_url: str | None
    content_hash: str | None
    data_version: str | None
    speaker: str | None


def compact_citation(item: Any, preview: Any | None) -> PublicCitation:
    return PublicCitation(
        index=item.index,
        source_id=item.source_id,
        source_type=str(item.source_type),
        excerpt=item.quoted_text,
        period=item.period,
        locator=item.locator,
        source_url=preview.live_url if preview else None,
        content_hash=preview.content_hash if preview else None,
        data_version=(
            str(item.metadata["data_version"])
            if item.metadata.get("data_version") is not None
            else None
        ),
        speaker=(
            str(item.metadata["speaker"]) if item.metadata.get("speaker") is not None else None
        ),
    )


def response_period(result: Any) -> str | None:
    resolution = result.period_resolution
    return resolution.resolved_period if resolution is not None else None


class PublicEvidenceItem(BaseModel):
    source_id: str
    source_type: str
    title: str
    content: str
    score: float = Field(ge=0, le=1)
    period: str | None
    locator: SourceLocator
    content_hash: str | None
    data_version: str
    speaker: str | None
    speakers: list[str]


def compact_evidence(item: Evidence) -> PublicEvidenceItem:
    speakers = [str(value) for value in item.metadata.get("speakers", [])]
    speaker = str(item.metadata["speaker"]) if item.metadata.get("speaker") else None
    if speaker and not speakers:
        speakers = [speaker]
    return PublicEvidenceItem(
        source_id=item.source_id,
        source_type=str(item.source_type),
        title=item.title,
        content=item.content,
        score=item.score,
        period=item.period,
        locator=item.locator,
        content_hash=item.content_hash,
        data_version=item.data_version,
        speaker=speaker,
        speakers=speakers,
    )


class VerifiedRAGResponse(BaseModel):
    schema_version: Literal["2.0"]
    status: MCPStatus
    answer: str
    company_code: str | None
    period: str | None
    citations: list[PublicCitation]
    warnings: list[str]
    clarification_question: str | None


class EvidenceToolResponse(BaseModel):
    schema_version: Literal["2.0"]
    status: RetrievalStatus
    company_code: str | None
    period: str | None
    items: list[PublicEvidenceItem]
    warnings: list[str]
    clarification_question: str | None


class TranscriptBlockContent(BaseModel):
    text: str
    paragraph_id: str | None
    source_id: str
    content_hash: str | None
    source_url: str | None


class TranscriptBlockItem(BaseModel):
    period: str | None
    speaker: str | None
    speakers: list[str]
    title: str
    score: float = Field(ge=0, le=1)
    content: TranscriptBlockContent


class TranscriptBlockResponse(BaseModel):
    schema_version: Literal["2.0"]
    status: RetrievalStatus
    company_code: str | None
    period: str | None
    items: list[TranscriptBlockItem]
    warnings: list[str]
    clarification_question: str | None


class TranscriptConversationResponse(BaseModel):
    schema_version: Literal["2.0"]
    status: RetrievalStatus
    company_code: str | None
    period: str | None
    quarter: str | None
    conversations: list[TranscriptConversationTurn]
    next_cursor: int | None = Field(ge=0)
    source_id: str | None
    source_url: str | None
    warnings: list[str]
    clarification_question: str | None


class PublicEarningsCall(BaseModel):
    period: str
    quarter: str
    event_date: str | None
    source_id: str


class EarningsCallListResponse(BaseModel):
    schema_version: Literal["2.0"]
    status: RetrievalStatus
    company_code: str | None
    earnings_calls: list[PublicEarningsCall]
    warnings: list[str]
    clarification_question: str | None


class MultiPeriodEarningsCallGroup(BaseModel):
    quarter: str
    period: str
    event_date: str | None
    source_id: str
    coverage_mode: Literal["topic_retrieval", "broad_facet_retrieval"]
    items: list[PublicEvidenceItem]


class MultiPeriodEarningsCallResponse(BaseModel):
    schema_version: Literal["2.0"]
    status: RetrievalStatus
    company_code: str | None
    quarters: list[MultiPeriodEarningsCallGroup]
    warnings: list[str]
    clarification_question: str | None


def clarification_response(message: str, _latency_ms: float = 0.0) -> VerifiedRAGResponse:
    return VerifiedRAGResponse(
        schema_version=MCP_SCHEMA_VERSION,
        status="needs_clarification",
        answer="",
        company_code=None,
        period=None,
        citations=[],
        warnings=[],
        clarification_question=message,
    )
