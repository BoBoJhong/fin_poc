from __future__ import annotations

from fastmcp import FastMCP

from app.config import get_settings
from app.repositories import build_knowledge_repository, dump_evidence
from app.validation import EvidenceValidator


settings = get_settings()
validator = EvidenceValidator(settings.allowed_co_code_set)
repository = build_knowledge_repository(settings)
mcp = FastMCP(
    "Knowledge MCP",
    instructions="Scoped financial document, graph and source-preview tools.",
)


@mcp.tool
async def search_financial_documents(
    query: str, co_code: str, top_k: int = 5
) -> dict:
    """Vector-search financial chunks with mandatory co_code filtering."""
    code = validator.validate_scope(co_code)
    items = await repository.search_documents(query, code, min(max(top_k, 1), 10))
    items = validator.validate_evidence(code, items)
    return {"evidence": dump_evidence(items), "metadata": {"tool": "vector_rag"}}


@mcp.tool
async def search_graph_relationships(
    query: str, co_code: str, max_hops: int = 2
) -> dict:
    """Traverse provenance-bearing, allowlisted Neo4j relationships up to two hops."""
    code = validator.validate_scope(co_code)
    items = await repository.search_graph(query, code, min(max(max_hops, 1), 2))
    items = validator.validate_evidence(code, items)
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


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=settings.mcp_server_host,
        port=settings.knowledge_mcp_port,
        show_banner=False,
    )
