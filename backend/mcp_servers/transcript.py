from __future__ import annotations

import asyncio
import time
from typing import Any

from fastmcp import FastMCP

from app.agents import FinancialAgentService
from app.config import Settings, get_settings
from app.llm import CompanyLLMClient
from app.mcp_auth import build_mcp_auth
from app.mcp_gateway import MCPGateway
from app.mcp_contracts import (
    MCP_SCHEMA_VERSION,
    MCP_TOOL_CONTRACT_VERSION,
    EarningsCallListResponse,
    EvidenceToolResponse,
    MultiPeriodEarningsCallGroup,
    MultiPeriodEarningsCallResponse,
    PublicEarningsCall,
    TranscriptBlockContent,
    TranscriptBlockItem,
    TranscriptBlockResponse,
    TranscriptConversationResponse,
    VerifiedRAGResponse,
    clarification_response,
    compact_citation,
    compact_evidence,
    response_period,
)
from app.validation import EvidenceValidationError, EvidenceValidator


def build_service(settings: Settings) -> FinancialAgentService:
    return FinancialAgentService(
        gateway=MCPGateway(settings),
        llm=CompanyLLMClient(settings),
        validator=EvidenceValidator.from_settings(settings),
        max_evidence_items=settings.max_evidence_items,
        retrieval_profile="transcript",
    )


def create_transcript_mcp(
    settings: Settings | None = None,
    service: FinancialAgentService | None = None,
) -> FastMCP:
    resolved_settings = settings or get_settings()
    resolved_service = service or build_service(resolved_settings)
    server = FastMCP(
        "Verified Earnings Call RAG",
        version=MCP_TOOL_CONTRACT_VERSION,
        mask_error_details=True,
        strict_input_validation=True,
        auth=build_mcp_auth(resolved_settings),
        instructions=(
            "Every tool call must use a self-contained natural-language query containing the "
            "company name or ticker; rewrite conversational follow-ups before calling. "
            "Use ask_earnings_call for questions that require a supported answer. Use "
            "list_earnings_calls before resolving requests such as recent quarters. Use "
            "retrieve_multi_period_earnings_call_evidence for quarter-by-quarter comparison. Use "
            "get_earnings_call_transcript when the user asks to read the latest or a specific "
            "call; it returns ordered speaker turns instead of vector Top-K. For several full "
            "transcripts, list calls first, then invoke get_earnings_call_transcript once per "
            "quarter and follow each next_cursor until null. Transcript tools "
            "never supplement missing content with financial databases or model memory."
        ),
    )

    @server.tool(output_schema=VerifiedRAGResponse.model_json_schema())
    async def ask_earnings_call(query: str) -> dict[str, Any]:
        """Answer one-company earnings-call questions using transcript evidence only."""
        started = time.perf_counter()
        try:
            result = await resolved_service.answer(query)
        except (EvidenceValidationError, ValueError) as exc:
            return clarification_response(
                str(exc), (time.perf_counter() - started) * 1000
            ).model_dump(mode="json")

        source_ids = list(dict.fromkeys(item.source_id for item in result.citations))
        previews = await asyncio.gather(
            *(
                resolved_service.gateway.get_source_preview(source_id, result.co_code)
                for source_id in source_ids
            )
        )
        provenance = {
            source_id: preview
            for source_id, preview in zip(source_ids, previews, strict=True)
            if preview is not None
        }
        citations = [
            compact_citation(item, provenance.get(item.source_id)) for item in result.citations
        ]

        passed = result.verification.get("passed") is True and bool(citations)
        response = VerifiedRAGResponse(
            schema_version=MCP_SCHEMA_VERSION,
            status="answered" if passed else "refused",
            answer=result.answer,
            company_code=result.co_code,
            period=response_period(result),
            citations=citations,
            warnings=[] if passed else ["Transcript evidence did not pass all verification gates."],
            clarification_question=None,
        )
        return response.model_dump(mode="json")

    @server.tool(output_schema=EarningsCallListResponse.model_json_schema())
    async def list_earnings_calls(query: str, limit: int = 10) -> dict[str, Any]:
        """List available calls so an agent never guesses what recent quarters means."""
        try:
            result = await resolved_service.list_earnings_calls(query, limit=limit)
        except (EvidenceValidationError, ValueError) as exc:
            return EarningsCallListResponse(
                schema_version=MCP_SCHEMA_VERSION,
                status="needs_clarification",
                company_code=None,
                earnings_calls=[],
                warnings=[],
                clarification_question=str(exc),
            ).model_dump(mode="json")
        calls = result["calls"]
        return EarningsCallListResponse(
            schema_version=MCP_SCHEMA_VERSION,
            status="retrieved" if calls else "refused",
            company_code=result["co_code"],
            earnings_calls=[
                PublicEarningsCall(
                    period=call.period,
                    quarter=call.quarter,
                    event_date=call.event_date,
                    source_id=call.source_id,
                )
                for call in calls
            ],
            warnings=[] if calls else ["No earnings calls are available for this company."],
            clarification_question=None,
        ).model_dump(mode="json")

    @server.tool(output_schema=MultiPeriodEarningsCallResponse.model_json_schema())
    async def retrieve_multi_period_earnings_call_evidence(
        query: str,
        quarters: list[str] | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        """Retrieve separately grouped evidence for up to four earnings-call quarters."""
        try:
            result = await resolved_service.retrieve_multi_period_transcript_evidence(
                query,
                quarters=quarters,
                limit=limit,
            )
        except (EvidenceValidationError, ValueError) as exc:
            return MultiPeriodEarningsCallResponse(
                schema_version=MCP_SCHEMA_VERSION,
                status="needs_clarification",
                company_code=None,
                quarters=[],
                warnings=[],
                clarification_question=str(exc),
            ).model_dump(mode="json")
        groups = [
            MultiPeriodEarningsCallGroup(
                quarter=group["call"].quarter,
                period=group["call"].period,
                event_date=group["call"].event_date,
                source_id=group["call"].source_id,
                coverage_mode=group["coverage_mode"],
                items=[compact_evidence(item) for item in group["evidence"]],
            )
            for group in result["groups"]
        ]
        return MultiPeriodEarningsCallResponse(
            schema_version=MCP_SCHEMA_VERSION,
            status="retrieved" if groups else "refused",
            company_code=result["co_code"],
            quarters=groups,
            warnings=(
                []
                if groups
                else ["No official earnings-call transcript was found for the requested quarters."]
            ),
            clarification_question=None,
        ).model_dump(mode="json")

    @server.tool(output_schema=TranscriptConversationResponse.model_json_schema())
    async def get_earnings_call_transcript(
        query: str,
        cursor: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Read the latest or specified earnings call as ordered speaker conversations."""
        try:
            result = await resolved_service.retrieve_transcript_conversation(
                query,
                cursor=max(cursor, 0),
                limit=min(max(limit, 1), 50),
            )
        except (EvidenceValidationError, ValueError) as exc:
            return TranscriptConversationResponse(
                schema_version=MCP_SCHEMA_VERSION,
                status="needs_clarification",
                company_code=None,
                period=None,
                quarter=None,
                conversations=[],
                next_cursor=None,
                source_id=None,
                source_url=None,
                warnings=[],
                clarification_question=str(exc),
            ).model_dump(mode="json")
        page = result["page"]
        if page is None:
            return TranscriptConversationResponse(
                schema_version=MCP_SCHEMA_VERSION,
                status="refused",
                company_code=result["co_code"],
                period=result["period_resolution"].get("resolved_period"),
                quarter=None,
                conversations=[],
                next_cursor=None,
                source_id=None,
                source_url=None,
                warnings=["No official earnings-call transcript was found for the requested period."],
                clarification_question=None,
            ).model_dump(mode="json")
        response = TranscriptConversationResponse(
            schema_version=MCP_SCHEMA_VERSION,
            status="retrieved",
            company_code=page.company_code,
            period=page.period,
            quarter=page.quarter,
            conversations=page.conversations,
            next_cursor=page.next_cursor,
            source_id=page.source_id,
            source_url=page.source_url,
            warnings=[],
            clarification_question=None,
        )
        return response.model_dump(mode="json")

    @server.tool(output_schema=EvidenceToolResponse.model_json_schema())
    async def retrieve_earnings_call_evidence(query: str) -> dict[str, Any]:
        """Retrieve validated transcript evidence without generating an answer."""
        try:
            result = await resolved_service.retrieve_evidence(query)
        except (EvidenceValidationError, ValueError) as exc:
            return EvidenceToolResponse(
                schema_version=MCP_SCHEMA_VERSION,
                status="needs_clarification",
                company_code=None,
                period=None,
                items=[],
                warnings=[],
                clarification_question=str(exc),
            ).model_dump(mode="json")
        evidence = result["evidence"]
        response = EvidenceToolResponse(
            schema_version=MCP_SCHEMA_VERSION,
            status="retrieved" if evidence else "refused",
            company_code=result["co_code"],
            period=result["period"],
            items=[compact_evidence(item) for item in evidence],
            warnings=[] if evidence else ["No verified transcript evidence was found."],
            clarification_question=None,
        )
        return response.model_dump(mode="json")

    @server.tool(output_schema=TranscriptBlockResponse.model_json_schema())
    async def retrieve_earnings_call_blocks(query: str) -> dict[str, Any]:
        """Retrieve transcript blocks with nested, attribution-preserving content objects."""
        try:
            result = await resolved_service.retrieve_evidence(query)
        except (EvidenceValidationError, ValueError) as exc:
            return TranscriptBlockResponse(
                schema_version=MCP_SCHEMA_VERSION,
                status="needs_clarification",
                company_code=None,
                period=None,
                items=[],
                warnings=[],
                clarification_question=str(exc),
            ).model_dump(mode="json")

        evidence = result["evidence"]
        source_ids = list(dict.fromkeys(item.source_id for item in evidence))
        previews = await asyncio.gather(
            *(
                resolved_service.gateway.get_source_preview(source_id, result["co_code"])
                for source_id in source_ids
            )
        )
        source_urls = {
            source_id: preview.live_url
            for source_id, preview in zip(source_ids, previews, strict=True)
            if preview is not None
        }
        items = [
            TranscriptBlockItem(
                period=item.period,
                speaker=item.metadata.get("speaker"),
                speakers=[str(value) for value in item.metadata.get("speakers", [])],
                title=item.title,
                score=item.score,
                content=TranscriptBlockContent(
                    text=item.content,
                    paragraph_id=item.locator.paragraph_id,
                    source_id=item.source_id,
                    content_hash=item.content_hash,
                    source_url=source_urls.get(item.source_id),
                ),
            )
            for item in evidence
        ]
        response = TranscriptBlockResponse(
            schema_version=MCP_SCHEMA_VERSION,
            status="retrieved" if items else "refused",
            company_code=result["co_code"],
            period=result["period"],
            items=items,
            warnings=[] if items else ["No verified transcript blocks were found."],
            clarification_question=None,
        )
        return response.model_dump(mode="json")

    return server


settings = get_settings()
mcp = create_transcript_mcp(settings)


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=settings.mcp_bind_host,
        port=settings.transcript_mcp_port,
        show_banner=False,
    )
