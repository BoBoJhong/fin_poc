import pytest

from app.agents import FinancialAgentService
from app.config import Settings
from app.llm import CompanyLLMClient
from app.mcp_gateway import MCPGateway
from app.validation import EvidenceValidator


def service() -> FinancialAgentService:
    settings = Settings(
        data_mode="mock",
        mcp_enabled=False,
        company_llm_mode="mock",
        allowed_co_codes="DEMO01,DEMO02",
    )
    return FinancialAgentService(
        gateway=MCPGateway(settings),
        llm=CompanyLLMClient(settings),
        validator=EvidenceValidator(settings.allowed_co_code_set),
    )


@pytest.mark.asyncio
async def test_mixed_question_uses_bounded_subagents() -> None:
    result = await service().answer(
        "範例科技 2026 Q2 的營收和毛利率是多少？主要風險是什麼？",
        "DEMO01",
    )
    assert set(result.routes) == {"knowledge", "finance"}
    assert result.verification["passed"] is True
    assert result.verification["evidence"]["passed"] is True
    assert result.verification["answer"]["passed"] is True
    assert result.citations
    assert all(citation.source_id.startswith("demo01-") for citation in result.citations)
    assert result.data_versions == ["demo-v1"]


@pytest.mark.asyncio
async def test_company_code_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="目前選擇"):
        await service().answer("請分析 DEMO02 的營收", "DEMO01")


@pytest.mark.asyncio
async def test_company_alias_is_resolved_before_retrieval() -> None:
    result = await service().answer("範科 2026 Q2 的營收是多少？", "DEMO01")
    resolution = result.verification["company_resolution"]
    assert resolution["passed"] is True
    assert resolution["method"] == "company_master"
    assert resolution["mentioned_co_codes"] == ["DEMO01"]


@pytest.mark.asyncio
async def test_multiple_company_question_is_rejected() -> None:
    with pytest.raises(ValueError, match="多家公司"):
        await service().answer("比較範例科技與示範製造的營收", "DEMO01")


@pytest.mark.asyncio
async def test_empty_company_data_does_not_hallucinate() -> None:
    result = await service().answer("2026 Q2 營收是多少？", "DEMO02")
    assert result.citations == []
    assert "找不到" in result.answer
    assert result.verification["passed"] is False
