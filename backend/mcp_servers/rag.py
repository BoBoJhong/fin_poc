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
    EvidenceToolResponse,
    VerifiedCitation,
    VerifiedRAGResponse,
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
        retrieval_profile="financial",
    )


def create_rag_mcp(
    settings: Settings | None = None,
    service: FinancialAgentService | None = None,
) -> FastMCP:
    resolved_settings = settings or get_settings()
    resolved_service = service or build_service(resolved_settings)
    server = FastMCP(
        "Verified Financial RAG",
        version=MCP_SCHEMA_VERSION,
        mask_error_details=True,
        strict_input_validation=True,
        auth=build_mcp_auth(resolved_settings),
        instructions=(
            "Use ask_financial_rag for structured financial metrics and SEC filing questions. "
            "The tool resolves the company, retrieves only scoped database and filing evidence, "
            "never earnings-call transcripts, and verifies every cited claim. "
            "and refuses unsupported answers. Preserve its citations and refusal status; do "
            "not add financial facts from model memory."
        ),
    )

    @server.tool(output_schema=VerifiedRAGResponse.model_json_schema())
    async def ask_financial_rag(query: str, co_code: str | None = None) -> dict[str, Any]:
        """Answer one-company financial questions with verified, traceable RAG evidence.

        Put the company name or ticker and period in `query`. `co_code` is an optional
        backwards-compatible hint, not a replacement for an explicit company in the query.
        A `refused` status means the caller must not invent or supplement an answer.
        """
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
            display=None,
            routes=[route for route in result.routes if route in {"knowledge", "finance"}],
            citations=citations,
            trace_id=result.trace_id,
            verification=result.verification,
            verified=passed,
            confidence=response_confidence(result),
            verification_notes=[
                str(result.verification.get("semantic", {}).get("reason", "verification_complete"))
            ],
            warnings=[] if passed else ["Financial evidence did not pass all verification gates."],
            data_versions=result.data_versions,
            latency_ms=(time.perf_counter() - started) * 1000,
            period_resolution=result.period_resolution,
            clarification_question=None,
        )
        return response.model_dump(mode="json")

    @server.tool(output_schema=EvidenceToolResponse.model_json_schema())
    async def retrieve_financial_evidence(
        query: str, co_code: str | None = None
    ) -> dict[str, Any]:
        """Retrieve validated financial evidence without generating an answer."""
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
            warnings=[] if evidence else ["No verified financial evidence was found."],
            latency_ms=(time.perf_counter() - started) * 1000,
            period_resolution=result["period_resolution"],
            clarification_question=None,
        )
        return response.model_dump(mode="json")

    return server


settings = get_settings()
mcp = create_rag_mcp(settings)


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=settings.mcp_bind_host,
        port=settings.rag_mcp_port,
        show_banner=False,
    )
