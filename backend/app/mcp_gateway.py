from __future__ import annotations

import asyncio
import json
from typing import Any

from app.company_resolver import find_company_mentions
from app.config import Settings
from app.models import CompanySummary, Evidence, SourcePreview, ToolEnvelope
from app.repositories import (
    FinanceRepository,
    KnowledgeRepository,
    build_finance_repository,
    build_knowledge_repository,
)


class MCPGateway:
    """Typed gateway over MCP, with a direct adapter for tests and local debugging."""

    def __init__(
        self,
        settings: Settings,
        knowledge_repository: KnowledgeRepository | None = None,
        finance_repository: FinanceRepository | None = None,
    ):
        self.settings = settings
        self.knowledge = knowledge_repository or build_knowledge_repository(settings)
        self.finance = finance_repository or build_finance_repository(settings)
        self._tools: dict[str, Any] | None = None
        self._tools_lock = asyncio.Lock()

    async def _get_tools(self) -> dict[str, Any]:
        if self._tools is not None:
            return self._tools
        async with self._tools_lock:
            if self._tools is not None:
                return self._tools
            from langchain_mcp_adapters.client import MultiServerMCPClient

            headers = {"Authorization": f"Bearer {self.settings.mcp_shared_token}"}
            client = MultiServerMCPClient(
                {
                    "knowledge": {
                        "transport": "http",
                        "url": self.settings.knowledge_mcp_url,
                        "headers": headers,
                    },
                    "finance": {
                        "transport": "http",
                        "url": self.settings.finance_mcp_url,
                        "headers": headers,
                    },
                }
            )
            tools = await client.get_tools()
            self._tools = {tool.name: tool for tool in tools}
        return self._tools

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tools = await self._get_tools()
        if tool_name not in tools:
            raise RuntimeError(f"MCP tool not found: {tool_name}")
        raw = await tools[tool_name].ainvoke(arguments)
        return self._coerce_mapping(raw)

    @classmethod
    def _coerce_mapping(cls, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        if isinstance(raw, (list, tuple)):
            for block in raw:
                if isinstance(block, dict) and block.get("type") == "text":
                    return cls._coerce_mapping(block.get("text", ""))
                structured = getattr(block, "structured_content", None)
                if isinstance(structured, dict):
                    return structured
                try:
                    return cls._coerce_mapping(block)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        content = getattr(raw, "content", None)
        if content is not None:
            if isinstance(content, str):
                return cls._coerce_mapping(content)
            if isinstance(content, list):
                for block in content:
                    text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
                    if text:
                        return cls._coerce_mapping(text)
        artifact = getattr(raw, "artifact", None)
        structured = getattr(artifact, "structured_content", None)
        if isinstance(structured, dict):
            return structured
        raise TypeError(f"Unsupported MCP result type: {type(raw)!r}")

    async def search_documents(
        self, query: str, co_code: str, top_k: int = 5
    ) -> list[Evidence]:
        if not self.settings.mcp_enabled:
            return await self.knowledge.search_documents(query, co_code, top_k)
        payload = await self._call(
            "search_financial_documents",
            {"query": query, "co_code": co_code, "top_k": top_k},
        )
        return ToolEnvelope.model_validate(payload).evidence

    async def search_graph(
        self, query: str, co_code: str, max_hops: int = 2
    ) -> list[Evidence]:
        if not self.settings.mcp_enabled:
            return await self.knowledge.search_graph(query, co_code, max_hops)
        payload = await self._call(
            "search_graph_relationships",
            {"query": query, "co_code": co_code, "max_hops": max_hops},
        )
        return ToolEnvelope.model_validate(payload).evidence

    async def get_metrics(
        self, co_code: str, period: str | None = None
    ) -> list[Evidence]:
        if not self.settings.mcp_enabled:
            return await self.finance.get_metrics(co_code, period)
        payload = await self._call(
            "get_financial_metrics", {"co_code": co_code, "period": period}
        )
        return ToolEnvelope.model_validate(payload).evidence

    async def resolve_company(self, query: str) -> list[CompanySummary]:
        if not self.settings.mcp_enabled:
            items = await self.finance.list_companies()
            allowed = [item for item in items if self.settings.is_company_allowed(item.co_code)]
            return find_company_mentions(query, allowed)
        payload = await self._call("resolve_company", {"name_or_code": query})
        return [
            CompanySummary.model_validate(item)
            for item in payload.get("companies", [])
            if self.settings.is_company_allowed(str(item.get("co_code", "")))
        ]

    async def list_companies(self) -> list[CompanySummary]:
        if not self.settings.mcp_enabled:
            items = await self.finance.list_companies()
        else:
            payload = await self._call("list_companies", {})
            items = [
                CompanySummary.model_validate(item)
                for item in payload.get("companies", [])
            ]
        return [
            item for item in items if self.settings.is_company_allowed(item.co_code)
        ]

    async def get_source_preview(
        self, source_id: str, co_code: str
    ) -> SourcePreview | None:
        if not self.settings.mcp_enabled:
            preview = await self.knowledge.get_source_preview(source_id, co_code)
            return preview or await self.finance.get_source_preview(source_id, co_code)
        payload = await self._call(
            "get_source_preview", {"source_id": source_id, "co_code": co_code}
        )
        preview = payload.get("preview")
        if preview:
            return SourcePreview.model_validate(preview)
        payload = await self._call(
            "get_financial_source_preview",
            {"source_id": source_id, "co_code": co_code},
        )
        preview = payload.get("preview")
        return SourcePreview.model_validate(preview) if preview else None
