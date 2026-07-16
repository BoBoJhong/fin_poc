import pytest
from fastmcp import Client

from mcp_servers.finance import mcp as finance_mcp
from mcp_servers.knowledge import mcp as knowledge_mcp


@pytest.mark.asyncio
async def test_knowledge_mcp_contract_and_scope() -> None:
    async with Client(knowledge_mcp) as client:
        tools = await client.list_tools()
        assert {
            "search_financial_documents",
            "search_graph_relationships",
            "get_source_preview",
        }.issubset({tool.name for tool in tools})
        result = await client.call_tool(
            "search_graph_relationships",
            {"query": "產品風險", "co_code": "DEMO01", "max_hops": 2},
        )
        assert result.is_error is False
        assert result.data["evidence"][0]["co_code"] == "DEMO01"


@pytest.mark.asyncio
async def test_finance_mcp_returns_recheckable_record() -> None:
    async with Client(finance_mcp) as client:
        tools = await client.list_tools()
        assert "resolve_company" in {tool.name for tool in tools}
        resolution = await client.call_tool(
            "resolve_company", {"name_or_code": "範科 2026 Q2 營收"}
        )
        assert resolution.data["companies"][0]["co_code"] == "DEMO01"

        result = await client.call_tool(
            "get_financial_metrics",
            {"co_code": "DEMO01", "period": "2026Q2"},
        )
        assert result.is_error is False
        assert len(result.data["evidence"]) == 2
        assert all(item["locator"]["primary_key"] for item in result.data["evidence"])

        preview = await client.call_tool(
            "get_financial_source_preview",
            {"source_id": "demo01-financial-metrics-2026q2", "co_code": "DEMO01"},
        )
        assert preview.data["preview"]["database_record"]["data_version"] == "demo-v1"
