import pytest
from fastmcp import Client

from app.config import Settings
from mcp_servers.transcript import create_transcript_mcp


def build_test_mcp() -> object:
    return create_transcript_mcp(
        Settings(
            data_mode="mock",
            mcp_enabled=False,
            company_llm_mode="mock",
            allowed_co_codes="DEMO01,DEMO02",
        )
    )


@pytest.mark.asyncio
async def test_transcript_mcp_exposes_answer_and_evidence_tools() -> None:
    async with Client(build_test_mcp()) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools] == [
            "ask_earnings_call",
            "retrieve_earnings_call_evidence",
        ]
        assert "display" in tools[0].outputSchema["required"]
        assert "period_resolution" in tools[1].outputSchema["required"]
        result = await client.call_tool(
            "ask_earnings_call",
            {"query": "範例科技 2026 Q2 法說會說明了哪些風險？"},
        )
        data = result.structured_content
        assert data["status"] == "answered"
        assert data["schema_version"] == "1.1"
        assert data["verification"]["passed"] is True
        assert data["citations"]
        assert {item["source_type"] for item in data["citations"]} == {"transcript"}
        assert data["display"]["title"] == "DEMO01 2026Q2 法說會"
        assert data["display"]["sources"][0]["source_content"] == data[
            "citations"
        ][0]["quoted_text"]

        evidence = await client.call_tool(
            "retrieve_earnings_call_evidence",
            {"query": "範例科技 2026 Q2 法說會風險"},
        )
        evidence_data = evidence.structured_content
        assert evidence_data["status"] == "retrieved"
        assert {item["source_type"] for item in evidence_data["evidence"]} == {"transcript"}
