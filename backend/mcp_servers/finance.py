from __future__ import annotations

import asyncio
import time

from fastmcp import FastMCP

from app.company_resolver import find_company_mentions, search_company_candidates as rank_companies
from app.config import get_settings
from app.mcp_auth import build_mcp_auth
from app.repositories import build_finance_repository, dump_evidence
from app.validation import EvidenceValidator


settings = get_settings()
validator = EvidenceValidator.from_settings(settings)
repository = build_finance_repository(settings)
_company_cache: list | None = None
_company_cache_expires_at = 0.0
_company_cache_lock = asyncio.Lock()
mcp = FastMCP(
    "Finance Data MCP",
    instructions="Read-only, parameterized financial metric tools. Arbitrary SQL is forbidden.",
    auth=build_mcp_auth(settings),
)


async def company_master() -> list:
    global _company_cache, _company_cache_expires_at
    now = time.monotonic()
    if _company_cache is not None and now < _company_cache_expires_at:
        return _company_cache
    async with _company_cache_lock:
        now = time.monotonic()
        if _company_cache is None or now >= _company_cache_expires_at:
            _company_cache = await repository.list_companies()
            _company_cache_expires_at = now + settings.company_index_ttl_seconds
    return _company_cache


@mcp.tool
async def resolve_company(name_or_code: str) -> dict:
    """Resolve names, aliases, and co_codes against the configured company master."""
    items = await company_master()
    allowed = [item for item in items if settings.is_company_allowed(item.co_code)]
    matches = find_company_mentions(name_or_code, allowed)
    return {
        "companies": [item.model_dump(mode="json") for item in matches],
        "metadata": {
            "tool": "resolve_company",
            "match_count": len(matches),
        },
    }


@mcp.tool
async def search_company_candidates(name_or_code: str, limit: int = 10) -> dict:
    """Return a bounded, scored candidate set from the configured company master."""
    items = await company_master()
    allowed = [item for item in items if settings.is_company_allowed(item.co_code)]
    candidates = rank_companies(name_or_code, allowed, limit)
    return {
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "metadata": {
            "tool": "search_company_candidates",
            "candidate_count": len(candidates),
        },
    }


@mcp.tool
async def list_companies() -> dict:
    """List companies in the configured scope; '*' exposes the whole company master."""
    items = await company_master()
    allowed = [item for item in items if settings.is_company_allowed(item.co_code)]
    return {
        "companies": [item.model_dump(mode="json") for item in allowed],
        "metadata": {"tool": "list_companies"},
    }


@mcp.tool
async def get_financial_metrics(co_code: str, period: str | None = None) -> dict:
    """Get structured financial metrics for one authorized company and optional period."""
    code = validator.validate_scope(co_code)
    items = await repository.get_metrics(code, period)
    items = validator.validate_evidence(code, items)
    return {"evidence": dump_evidence(items), "metadata": {"tool": "financial_metrics"}}


@mcp.tool
async def compare_financial_periods(
    co_code: str, base_period: str, compare_period: str
) -> dict:
    """Return source records for two periods; calculations remain deterministic upstream."""
    code = validator.validate_scope(co_code)
    base = await repository.get_metrics(code, base_period)
    compare = await repository.get_metrics(code, compare_period)
    items = validator.validate_evidence(code, [*base, *compare])
    return {
        "evidence": dump_evidence(items),
        "metadata": {
            "tool": "compare_periods",
            "base_period": base_period,
            "compare_period": compare_period,
        },
    }


@mcp.tool
async def get_record_provenance(
    co_code: str, period: str, metric_code: str
) -> dict:
    """Return the exact DB evidence row matching a metric primary key."""
    code = validator.validate_scope(co_code)
    items = await repository.get_metrics(code, period)
    matches = [item for item in items if item.metadata.get("metric_code") == metric_code]
    matches = validator.validate_evidence(code, matches)
    return {"evidence": dump_evidence(matches), "metadata": {"tool": "record_provenance"}}


@mcp.tool
async def get_financial_source_preview(source_id: str, co_code: str) -> dict:
    """Return the DB rows and data version behind a structured financial source."""
    code = validator.validate_scope(co_code)
    preview = await repository.get_source_preview(source_id, code)
    return {
        "preview": preview.model_dump(mode="json") if preview else None,
        "metadata": {"tool": "financial_source_preview"},
    }


@mcp.tool
async def list_financial_periods(co_code: str) -> dict:
    """List available structured financial periods for one company."""
    code = validator.validate_scope(co_code)
    return {
        "periods": await repository.list_periods(code),
        "metadata": {"tool": "list_financial_periods", "co_code": code},
    }


@mcp.tool
async def get_fiscal_calendar(co_code: str) -> dict:
    """Return the company fiscal-calendar metadata when configured."""
    code = validator.validate_scope(co_code)
    calendar = await repository.get_fiscal_calendar(code)
    return {
        "fiscal_calendar": calendar.model_dump(mode="json") if calendar else None,
        "metadata": {"tool": "get_fiscal_calendar", "co_code": code},
    }


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=settings.mcp_bind_host,
        port=settings.finance_mcp_port,
        show_banner=False,
    )
