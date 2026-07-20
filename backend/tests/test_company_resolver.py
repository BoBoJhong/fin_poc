from unittest.mock import AsyncMock

import pytest

from app.company_resolver import (
    CompanyResolutionError,
    find_company_mentions,
    resolve_company_scope,
    search_company_candidates,
)
from app.config import Settings
from app.llm import CompanyLLMClient
from app.models import CompanySummary


COMPANIES = [
    CompanySummary(
        co_code="DEMO01",
        company_name="範例科技股份有限公司",
        aliases=["範例科技", "範科"],
    ),
    CompanySummary(
        co_code="DEMO02",
        company_name="示範製造股份有限公司",
        aliases=["示範製造", "示製"],
    ),
]


def test_resolves_full_name_alias_and_code() -> None:
    assert find_company_mentions("範例科技的營收", COMPANIES)[0].co_code == "DEMO01"
    assert find_company_mentions("請分析範科", COMPANIES)[0].co_code == "DEMO01"
    assert find_company_mentions("DEMO01 風險", COMPANIES)[0].co_code == "DEMO01"


def test_explicit_company_overrides_default_and_ambiguous_matches_are_rejected() -> None:
    other = find_company_mentions("示範製造的營收", COMPANIES)
    assert resolve_company_scope("DEMO01", other) == "DEMO02"

    multiple = find_company_mentions("比較範例科技與示範製造", COMPANIES)
    with pytest.raises(CompanyResolutionError, match="多個候選"):
        resolve_company_scope("DEMO01", multiple)


def test_company_is_required_when_there_is_no_legacy_default() -> None:
    with pytest.raises(CompanyResolutionError, match="無法從問題判斷公司"):
        resolve_company_scope(None, [])


def test_company_index_ranks_typo_and_short_codes_require_boundaries() -> None:
    candidates = search_company_candidates("範例科枝 2026 Q2 營收", COMPANIES)
    assert candidates[0].company.co_code == "DEMO01"
    assert candidates[0].match_method == "fuzzy_company_index"

    short_code = CompanySummary(co_code="AI", company_name="Artificial Industries")
    assert find_company_mentions("The company said revenue increased", [short_code]) == []
    assert find_company_mentions("AI revenue", [short_code])[0].co_code == "AI"


@pytest.mark.asyncio
async def test_llm_resolution_is_constrained_to_company_master() -> None:
    client = CompanyLLMClient(
        Settings(
            company_llm_mode="openai_compatible",
            company_llm_api_key="test-key",
        )
    )
    client._chat = AsyncMock(  # type: ignore[method-assign]
        return_value='{"status":"matched","co_codes":["DEMO01"],"reason":"英文別名"}'
    )
    result = await client.resolve_company_reference("Example Tech 的營收", COMPANIES)
    assert result["status"] == "matched"
    assert [company.co_code for company in result["companies"]] == ["DEMO01"]


@pytest.mark.asyncio
async def test_llm_cannot_invent_company_code() -> None:
    client = CompanyLLMClient(
        Settings(
            company_llm_mode="openai_compatible",
            company_llm_api_key="test-key",
        )
    )
    client._chat = AsyncMock(  # type: ignore[method-assign]
        return_value='{"status":"matched","co_codes":["INVENTED"],"reason":"guess"}'
    )
    result = await client.resolve_company_reference("未知公司", COMPANIES)
    assert result["status"] == "unknown"
    assert result["companies"] == []
