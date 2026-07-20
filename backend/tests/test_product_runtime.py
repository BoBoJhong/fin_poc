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
                "schema_version": "1.1",
                "status": "refused",
                "answer": "No verified evidence.",
                "co_code": "MSFT",
                "display": None,
                "citations": [],
                "routes": [],
                "trace_id": "trace",
                "verification": {"passed": False},
                "verified": False,
                "confidence": 0.0,
                "verification_notes": [],
                "warnings": [],
                "data_versions": [],
                "latency_ms": 1.0,
                "clarification_question": None,
                "period_resolution": None,
            }

    settings = Settings(mcp_enabled=True)
    service = PublicMCPChatService(settings, MCPGateway(Settings(mcp_enabled=False)))
    service._tools = {"ask_financial_rag": Tool()}
    result = await service.answer("Microsoft revenue")
    assert result.schema_version == "1.1"
    assert result.status == "refused"
    assert result.verified is False


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
