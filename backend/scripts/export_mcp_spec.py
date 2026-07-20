from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from fastmcp import Client

from app.config import Settings
from app.mcp_contracts import MCP_SCHEMA_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def inspect_endpoint(name: str, url: str, auth: str | None) -> dict[str, Any]:
    async with Client(url, auth=auth) as client:
        tools = await client.list_tools()
    return {
        "name": name,
        "url": url,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
                "outputSchema": tool.outputSchema,
            }
            for tool in tools
        ],
    }


async def export(output: Path) -> None:
    settings = Settings()
    auth = settings.mcp_shared_token if settings.mcp_auth_mode == "static" else None
    host = settings.mcp_server_host
    servers = await asyncio.gather(
        inspect_endpoint(
            "verified_financial_rag",
            f"http://{host}:{settings.rag_mcp_port}/mcp",
            auth,
        ),
        inspect_endpoint(
            "verified_earnings_call",
            f"http://{host}:{settings.transcript_mcp_port}/mcp",
            auth,
        ),
    )
    payload = {"schema_version": MCP_SCHEMA_VERSION, "servers": servers}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"MCP tool schemas written to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export tool schemas from the running public MCP services."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "docs" / "mcp-tools.json",
    )
    args = parser.parse_args()
    asyncio.run(export(args.output.resolve()))


if __name__ == "__main__":
    main()
