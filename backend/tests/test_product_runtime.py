import pytest
from fastapi import HTTPException

from app.config import Settings
from app.main import RequestConcurrencyGate
from app.mcp_gateway import MCPGateway
from app.public_mcp_service import PublicMCPChatService


def test_frontend_public_mcp_routing_is_deterministic() -> None:
    assert PublicMCPChatService.select_tool("Microsoft 2026 Q1 revenue?") == (
        "ask_financial_rag"
    )
    assert PublicMCPChatService.select_tool("微軟最近一季法說會說了什麼？") == (
        "ask_earnings_call"
    )


@pytest.mark.asyncio
async def test_public_mcp_response_is_runtime_validated() -> None:
    class Tool:
        async def ainvoke(self, arguments):
            assert arguments["query"] == "Microsoft revenue"
            return {
                "schema_version": "2.0",
                "status": "refused",
                "answer": "No verified evidence.",
                "company_code": "MSFT",
                "period": None,
                "citations": [],
                "warnings": [],
                "clarification_question": None,
            }

    settings = Settings(mcp_enabled=True)
    service = PublicMCPChatService(settings, MCPGateway(Settings(mcp_enabled=False)))
    service._tools = {"ask_financial_rag": Tool()}
    result = await service.answer("Microsoft revenue")
    assert result.schema_version == "2.0"
    assert result.status == "refused"
    assert result.company_code == "MSFT"
    assert result.citations == []


@pytest.mark.asyncio
async def test_legacy_http_company_code_is_folded_into_public_mcp_query() -> None:
    class Tool:
        async def ainvoke(self, arguments):
            assert arguments == {"query": "MSFT 2025 Q3 法說會內容"}
            return {
                "schema_version": "2.0",
                "status": "refused",
                "answer": "No verified evidence.",
                "company_code": "MSFT",
                "period": "2025Q3",
                "citations": [],
                "warnings": [],
                "clarification_question": None,
            }

    settings = Settings(mcp_enabled=True)
    service = PublicMCPChatService(settings, MCPGateway(Settings(mcp_enabled=False)))
    service._tools = {"ask_earnings_call": Tool()}
    result = await service.answer("2025 Q3 法說會內容", "MSFT")
    assert result.company_code == "MSFT"
    assert result.period == "2025Q3"


@pytest.mark.asyncio
async def test_concurrency_gate_fails_fast_when_queue_is_full() -> None:
    gate = RequestConcurrencyGate(limit=1, timeout_seconds=0.01)
    await gate.acquire()
    try:
        with pytest.raises(HTTPException) as error:
            await gate.acquire()
        assert error.value.status_code == 503
    finally:
        gate.release()
