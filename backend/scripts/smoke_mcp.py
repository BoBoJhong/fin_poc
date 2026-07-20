from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from fastmcp import Client

from app.config import Settings


async def call(
    url: str,
    auth: str | None,
    answer_tool: str,
    evidence_tool: str,
    query: str,
) -> dict[str, Any]:
    async with Client(url, auth=auth) as client:
        names = {tool.name for tool in await client.list_tools()}
        if {answer_tool, evidence_tool} - names:
            raise RuntimeError(f"Missing expected tools at {url}: {sorted(names)}")
        answer = (await client.call_tool(answer_tool, {"query": query})).structured_content
        evidence = (await client.call_tool(evidence_tool, {"query": query})).structured_content
    if answer["status"] != "answered" or answer["verified"] is not True:
        raise RuntimeError(f"{answer_tool} smoke test failed: {answer}")
    if evidence["status"] != "retrieved" or not evidence["evidence"]:
        raise RuntimeError(f"{evidence_tool} smoke test failed: {evidence}")
    return {
        "answer_tool": answer_tool,
        "status": answer["status"],
        "co_code": answer["co_code"],
        "citations": len(answer["citations"]),
        "evidence": len(evidence["evidence"]),
    }


async def run(financial_query: str | None, transcript_query: str | None) -> None:
    settings = Settings()
    auth = settings.mcp_shared_token if settings.mcp_auth_mode == "static" else None
    if settings.data_mode == "mock":
        financial_query = financial_query or "範例科技 2026 Q2 revenue?"
        transcript_query = transcript_query or "範例科技 2026 Q2 法說會風險？"
    else:
        financial_query = financial_query or "Microsoft 2026 Q1 revenue?"
        transcript_query = transcript_query or "Microsoft 2026 Q1 法說會需求？"
    host = settings.mcp_server_host
    results = await asyncio.gather(
        call(
            f"http://{host}:{settings.rag_mcp_port}/mcp",
            auth,
            "ask_financial_rag",
            "retrieve_financial_evidence",
            financial_query,
        ),
        call(
            f"http://{host}:{settings.transcript_mcp_port}/mcp",
            auth,
            "ask_earnings_call",
            "retrieve_earnings_call_evidence",
            transcript_query,
        ),
    )
    print(json.dumps({"status": "ok", "results": results}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test both running public MCP services.")
    parser.add_argument("--financial-query")
    parser.add_argument("--transcript-query")
    args = parser.parse_args()
    asyncio.run(run(args.financial_query, args.transcript_query))


if __name__ == "__main__":
    main()
