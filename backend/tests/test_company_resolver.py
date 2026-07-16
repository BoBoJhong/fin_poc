from unittest.mock import AsyncMock

import pytest

from app.company_resolver import (
    CompanyResolutionError,
    enforce_company_scope,
    find_company_mentions,
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


def test_rejects_different_or_multiple_company_mentions() -> None:
    other = find_company_mentions("示範製造的營收", COMPANIES)
    with pytest.raises(CompanyResolutionError, match="目前選擇"):
        enforce_company_scope("DEMO01", other)

    multiple = find_company_mentions("比較範例科技與示範製造", COMPANIES)
    with pytest.raises(CompanyResolutionError, match="多家公司"):
        enforce_company_scope("DEMO01", multiple)


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
