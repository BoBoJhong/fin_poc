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
from app.models import ChatRequest, ChatResponse, CompanySummary, SourcePreview
from app.validation import EvidenceValidationError, EvidenceValidator


class TenantContext:
    def __init__(self, user_id: str, co_code: str | None):
        self.user_id = user_id
        self.co_code = co_code


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
def get_agent_service() -> FinancialAgentService:
    settings = get_settings()
    return FinancialAgentService(
        gateway=MCPGateway(settings),
        llm=CompanyLLMClient(settings),
        validator=EvidenceValidator.from_settings(settings),
        max_evidence_items=settings.max_evidence_items,
    )


app = FastAPI(
    title="Financial GraphRAG MCP PoC",
    version="0.1.0",
    description="Local LangGraph workflow with MCP, Neo4j GraphRAG and SQLite.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-User-Id", "X-Co-Code"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "financial-graphrag-api"}


@app.get("/api/v1/companies", response_model=list[CompanySummary])
async def companies(
    service: FinancialAgentService = Depends(get_agent_service),
) -> list[CompanySummary]:
    """Return only locally available companies in the configured allowlist."""
    return await service.gateway.list_companies()


async def run_answer(
    request: ChatRequest, tenant: TenantContext, service: FinancialAgentService
) -> ChatResponse:
    request_code = request.co_code.strip().upper() if request.co_code else None
    if request_code and tenant.co_code and request_code != tenant.co_code:
        raise HTTPException(status_code=403, detail="body 與授權 co_code 不一致")
    try:
        return await service.answer(request.query, request_code or tenant.co_code)
    except (EvidenceValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    tenant: TenantContext = Depends(tenant_context),
    service: FinancialAgentService = Depends(get_agent_service),
) -> ChatResponse:
    return await run_answer(request, tenant, service)


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/v1/chat/stream")
async def chat_stream(
    request: ChatRequest,
    tenant: TenantContext = Depends(tenant_context),
    service: FinancialAgentService = Depends(get_agent_service),
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
    service: FinancialAgentService = Depends(get_agent_service),
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
