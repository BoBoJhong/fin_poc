from __future__ import annotations

from fastmcp import FastMCP

from app.config import get_settings
from app.mcp_auth import build_mcp_auth
from app.repositories import build_knowledge_repository, dump_evidence
from app.validation import EvidenceValidator


settings = get_settings()
validator = EvidenceValidator.from_settings(settings)
repository = build_knowledge_repository(settings)
mcp = FastMCP(
    "Knowledge MCP",
    instructions="Scoped financial document, graph and source-preview tools.",
    auth=build_mcp_auth(settings),
)


@mcp.tool
async def search_financial_documents(
    query: str,
    co_code: str,
    top_k: int = 5,
    period: str | None = None,
    source_types: list[str] | None = None,
) -> dict:
    """Vector-search financial chunks with mandatory co_code filtering."""
    code = validator.validate_scope(co_code)
    allowed = {"financial_report", "transcript", "url"}
    requested_types = tuple(source_types) if source_types else None
    if requested_types and not set(requested_types).issubset(allowed):
        raise ValueError("source_types contains an unsupported document type")
    items = await repository.search_documents(
        query, code, min(max(top_k, 1), 10), period, requested_types
    )
    items = validator.validate_evidence(code, items, period)
    return {"evidence": dump_evidence(items), "metadata": {"tool": "vector_rag"}}


@mcp.tool
async def search_graph_relationships(
    query: str, co_code: str, max_hops: int = 2, period: str | None = None
) -> dict:
    """Traverse provenance-bearing, allowlisted Neo4j relationships up to two hops."""
    code = validator.validate_scope(co_code)
    items = await repository.search_graph(query, code, min(max(max_hops, 1), 2), period)
    items = validator.validate_evidence(code, items, period)
    return {"evidence": dump_evidence(items), "metadata": {"tool": "graph_rag"}}


@mcp.tool
async def get_source_preview(source_id: str, co_code: str) -> dict:
    """Return the exact snapshot/record used by the answer, scoped by co_code."""
    code = validator.validate_scope(co_code)
    preview = await repository.get_source_preview(source_id, code)
    return {
        "preview": preview.model_dump(mode="json") if preview else None,
        "metadata": {"tool": "source_preview"},
    }


@mcp.tool
async def list_document_periods(
    co_code: str, source_types: list[str] | None = None
) -> dict:
    """List verified document periods for one company and optional source types."""
    code = validator.validate_scope(co_code)
    periods = await repository.list_periods(
        code, tuple(source_types) if source_types else None
    )
    return {
        "periods": periods,
        "metadata": {"tool": "list_document_periods", "co_code": code},
    }


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=settings.mcp_bind_host,
        port=settings.knowledge_mcp_port,
        show_banner=False,
    )
