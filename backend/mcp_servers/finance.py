from __future__ import annotations

from fastmcp import FastMCP

from app.company_resolver import find_company_mentions
from app.config import get_settings
from app.repositories import build_finance_repository, dump_evidence
from app.validation import EvidenceValidator


settings = get_settings()
validator = EvidenceValidator(settings.allowed_co_code_set)
repository = build_finance_repository(settings)
mcp = FastMCP(
    "Finance Data MCP",
    instructions="Read-only, parameterized financial metric tools. Arbitrary SQL is forbidden.",
)


@mcp.tool
async def resolve_company(name_or_code: str) -> dict:
    """Resolve names, aliases, and co_codes against the configured company master."""
    items = await repository.list_companies()
    allowed = [item for item in items if item.co_code in settings.allowed_co_code_set]
    matches = find_company_mentions(name_or_code, allowed)
    return {
        "companies": [item.model_dump(mode="json") for item in matches],
        "metadata": {
            "tool": "resolve_company",
            "match_count": len(matches),
        },
    }


@mcp.tool
async def list_companies() -> dict:
    """List local companies, filtered by the configured allowlist."""
    items = await repository.list_companies()
    allowed = [item for item in items if item.co_code in settings.allowed_co_code_set]
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


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=settings.mcp_server_host,
        port=settings.finance_mcp_port,
        show_banner=False,
    )
