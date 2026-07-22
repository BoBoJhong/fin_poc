from __future__ import annotations

import asyncio
from typing import Any

from app.config import Settings
from app.mcp_contracts import VerifiedRAGResponse
from app.mcp_gateway import MCPGateway


TRANSCRIPT_TERMS = (
    "法說",
    "逐字稿",
    "管理層說",
    "發表人",
    "earnings call",
    "conference call",
    "transcript",
    "prepared remarks",
    "what did",
)


class PublicMCPChatService:
    """Frontend adapter that exercises the same public MCP tools as external agents."""

    def __init__(self, settings: Settings, source_gateway: MCPGateway):
        self.settings = settings
        self.gateway = source_gateway
        self._tools: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def _get_tools(self) -> dict[str, Any]:
        if self._tools is not None:
            return self._tools
        async with self._lock:
            if self._tools is not None:
                return self._tools
            from langchain_mcp_adapters.client import MultiServerMCPClient

            headers = {"Authorization": f"Bearer {self.settings.mcp_shared_token}"}
            client = MultiServerMCPClient(
                {
                    "financial_rag": {
                        "transport": "http",
                        "url": f"http://{self.settings.mcp_server_host}:{self.settings.rag_mcp_port}/mcp",
                        "headers": headers,
                    },
                    "earnings_call": {
                        "transport": "http",
                        "url": (
                            f"http://{self.settings.mcp_server_host}:"
                            f"{self.settings.transcript_mcp_port}/mcp"
                        ),
                        "headers": headers,
                    },
                }
            )
            tools = await client.get_tools()
            self._tools = {tool.name: tool for tool in tools}
        return self._tools

    @staticmethod
    def select_tool(query: str) -> str:
        lowered = query.casefold()
        if any(term in lowered for term in TRANSCRIPT_TERMS):
            return "ask_earnings_call"
        return "ask_financial_rag"

    async def answer(self, query: str, co_code: str | None = None) -> VerifiedRAGResponse:
        tool_name = self.select_tool(query)
        tools = await self._get_tools()
        tool_query = f"{co_code} {query}" if co_code else query
        raw = await tools[tool_name].ainvoke({"query": tool_query})
        payload = MCPGateway._coerce_mapping(raw)
        return VerifiedRAGResponse.model_validate(payload)
