import pytest
from fastmcp import Client

from app.agents import FinancialAgentService
from app.config import Settings
from app.llm import CompanyLLMClient
from app.models import EarningsCallRecord, Evidence, PeriodResolution, SourceLocator, SourceType
from app.validation import EvidenceValidator
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
async def test_multi_period_retrieval_keeps_quarter_evidence_isolated() -> None:
    settings = Settings(data_mode="mock", mcp_enabled=False, allowed_co_codes="MSFT")

    class Gateway:
        async def list_earnings_calls(self, co_code: str, limit: int = 20):
            assert co_code == "MSFT"
            return [
                EarningsCallRecord(
                    company_code="MSFT",
                    period="2026Q1",
                    quarter="FY2026 Q3",
                    event_date="2026-04-29",
                    source_id="call-q3",
                ),
                EarningsCallRecord(
                    company_code="MSFT",
                    period="2025Q4",
                    quarter="FY2026 Q2",
                    event_date="2026-01-28",
                    source_id="call-q2",
                ),
            ][:limit]

        async def search_documents(self, query, co_code, top_k=5, period=None, source_types=None):
            assert co_code == "MSFT"
            assert source_types == ("transcript",)
            return [
                Evidence(
                    evidence_id=f"{period}-{abs(hash(query))}",
                    co_code="MSFT",
                    source_id=f"call-{period}",
                    source_type=SourceType.TRANSCRIPT,
                    title=f"Call {period}",
                    content=f"Evidence for {period}",
                    score=0.9,
                    period=period,
                    locator=SourceLocator(paragraph_id="p-1"),
                    content_hash=f"sha256:{period}",
                )
            ][:top_k]

    service = FinancialAgentService(
        gateway=Gateway(),
        llm=CompanyLLMClient(settings),
        validator=EvidenceValidator.from_settings(settings),
        retrieval_profile="transcript",
    )

    async def scoped_state(_state):
        return {"co_code": "MSFT", "period_resolution": {}}

    service._scope_node = scoped_state
    mapped = await service._resolve_transcript_fiscal_label(
        "微軟 FY2026 Q2 法說會",
        "MSFT",
        PeriodResolution(
            input="2026Q2",
            resolved_period="2026Q2",
            as_of="2026-07-22",
            method="explicit_fiscal_quarter",
            confidence=1.0,
        ),
    )
    assert mapped.resolved_period == "2025Q4"
    assert mapped.input == "FY2026 Q2"
    assert mapped.method == "company_fiscal_label"

    result = await service.retrieve_multi_period_transcript_evidence(
        "微軟最近幾季的法說會重點分別是什麼？",
        quarters=["FY2026 Q3", "FY2026 Q2"],
    )

    assert [group["call"].quarter for group in result["groups"]] == [
        "FY2026 Q3",
        "FY2026 Q2",
    ]
    assert all(len(group["coverage_queries"]) == 4 for group in result["groups"])
    assert all(
        {item.period for item in group["evidence"]} == {group["call"].period}
        for group in result["groups"]
    )


@pytest.mark.asyncio
async def test_transcript_mcp_exposes_answer_and_evidence_tools() -> None:
    async with Client(build_test_mcp()) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools] == [
            "ask_earnings_call",
            "list_earnings_calls",
            "retrieve_multi_period_earnings_call_evidence",
            "get_earnings_call_transcript",
            "retrieve_earnings_call_evidence",
            "retrieve_earnings_call_blocks",
        ]
        assert "display" in tools[0].outputSchema["required"]
        assert "earnings_calls" in tools[1].outputSchema["properties"]
        assert "quarters" in tools[2].outputSchema["properties"]
        assert "conversations" in tools[3].outputSchema["properties"]
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
        assert (
            data["display"]["sources"][0]["source_content"] == data["citations"][0]["quoted_text"]
        )

        calls = await client.call_tool(
            "list_earnings_calls",
            {"query": "範例科技最近有哪些法說會？", "limit": 5},
        )
        calls_data = calls.structured_content
        assert calls_data["status"] == "retrieved"
        assert [item["quarter"] for item in calls_data["earnings_calls"]] == ["2026Q2"]

        multi_period = await client.call_tool(
            "retrieve_multi_period_earnings_call_evidence",
            {
                "query": "範例科技最近幾個季度的法說會重點分別是什麼？",
                "quarters": ["2026Q2"],
            },
        )
        multi_data = multi_period.structured_content
        assert multi_data["status"] == "retrieved"
        assert [item["quarter"] for item in multi_data["quarters"]] == ["2026Q2"]
        assert all(item["evidence"] for item in multi_data["quarters"])
        assert all(
            item["coverage_mode"] == "broad_facet_retrieval" for item in multi_data["quarters"]
        )
        assert all(
            {evidence["period"] for evidence in item["evidence"]} == {item["period"]}
            for item in multi_data["quarters"]
        )

        transcript = await client.call_tool(
            "get_earnings_call_transcript",
            {"query": "範例科技最近的法說會對話內容", "limit": 10},
        )
        transcript_data = transcript.structured_content
        assert transcript_data["status"] == "retrieved"
        assert transcript_data["company_code"] == "DEMO01"
        assert transcript_data["quarter"] == "2026Q2"
        assert transcript_data["conversations"] == [
            {
                "speaker": {"name": "財務長", "title": None},
                "content": (
                    "財務長表示，下半年主要不確定性包括海外專案驗收遞延、匯率波動，"
                    "以及雲端基礎設施成本上升；公司尚未因此調整全年展望。"
                ),
            }
        ]

        evidence = await client.call_tool(
            "retrieve_earnings_call_evidence",
            {"query": "範例科技 2026 Q2 法說會風險"},
        )
        evidence_data = evidence.structured_content
        assert evidence_data["status"] == "retrieved"
        assert {item["source_type"] for item in evidence_data["evidence"]} == {"transcript"}

        blocks = await client.call_tool(
            "retrieve_earnings_call_blocks",
            {"query": "範例科技 2026 Q2 法說會風險"},
        )
        block_data = blocks.structured_content
        assert block_data["status"] == "retrieved"
        assert block_data["co_code"] == "DEMO01"
        assert block_data["period"] == "2026Q2"
        assert block_data["items"]
        assert block_data["items"][0]["content"]["text"]
        assert block_data["items"][0]["content"]["source_id"]
