import pytest
from fastmcp import Client

from app.config import Settings
from mcp_servers.rag import create_rag_mcp


def build_test_mcp() -> object:
    settings = Settings(
        data_mode="mock",
        mcp_enabled=False,
        company_llm_mode="mock",
        allowed_co_codes="DEMO01,DEMO02",
    )
    return create_rag_mcp(settings)


@pytest.mark.asyncio
async def test_public_rag_mcp_exposes_answer_and_evidence_tools() -> None:
    async with Client(build_test_mcp()) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools] == [
            "ask_financial_rag",
            "retrieve_financial_evidence",
        ]
        answer_required = set(tools[0].outputSchema["required"])
        assert {
            "schema_version",
            "status",
            "answer",
            "co_code",
            "display",
            "citations",
            "routes",
            "trace_id",
            "verification",
            "verified",
            "confidence",
            "verification_notes",
            "warnings",
            "data_versions",
            "latency_ms",
            "clarification_question",
            "period_resolution",
        } <= answer_required
        assert {
            "schema_version",
            "status",
            "co_code",
            "period",
            "evidence",
            "verified",
            "verification",
            "warnings",
            "latency_ms",
            "clarification_question",
            "period_resolution",
        } <= set(tools[1].outputSchema["required"])

        result = await client.call_tool(
            "ask_financial_rag",
            {"query": "範例科技 2026 Q2 的營收、毛利率與主要風險？"},
        )
        data = result.structured_content
        assert result.is_error is False
        assert data["schema_version"] == "1.1"
        assert data["status"] == "answered"
        assert data["co_code"] == "DEMO01"
        assert data["verification"]["passed"] is True
        assert data["citations"]
        assert all(
            item["content_hash"] or item["locator"]["primary_key"]
            for item in data["citations"]
        )

        evidence = await client.call_tool(
            "retrieve_financial_evidence",
            {"query": "範例科技 2026 Q2 的營收是多少？"},
        )
        evidence_data = evidence.structured_content
        assert evidence_data["status"] == "retrieved"
        assert evidence_data["verified"] is True
        assert {item["source_type"] for item in evidence_data["evidence"]} <= {
            "database",
            "financial_report",
            "url",
        }


@pytest.mark.asyncio
async def test_public_rag_mcp_makes_refusal_machine_readable() -> None:
    async with Client(build_test_mcp()) as client:
        result = await client.call_tool(
            "ask_financial_rag",
            {"query": "範例科技 2035 Q4 revenue?"},
        )
        data = result.structured_content
        assert data["status"] == "refused"
        assert data["verification"]["passed"] is False
        assert data["verified"] is False
        assert data["citations"] == []
