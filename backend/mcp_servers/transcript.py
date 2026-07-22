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
    EarningsCallListResponse,
    EvidenceToolResponse,
    MultiPeriodEarningsCallGroup,
    MultiPeriodEarningsCallResponse,
    TranscriptBlockContent,
    TranscriptBlockItem,
    TranscriptBlockResponse,
    TranscriptConversationResponse,
    VerifiedCitation,
    VerifiedRAGResponse,
    build_transcript_display,
    clarification_response,
    response_confidence,
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
        version=MCP_SCHEMA_VERSION,
        mask_error_details=True,
        strict_input_validation=True,
        auth=build_mcp_auth(resolved_settings),
        instructions=(
            "Use ask_earnings_call for questions that require a supported answer. Use "
            "list_earnings_calls before resolving requests such as recent quarters. Use "
            "retrieve_multi_period_earnings_call_evidence for quarter-by-quarter comparison. Use "
            "get_earnings_call_transcript when the user asks to read the latest or a specific "
            "call; it returns ordered speaker turns instead of vector Top-K. Transcript tools "
            "never supplement missing content with financial databases or model memory."
        ),
    )

    @server.tool(output_schema=VerifiedRAGResponse.model_json_schema())
    async def ask_earnings_call(query: str, co_code: str | None = None) -> dict[str, Any]:
        """Answer one-company earnings-call questions using transcript evidence only."""
        started = time.perf_counter()
        try:
            result = await resolved_service.answer(query, co_code)
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
        citations: list[VerifiedCitation] = []
        for item in result.citations:
            preview = provenance.get(item.source_id)
            citations.append(
                VerifiedCitation.model_validate(
                    {
                        **item.model_dump(mode="json"),
                        "live_url": preview.live_url if preview else None,
                        "content_hash": preview.content_hash if preview else None,
                        "captured_at": preview.captured_at if preview else None,
                    }
                )
            )

        passed = result.verification.get("passed") is True
        response = VerifiedRAGResponse(
            schema_version=MCP_SCHEMA_VERSION,
            status="answered" if passed else "refused",
            answer=result.answer,
            co_code=result.co_code,
            display=build_transcript_display(result, citations),
            routes=["transcript"],
            citations=citations,
            trace_id=result.trace_id,
            verification=result.verification,
            verified=passed,
            confidence=response_confidence(result),
            verification_notes=[
                str(result.verification.get("semantic", {}).get("reason", "verification_complete"))
            ],
            warnings=[] if passed else ["Transcript evidence did not pass all verification gates."],
            data_versions=result.data_versions,
            latency_ms=(time.perf_counter() - started) * 1000,
            period_resolution=result.period_resolution,
            clarification_question=None,
        )
        return response.model_dump(mode="json")

    @server.tool(output_schema=EarningsCallListResponse.model_json_schema())
    async def list_earnings_calls(
        query: str, co_code: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        """List available calls so an agent never guesses what recent quarters means."""
        try:
            result = await resolved_service.list_earnings_calls(query, co_code, limit)
        except (EvidenceValidationError, ValueError) as exc:
            return EarningsCallListResponse(
                status="needs_clarification",
                message=str(exc),
            ).model_dump(mode="json", exclude_none=True)
        calls = result["calls"]
        return EarningsCallListResponse(
            status="retrieved" if calls else "refused",
            company_code=result["co_code"],
            earnings_calls=calls,
            message=None if calls else "No earnings calls are available for this company.",
        ).model_dump(mode="json", exclude_none=True)

    @server.tool(output_schema=MultiPeriodEarningsCallResponse.model_json_schema())
    async def retrieve_multi_period_earnings_call_evidence(
        query: str,
        co_code: str | None = None,
        quarters: list[str] | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        """Retrieve separately grouped evidence for up to four earnings-call quarters."""
        try:
            result = await resolved_service.retrieve_multi_period_transcript_evidence(
                query,
                co_code,
                quarters,
                limit,
            )
        except (EvidenceValidationError, ValueError) as exc:
            return MultiPeriodEarningsCallResponse(
                status="needs_clarification",
                message=str(exc),
            ).model_dump(mode="json", exclude_none=True)
        groups = [
            MultiPeriodEarningsCallGroup(
                quarter=group["call"].quarter,
                period=group["call"].period,
                event_date=group["call"].event_date,
                source_id=group["call"].source_id,
                coverage_mode=group["coverage_mode"],
                coverage_queries=group["coverage_queries"],
                evidence=group["evidence"],
            )
            for group in result["groups"]
        ]
        return MultiPeriodEarningsCallResponse(
            status="retrieved" if groups else "refused",
            company_code=result["co_code"],
            quarters=groups,
            warnings=(
                []
                if groups
                else ["No official earnings-call transcript was found for the requested quarters."]
            ),
        ).model_dump(mode="json", exclude_none=True)

    @server.tool(output_schema=TranscriptConversationResponse.model_json_schema())
    async def get_earnings_call_transcript(
        query: str,
        co_code: str | None = None,
        cursor: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Read the latest or specified earnings call as ordered speaker conversations."""
        try:
            result = await resolved_service.retrieve_transcript_conversation(
                query,
                co_code,
                cursor=max(cursor, 0),
                limit=min(max(limit, 1), 50),
            )
        except (EvidenceValidationError, ValueError) as exc:
            return TranscriptConversationResponse(
                status="needs_clarification",
                message=str(exc),
            ).model_dump(mode="json", exclude_none=True)
        page = result["page"]
        if page is None:
            return TranscriptConversationResponse(
                status="refused",
                company_code=result["co_code"],
                message="No official earnings-call transcript was found for the requested period.",
            ).model_dump(mode="json", exclude_none=True)
        response = TranscriptConversationResponse(
            status="retrieved",
            company_code=page.company_code,
            quarter=page.quarter,
            conversations=page.conversations,
            next_cursor=page.next_cursor,
        )
        payload = response.model_dump(mode="json", exclude_none=True)
        payload["conversations"] = [turn.model_dump(mode="json") for turn in page.conversations]
        return payload

    @server.tool(output_schema=EvidenceToolResponse.model_json_schema())
    async def retrieve_earnings_call_evidence(
        query: str, co_code: str | None = None
    ) -> dict[str, Any]:
        """Retrieve validated transcript evidence without generating an answer."""
        started = time.perf_counter()
        try:
            result = await resolved_service.retrieve_evidence(query, co_code)
        except (EvidenceValidationError, ValueError) as exc:
            return EvidenceToolResponse(
                schema_version=MCP_SCHEMA_VERSION,
                status="needs_clarification",
                co_code=None,
                period=None,
                evidence=[],
                verified=False,
                verification={"passed": False, "reason": "needs_clarification"},
                warnings=[],
                latency_ms=(time.perf_counter() - started) * 1000,
                clarification_question=str(exc),
                period_resolution=None,
            ).model_dump(mode="json")
        evidence = result["evidence"]
        response = EvidenceToolResponse(
            schema_version=MCP_SCHEMA_VERSION,
            status="retrieved" if evidence else "refused",
            co_code=result["co_code"],
            period=result["period"],
            evidence=evidence,
            verified=bool(evidence),
            verification=result["verification"],
            warnings=[] if evidence else ["No verified transcript evidence was found."],
            latency_ms=(time.perf_counter() - started) * 1000,
            period_resolution=result["period_resolution"],
            clarification_question=None,
        )
        return response.model_dump(mode="json")

    @server.tool(output_schema=TranscriptBlockResponse.model_json_schema())
    async def retrieve_earnings_call_blocks(
        query: str, co_code: str | None = None
    ) -> dict[str, Any]:
        """Retrieve transcript blocks with nested, attribution-preserving content objects."""
        started = time.perf_counter()
        try:
            result = await resolved_service.retrieve_evidence(query, co_code)
        except (EvidenceValidationError, ValueError) as exc:
            return TranscriptBlockResponse(
                schema_version=MCP_SCHEMA_VERSION,
                status="needs_clarification",
                co_code=None,
                period=None,
                items=[],
                verified=False,
                latency_ms=(time.perf_counter() - started) * 1000,
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
                fiscal_label=item.metadata.get("fiscal_label"),
                speaker=item.metadata.get("speaker"),
                speakers=[str(value) for value in item.metadata.get("speakers", [])],
                title=item.title,
                score=item.score,
                content=TranscriptBlockContent(
                    section=item.metadata.get("section"),
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
            co_code=result["co_code"],
            period=result["period"],
            items=items,
            verified=bool(items),
            warnings=[] if items else ["No verified transcript blocks were found."],
            latency_ms=(time.perf_counter() - started) * 1000,
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
