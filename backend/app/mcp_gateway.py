from __future__ import annotations

import asyncio
import json
from typing import Any

from app.company_resolver import find_company_mentions, search_company_candidates
from app.config import Settings
from app.models import (
    CompanyCandidate,
    CompanySummary,
    EarningsCallRecord,
    Evidence,
    FiscalCalendar,
    SourcePreview,
    ToolEnvelope,
    TranscriptConversationPage,
)
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
        self._company_index: list[CompanySummary] | None = None

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
                    text = (
                        block.get("text")
                        if isinstance(block, dict)
                        else getattr(block, "text", None)
                    )
                    if text:
                        return cls._coerce_mapping(text)
        artifact = getattr(raw, "artifact", None)
        structured = getattr(artifact, "structured_content", None)
        if isinstance(structured, dict):
            return structured
        raise TypeError(f"Unsupported MCP result type: {type(raw)!r}")

    async def search_documents(
        self,
        query: str,
        co_code: str,
        top_k: int = 5,
        period: str | None = None,
        source_types: tuple[str, ...] | None = None,
    ) -> list[Evidence]:
        if not self.settings.mcp_enabled:
            return await self.knowledge.search_documents(
                query, co_code, top_k, period, source_types
            )
        payload = await self._call(
            "search_financial_documents",
            {
                "query": query,
                "co_code": co_code,
                "top_k": top_k,
                "period": period,
                "source_types": list(source_types) if source_types else None,
            },
        )
        return ToolEnvelope.model_validate(payload).evidence

    async def search_graph(
        self,
        query: str,
        co_code: str,
        max_hops: int = 2,
        period: str | None = None,
    ) -> list[Evidence]:
        if not self.settings.mcp_enabled:
            return await self.knowledge.search_graph(query, co_code, max_hops, period)
        payload = await self._call(
            "search_graph_relationships",
            {
                "query": query,
                "co_code": co_code,
                "max_hops": max_hops,
                "period": period,
            },
        )
        return ToolEnvelope.model_validate(payload).evidence

    async def get_metrics(self, co_code: str, period: str | None = None) -> list[Evidence]:
        if not self.settings.mcp_enabled:
            return await self.finance.get_metrics(co_code, period)
        payload = await self._call("get_financial_metrics", {"co_code": co_code, "period": period})
        return ToolEnvelope.model_validate(payload).evidence

    async def list_available_periods(
        self, co_code: str, retrieval_profile: str = "unified"
    ) -> list[str]:
        periods: set[str] = set()
        if retrieval_profile != "transcript":
            if self.settings.mcp_enabled:
                payload = await self._call("list_financial_periods", {"co_code": co_code})
                periods.update(str(item) for item in payload.get("periods", []))
            else:
                periods.update(await self.finance.list_periods(co_code))
        if retrieval_profile != "financial":
            source_types = ["transcript"] if retrieval_profile == "transcript" else None
            if self.settings.mcp_enabled:
                payload = await self._call(
                    "list_document_periods",
                    {"co_code": co_code, "source_types": source_types},
                )
                periods.update(str(item) for item in payload.get("periods", []))
            else:
                periods.update(
                    await self.knowledge.list_periods(
                        co_code, tuple(source_types) if source_types else None
                    )
                )
        return sorted(periods)

    async def get_fiscal_calendar(self, co_code: str) -> FiscalCalendar | None:
        if not self.settings.mcp_enabled:
            return await self.finance.get_fiscal_calendar(co_code)
        payload = await self._call("get_fiscal_calendar", {"co_code": co_code})
        item = payload.get("fiscal_calendar")
        return FiscalCalendar.model_validate(item) if item else None

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

    async def search_company_candidates(
        self, query: str, limit: int = 10
    ) -> list[CompanyCandidate]:
        if not self.settings.mcp_enabled:
            if self._company_index is None:
                self._company_index = await self.finance.list_companies()
            allowed = [
                item
                for item in self._company_index
                if self.settings.is_company_allowed(item.co_code)
            ]
            return search_company_candidates(query, allowed, limit)
        payload = await self._call(
            "search_company_candidates",
            {"name_or_code": query, "limit": limit},
        )
        return [CompanyCandidate.model_validate(item) for item in payload.get("candidates", [])]

    async def list_companies(self) -> list[CompanySummary]:
        if not self.settings.mcp_enabled:
            items = await self.finance.list_companies()
        else:
            payload = await self._call("list_companies", {})
            items = [CompanySummary.model_validate(item) for item in payload.get("companies", [])]
        return [item for item in items if self.settings.is_company_allowed(item.co_code)]

    async def get_source_preview(self, source_id: str, co_code: str) -> SourcePreview | None:
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

    async def get_transcript_conversation(
        self,
        co_code: str,
        period: str | None = None,
        cursor: int = 0,
        limit: int = 20,
    ) -> TranscriptConversationPage | None:
        if not self.settings.mcp_enabled:
            return await self.knowledge.get_transcript_conversation(co_code, period, cursor, limit)
        payload = await self._call(
            "read_earnings_call_transcript",
            {
                "co_code": co_code,
                "period": period,
                "cursor": cursor,
                "limit": limit,
            },
        )
        page = payload.get("transcript")
        return TranscriptConversationPage.model_validate(page) if page else None

    async def list_earnings_calls(
        self, co_code: str, limit: int = 20
    ) -> list[EarningsCallRecord]:
        bounded_limit = min(max(limit, 1), 20)
        if not self.settings.mcp_enabled:
            return await self.knowledge.list_earnings_calls(co_code, bounded_limit)
        payload = await self._call(
            "list_earnings_call_records",
            {"co_code": co_code, "limit": bounded_limit},
        )
        return [EarningsCallRecord.model_validate(item) for item in payload.get("calls", [])]
