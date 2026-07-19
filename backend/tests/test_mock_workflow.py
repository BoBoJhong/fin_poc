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
    )
    assert set(result.routes) == {"knowledge", "finance"}
    assert result.verification["passed"] is True
    assert result.verification["evidence"]["passed"] is True
    assert result.verification["answer"]["passed"] is True
    assert result.verification["reliability_policy"]["accepted"] is True
    assert result.verification["reliability_policy"]["level"] == "high_guardrail_pass"
    assert result.citations
    assert all(citation.source_id.startswith("demo01-") for citation in result.citations)
    assert result.data_versions == ["demo-v1"]


@pytest.mark.asyncio
async def test_explicit_company_routes_to_resolved_scope() -> None:
    result = await service().answer("示範製造 2026 Q2 的營收", "DEMO01")
    assert result.co_code == "DEMO02"
    assert result.verification["company_resolution"]["selection_overridden"] is True
    assert {citation.source_id for citation in result.citations} == {
        "demo02-financial-metrics-2026q2"
    }


@pytest.mark.asyncio
async def test_company_alias_is_resolved_before_retrieval() -> None:
    result = await service().answer("範科 2026 Q2 的營收是多少？")
    resolution = result.verification["company_resolution"]
    assert resolution["passed"] is True
    assert resolution["method"] == "company_master"
    assert resolution["mentioned_co_codes"] == ["DEMO01"]


@pytest.mark.asyncio
async def test_empty_company_data_does_not_hallucinate() -> None:
    result = await service().answer("示範製造 2025 Q1 營收是多少？")
    assert result.citations == []
    assert "找不到" in result.answer
    assert result.verification["passed"] is False
    assert result.verification["reliability_policy"]["accepted"] is False
