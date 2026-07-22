from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from typing import Annotated, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.agents import FinancialAgentService
from app.config import Settings, get_settings
from app.llm import CompanyLLMClient
from app.mcp_gateway import MCPGateway
from app.http_contracts import (
    HTTP_SCHEMA_VERSION,
    HttpCitation,
    HttpRAGResponse,
    response_confidence,
)
from app.mcp_contracts import VerifiedRAGResponse
from app.models import ChatRequest, CompanySummary, SourcePreview
from app.public_mcp_service import PublicMCPChatService
from app.validation import EvidenceValidationError, EvidenceValidator


class TenantContext:
    def __init__(self, user_id: str, co_code: str | None):
        self.user_id = user_id
        self.co_code = co_code


class RequestConcurrencyGate:
    def __init__(self, limit: int, timeout_seconds: float):
        self.semaphore = asyncio.Semaphore(limit)
        self.timeout_seconds = timeout_seconds

    async def acquire(self) -> None:
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail="服務繁忙，請稍後重試") from exc

    def release(self) -> None:
        self.semaphore.release()


@lru_cache
def get_concurrency_gate() -> RequestConcurrencyGate:
    settings = get_settings()
    return RequestConcurrencyGate(
        settings.api_max_concurrency,
        settings.api_queue_timeout_seconds,
    )


async def concurrency_slot(
    gate: RequestConcurrencyGate = Depends(get_concurrency_gate),
) -> AsyncIterator[None]:
    await gate.acquire()
    try:
        yield
    finally:
        gate.release()


def tenant_context(
    x_user_id: Annotated[str, Header(alias="X-User-Id")] = "poc-user",
    x_co_code: Annotated[str | None, Header(alias="X-Co-Code")] = None,
    settings: Settings = Depends(get_settings),
) -> TenantContext:
    code = x_co_code.strip().upper() if x_co_code else None
    if code and not settings.is_company_allowed(code):
        raise HTTPException(status_code=403, detail="co_code 不在授權範圍")
    # X-Co-Code remains a backwards-compatible default only. The question normally
    # determines company scope; production IAM authorization remains a separate layer.
    return TenantContext(user_id=x_user_id, co_code=code)


@lru_cache
def get_agent_service() -> FinancialAgentService | PublicMCPChatService:
    settings = get_settings()
    internal = FinancialAgentService(
        gateway=MCPGateway(settings),
        llm=CompanyLLMClient(settings),
        validator=EvidenceValidator.from_settings(settings),
        max_evidence_items=settings.max_evidence_items,
    )
    if settings.mcp_enabled and settings.frontend_use_public_mcp:
        return PublicMCPChatService(settings, internal.gateway)
    return internal


runtime_settings = get_settings()

app = FastAPI(
    title="Verified Financial RAG MCP",
    version="1.0.0",
    description="Source-isolated financial and earnings-call RAG with verified citations.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=runtime_settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-User-Id", "X-Co-Code"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "financial-graphrag-api"}


@app.get("/health/readiness")
async def readiness(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    llm_ready = bool(
        settings.company_llm_mode == "openai_compatible"
        and settings.company_llm_api_key
        and settings.company_llm_model
        and settings.company_llm_model != "your-model"
    )
    return {
        "status": "ready" if llm_ready else "evidence_only_ready",
        "schema_version": HTTP_SCHEMA_VERSION,
        "data_mode": settings.data_mode,
        "frontend_uses_public_mcp": settings.frontend_use_public_mcp,
        "evidence_tools_ready": True,
        "answer_llm_ready": llm_ready,
        "answer_mode": settings.company_llm_mode,
        "api_max_concurrency": settings.api_max_concurrency,
    }


@app.get("/api/v1/companies", response_model=list[CompanySummary])
async def companies(
    service: FinancialAgentService = Depends(get_agent_service),
) -> list[CompanySummary]:
    """Return only locally available companies in the configured allowlist."""
    return await service.gateway.list_companies()


async def run_answer(
    request: ChatRequest,
    tenant: TenantContext,
    service: FinancialAgentService | PublicMCPChatService,
) -> HttpRAGResponse:
    request_code = request.co_code.strip().upper() if request.co_code else None
    if request_code and tenant.co_code and request_code != tenant.co_code:
        raise HTTPException(status_code=403, detail="body 與授權 co_code 不一致")
    try:
        result = await service.answer(request.query, request_code or tenant.co_code)
    except (EvidenceValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(result, VerifiedRAGResponse):
        passed = result.status == "answered" and bool(result.citations)
        return HttpRAGResponse(
            schema_version=HTTP_SCHEMA_VERSION,
            status=result.status,
            answer=result.answer,
            co_code=result.company_code,
            display=None,
            citations=[
                HttpCitation(
                    index=item.index,
                    evidence_id=f"mcp-{item.source_id}-{item.index}",
                    co_code=result.company_code or "",
                    source_id=item.source_id,
                    title=item.source_id,
                    source_type=item.source_type,
                    locator=item.locator,
                    quoted_text=item.excerpt,
                    period=item.period,
                    metadata={"speaker": item.speaker} if item.speaker else {},
                    live_url=item.source_url,
                    content_hash=item.content_hash,
                )
                for item in result.citations
            ],
            routes=[],
            trace_id=None,
            verification={"passed": passed, "source": "public_mcp"},
            verified=passed,
            confidence=1.0 if passed else 0.0,
            verification_notes=[],
            warnings=result.warnings,
            data_versions=sorted(
                {item.data_version for item in result.citations if item.data_version}
            ),
            latency_ms=0.0,
            clarification_question=result.clarification_question,
            period_resolution=None,
        )
    return HttpRAGResponse(
        schema_version=HTTP_SCHEMA_VERSION,
        status="answered" if result.verification.get("passed") is True else "refused",
        answer=result.answer,
        co_code=result.co_code,
        display=None,
        citations=[HttpCitation.model_validate(item.model_dump()) for item in result.citations],
        routes=result.routes,
        trace_id=result.trace_id,
        verification=result.verification,
        verified=result.verification.get("passed") is True,
        confidence=response_confidence(result),
        verification_notes=[],
        warnings=[],
        data_versions=result.data_versions,
        latency_ms=0.0,
        clarification_question=None,
        period_resolution=result.period_resolution,
    )


@app.post("/api/v1/chat", response_model=HttpRAGResponse)
async def chat(
    request: ChatRequest,
    tenant: TenantContext = Depends(tenant_context),
    service: FinancialAgentService | PublicMCPChatService = Depends(get_agent_service),
    _: None = Depends(concurrency_slot),
) -> HttpRAGResponse:
    return await run_answer(request, tenant, service)


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/v1/chat/stream")
async def chat_stream(
    request: ChatRequest,
    tenant: TenantContext = Depends(tenant_context),
    service: FinancialAgentService | PublicMCPChatService = Depends(get_agent_service),
    _: None = Depends(concurrency_slot),
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        yield sse("status", {"stage": "retrieving", "message": "正在檢索授權來源"})
        result = await run_answer(request, tenant, service)
        yield sse("status", {"stage": "verifying", "message": "來源驗證完成"})
        for offset in range(0, len(result.answer), 48):
            yield sse("token", {"text": result.answer[offset : offset + 48]})
            await asyncio.sleep(0)
        yield sse("result", result.model_dump(mode="json"))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/sources/{source_id}", response_model=SourcePreview)
async def source_preview(
    source_id: str,
    co_code: Annotated[str, Query(min_length=2, max_length=32)],
    tenant: TenantContext = Depends(tenant_context),
    service: FinancialAgentService | PublicMCPChatService = Depends(get_agent_service),
    settings: Settings = Depends(get_settings),
) -> SourcePreview:
    del tenant
    code = co_code.strip().upper()
    if not settings.is_company_allowed(code):
        raise HTTPException(status_code=403, detail="來源不在授權公司範圍")
    preview = await service.gateway.get_source_preview(source_id, code)
    if preview is None:
        raise HTTPException(status_code=404, detail="找不到來源")
    return preview
