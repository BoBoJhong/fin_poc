from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from fastmcp import Client

from app.config import Settings
from app.mcp_contracts import MCP_SCHEMA_VERSION, MCP_TOOL_CONTRACT_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def inspect_endpoint(
    name: str, url: str, auth: str | None, server: Any | None = None
) -> dict[str, Any]:
    async with Client(server or url, auth=None if server is not None else auth) as client:
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


async def export(output: Path, in_process: bool = False) -> None:
    settings = Settings()
    auth = settings.mcp_shared_token if settings.mcp_auth_mode == "static" else None
    host = settings.mcp_server_host
    local_servers: tuple[Any | None, Any | None] = (None, None)
    if in_process:
        from mcp_servers.rag import create_rag_mcp
        from mcp_servers.transcript import create_transcript_mcp

        local_settings = Settings(data_mode="mock", mcp_enabled=False, mcp_auth_mode="none")
        local_servers = (
            create_rag_mcp(local_settings),
            create_transcript_mcp(local_settings),
        )
    servers = await asyncio.gather(
        inspect_endpoint(
            "verified_financial_rag",
            f"http://{host}:{settings.rag_mcp_port}/mcp",
            auth,
            local_servers[0],
        ),
        inspect_endpoint(
            "verified_earnings_call",
            f"http://{host}:{settings.transcript_mcp_port}/mcp",
            auth,
            local_servers[1],
        ),
    )
    payload = {
        "tool_contract_version": MCP_TOOL_CONTRACT_VERSION,
        "response_schema_version": MCP_SCHEMA_VERSION,
        "servers": servers,
    }
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
    parser.add_argument(
        "--in-process",
        action="store_true",
        help="Export from mock in-process public MCP servers without running network services.",
    )
    args = parser.parse_args()
    asyncio.run(export(args.output.resolve(), in_process=args.in_process))


if __name__ == "__main__":
    main()
