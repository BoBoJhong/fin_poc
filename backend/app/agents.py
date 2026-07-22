from __future__ import annotations

import asyncio
import re
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.company_resolver import CompanyResolutionError, resolve_company_scope
from app.llm import CompanyLLMClient
from app.mcp_gateway import MCPGateway
from app.models import ChatResponse, Citation, Evidence
from app.period_resolver import canonical_fiscal_label, has_relative_period, resolve_period
from app.validation import EvidenceValidator


class AgentState(TypedDict, total=False):
    query: str
    co_code: str | None
    trace_id: str
    routes: list[str]
    period: str | None
    period_resolution: dict[str, Any]
    evidence: list[Evidence]
    answer: str
    verification: dict[str, Any]
    company_resolution: dict[str, Any]
    repaired: bool


class FinancialAgentService:
    """A bounded workflow: the LLM can route, but cannot invent tools or database queries."""

    def __init__(
        self,
        gateway: MCPGateway,
        llm: CompanyLLMClient,
        validator: EvidenceValidator,
        max_evidence_items: int = 8,
        retrieval_profile: str = "unified",
    ):
        self.gateway = gateway
        self.llm = llm
        self.validator = validator
        self.max_evidence_items = max(1, min(max_evidence_items, 20))
        if retrieval_profile not in {"unified", "financial", "transcript"}:
            raise ValueError(f"Unsupported retrieval profile: {retrieval_profile}")
        self.retrieval_profile = retrieval_profile
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("company_resolver", self._scope_node)
        builder.add_node("main_agent_router", self._route_node)
        builder.add_node("knowledge_graphrag_subagent", self._knowledge_node)
        builder.add_node("finance_db_subagent", self._data_node)
        builder.add_node("evidence_aggregator", self._aggregate_node)
        builder.add_node("evidence_validator", self._validate_node)
        builder.add_node("answer_draft", self._answer_node)
        builder.add_node("answer_deterministic_validator", self._answer_validate_node)
        builder.add_node("semantic_verifier", self._verify_node)

        builder.add_edge(START, "company_resolver")
        builder.add_edge("company_resolver", "main_agent_router")
        builder.add_edge("main_agent_router", "knowledge_graphrag_subagent")
        builder.add_edge("knowledge_graphrag_subagent", "finance_db_subagent")
        builder.add_edge("finance_db_subagent", "evidence_aggregator")
        builder.add_edge("evidence_aggregator", "evidence_validator")
        builder.add_edge("evidence_validator", "answer_draft")
        builder.add_edge("answer_draft", "answer_deterministic_validator")
        builder.add_edge("answer_deterministic_validator", "semantic_verifier")
        builder.add_edge("semantic_verifier", END)
        return builder.compile()

    async def _scope_node(self, state: AgentState) -> AgentState:
        requested_code = state.get("co_code")
        selected_code = self.validator.validate_scope(requested_code) if requested_code else None
        mentioned_companies = await self.gateway.resolve_company(state["query"])
        resolution_method = "company_master"
        resolution_status = "matched" if mentioned_companies else "not_mentioned"
        resolution_reason = "deterministic_name_alias_or_code_match"
        if not mentioned_companies:
            candidates = await self.gateway.search_company_candidates(state["query"], limit=10)
            if (
                candidates
                and candidates[0].score >= 0.92
                and (len(candidates) == 1 or candidates[0].score - candidates[1].score >= 0.08)
            ):
                mentioned_companies = [candidates[0].company]
                resolution_status = "matched"
                resolution_reason = "high_confidence_fuzzy_company_index"
                resolution_method = "company_entity_index"
            companies = [candidate.company for candidate in candidates]
        if not mentioned_companies:
            semantic_resolution = await self.llm.resolve_company_reference(
                state["query"], companies
            )
            resolution_status = semantic_resolution["status"]
            resolution_reason = semantic_resolution["reason"]
            mentioned_companies = semantic_resolution["companies"]
            resolution_method = "company_llm_constrained"
            if resolution_status == "unknown":
                raise CompanyResolutionError(
                    "問題似乎提到公司，但無法對應到允許的公司主檔；請改用正式名稱或代碼"
                )
            if resolution_status == "ambiguous" and len(mentioned_companies) < 2:
                raise CompanyResolutionError("公司名稱不明確；請改用正式名稱或代碼")
        scoped = resolve_company_scope(selected_code, mentioned_companies)
        scoped = self.validator.validate_scope(scoped)
        calendar = await self.gateway.get_fiscal_calendar(scoped)
        available_periods = (
            await self.gateway.list_available_periods(scoped, self.retrieval_profile)
            if has_relative_period(state["query"])
            else []
        )
        period_resolution = resolve_period(
            state["query"], available_periods, fiscal_calendar=calendar
        )
        period = period_resolution.resolved_period
        return {
            "co_code": scoped,
            "period": period,
            "period_resolution": period_resolution.model_dump(mode="json"),
            "evidence": [],
            "company_resolution": {
                "passed": True,
                "method": resolution_method,
                "status": resolution_status,
                "reason": resolution_reason,
                "co_code": scoped,
                "selected_co_code": selected_code,
                "selection_overridden": bool(selected_code and scoped != selected_code),
                "mentioned_co_codes": [company.co_code for company in mentioned_companies],
            },
        }

    async def _route_node(self, state: AgentState) -> AgentState:
        if self.retrieval_profile == "transcript":
            return {"routes": ["knowledge"]}
        routes = await self.llm.route(state["query"])
        return {"routes": routes}

    async def _knowledge_node(self, state: AgentState) -> AgentState:
        if "knowledge" not in state["routes"]:
            return {}
        period_resolution = state.get("period_resolution", {})
        if period_resolution.get("input") and not period_resolution.get("resolved_period"):
            return {}
        source_types = {
            "financial": ("financial_report", "url"),
            "transcript": ("transcript",),
        }.get(self.retrieval_profile)
        documents = await self.gateway.search_documents(
            state["query"],
            state["co_code"],
            top_k=5,
            period=state.get("period"),
            source_types=source_types,
        )
        graph = (
            await self.gateway.search_graph(
                state["query"], state["co_code"], max_hops=2, period=state.get("period")
            )
            if self.retrieval_profile == "unified"
            else []
        )
        return {"evidence": [*state.get("evidence", []), *documents, *graph]}

    async def _data_node(self, state: AgentState) -> AgentState:
        if "finance" not in state["routes"] or self.retrieval_profile == "transcript":
            return {}
        period_resolution = state.get("period_resolution", {})
        if period_resolution.get("input") and not period_resolution.get("resolved_period"):
            return {}
        items = await self.gateway.get_metrics(state["co_code"], state.get("period"))
        return {"evidence": [*state.get("evidence", []), *items]}

    async def _aggregate_node(self, state: AgentState) -> AgentState:
        unique: dict[str, Evidence] = {}
        for item in state.get("evidence", []):
            current = unique.get(item.evidence_id)
            if current is None or item.score > current.score:
                unique[item.evidence_id] = item
        return {"evidence": list(unique.values())}

    @staticmethod
    def _metric_query_rank(item: Evidence, query: str) -> tuple[int, float]:
        if item.source_type != "database":
            return (0, item.score)
        lowered = query.casefold()
        terms = [
            str(item.metadata.get("metric_code", "")).replace("_", " "),
            str(item.metadata.get("metric_display_name", "")),
            str(item.metadata.get("provider_metric_key", "")).split(".")[-1],
            *(str(value) for value in item.metadata.get("metric_aliases", [])),
        ]
        matches = [term for term in terms if term and term.casefold() in lowered]
        return (max((len(term) for term in matches), default=0), item.score)

    def _select_diverse_evidence(self, items: list[Evidence], query: str) -> list[Evidence]:
        """Prevent one retriever from crowding structured or document evidence out."""
        ranked = sorted(
            items,
            key=lambda item: self._metric_query_rank(item, query),
            reverse=True,
        )
        buckets: dict[str, list[Evidence]] = {
            "database": [],
            "financial_report": [],
            "transcript": [],
            "other_document": [],
            "graph": [],
        }
        for item in ranked:
            if item.source_type == "database":
                buckets["database"].append(item)
            elif item.source_type == "graph":
                buckets["graph"].append(item)
            elif item.source_type == "financial_report":
                buckets["financial_report"].append(item)
            elif item.source_type == "transcript":
                buckets["transcript"].append(item)
            else:
                buckets["other_document"].append(item)

        selected: list[Evidence] = []
        # Reserve capacity for every available retrieval family, then fill by score.
        reservations = {
            "database": 3,
            "financial_report": 2,
            "transcript": 2,
            "other_document": 0,
            "graph": 1,
        }
        for family in (
            "database",
            "financial_report",
            "transcript",
            "other_document",
            "graph",
        ):
            capacity = min(reservations[family], self.max_evidence_items - len(selected))
            selected.extend(buckets[family][:capacity])
            buckets[family] = buckets[family][capacity:]
        remaining = sorted(
            [item for bucket in buckets.values() for item in bucket],
            key=lambda item: item.score,
            reverse=True,
        )
        selected.extend(remaining[: self.max_evidence_items - len(selected)])
        return selected

    async def _validate_node(self, state: AgentState) -> AgentState:
        valid = self.validator.validate_evidence(
            state["co_code"], state.get("evidence", []), state.get("period")
        )
        valid = self._select_diverse_evidence(valid, state["query"])
        return {
            "evidence": valid,
            "verification": {
                "company_resolution": state.get("company_resolution", {}),
                "evidence": {
                    "passed": bool(valid),
                    "reason": "evidence_contract_valid" if valid else "no_evidence",
                    "evidence_count": len(valid),
                    "minimum_document_score": self.validator.document_min_relevance_score,
                    "minimum_graph_score": self.validator.graph_min_relevance_score,
                    "max_evidence_items": self.max_evidence_items,
                },
            },
        }

    async def _answer_node(self, state: AgentState) -> AgentState:
        verification = state.get("verification", {})
        if not verification.get("evidence", {}).get("passed", False):
            return {
                "answer": "目前找不到足以回答此問題的授權來源，因此不產生推測性答案。",
                "verification": {**verification, "passed": False},
            }
        answer = await self.llm.synthesize(
            state["query"], state["co_code"], state.get("evidence", [])
        )
        return {"answer": answer}

    async def _answer_validate_node(self, state: AgentState) -> AgentState:
        evidence = state.get("evidence", [])
        answer = state["answer"]
        answer_check = self.validator.verify_answer(answer, evidence)
        passed = bool(answer_check["passed"])
        repaired = False

        if not passed and evidence:
            repaired = True
            answer = await self.llm.synthesize(
                state["query"], state["co_code"], evidence, repair=True
            )
            answer_check = self.validator.verify_answer(answer, evidence)
            passed = bool(answer_check["passed"])

        return {
            "answer": answer,
            "repaired": repaired,
            "verification": {
                **state.get("verification", {}),
                "passed": passed,
                "answer": answer_check,
                "repair_attempted": repaired,
            },
        }

    async def _verify_node(self, state: AgentState) -> AgentState:
        evidence = state.get("evidence", [])
        verification = state.get("verification", {})
        answer = state["answer"]
        if not evidence:
            policy = {
                "accepted": False,
                "level": "rejected",
                "gates": {
                    "company_resolved": bool(
                        verification.get("company_resolution", {}).get("passed")
                    ),
                    "evidence_available": False,
                    "deterministic_answer_check": False,
                    "semantic_answer_check": False,
                },
            }
            return {
                "answer": answer,
                "verification": {
                    **verification,
                    "passed": False,
                    "semantic": {"passed": False, "reason": "no_evidence"},
                    "repair_attempted": False,
                    "reliability_policy": policy,
                },
            }
        deterministic_passed = bool(verification.get("answer", {}).get("passed"))
        semantic = (
            await self.llm.semantic_verify(answer, evidence)
            if deterministic_passed
            else {"passed": False, "reason": "answer_deterministic_check_failed"}
        )
        passed = bool(deterministic_passed and semantic["passed"])
        repaired = bool(state.get("repaired", False))

        if not passed and evidence and not repaired:
            repaired = True
            answer = await self.llm.synthesize(
                state["query"], state["co_code"], evidence, repair=True
            )
            answer_check = self.validator.verify_answer(answer, evidence)
            semantic = (
                await self.llm.semantic_verify(answer, evidence)
                if answer_check["passed"]
                else {"passed": False, "reason": "repair_deterministic_check_failed"}
            )
            passed = bool(answer_check["passed"] and semantic["passed"])
            verification = {**verification, "answer": answer_check}

        if not passed:
            answer = (
                "來源或答案驗證未通過，因此系統拒絕輸出可能誤導的答案。請查看 Trace ID 後重試。"
            )

        policy = {
            "accepted": passed,
            "level": "high_guardrail_pass" if passed else "rejected",
            "gates": {
                "company_resolved": bool(verification.get("company_resolution", {}).get("passed")),
                "evidence_available": bool(evidence),
                "deterministic_answer_check": bool(verification.get("answer", {}).get("passed")),
                "semantic_answer_check": bool(semantic.get("passed")),
            },
            "evidence_count": len(evidence),
            "lowest_evidence_score": min(item.score for item in evidence),
        }

        return {
            "answer": answer,
            "repaired": repaired,
            "verification": {
                **verification,
                "passed": passed,
                "semantic": semantic,
                "repair_attempted": repaired,
                "reliability_policy": policy,
            },
        }

    async def answer(self, query: str, co_code: str | None = None) -> ChatResponse:
        trace_id = str(uuid4())
        final: AgentState = await self.graph.ainvoke(
            {
                "query": query,
                "co_code": co_code,
                "trace_id": trace_id,
                "evidence": [],
                "routes": [],
                "repaired": False,
            }
        )
        evidence = final.get("evidence", [])
        cited_indices = {int(value) for value in re.findall(r"\[(\d+)]", final["answer"])}
        cited_evidence = [
            item for index, item in enumerate(evidence, start=1) if index in cited_indices
        ]
        citations = [
            Citation(
                index=index,
                evidence_id=item.evidence_id,
                co_code=item.co_code,
                source_id=item.source_id,
                title=item.title,
                source_type=item.source_type,
                locator=item.locator,
                quoted_text=item.content,
                period=item.period,
                metadata={
                    key: item.metadata[key]
                    for key in ("speaker", "speakers", "section", "fiscal_label", "event_date")
                    if item.metadata.get(key) is not None
                },
            )
            for index, item in enumerate(evidence, start=1)
            if index in cited_indices
        ]
        return ChatResponse(
            answer=final["answer"],
            co_code=final["co_code"],
            citations=citations,
            trace_id=trace_id,
            routes=final.get("routes", []),
            verification=final.get("verification", {}),
            data_versions=sorted({item.data_version for item in cited_evidence}),
            period_resolution=final.get("period_resolution"),
        )

    async def retrieve_evidence(self, query: str, co_code: str | None = None) -> dict[str, Any]:
        """Retrieve and validate evidence without answer generation or semantic LLM calls."""
        selected_code = self.validator.validate_scope(co_code) if co_code else None
        mentioned = await self.gateway.resolve_company(query)
        scoped = resolve_company_scope(selected_code, mentioned)
        scoped = self.validator.validate_scope(scoped)
        calendar = await self.gateway.get_fiscal_calendar(scoped)
        available_periods = (
            await self.gateway.list_available_periods(scoped, self.retrieval_profile)
            if has_relative_period(query)
            else []
        )
        period_resolution = resolve_period(query, available_periods, fiscal_calendar=calendar)
        period = period_resolution.resolved_period
        if self.retrieval_profile == "transcript":
            routes = ["knowledge"]
        elif self.retrieval_profile == "financial":
            routes = ["knowledge", "finance"]
        else:
            routes = CompanyLLMClient._heuristic_routes(query)
        state: AgentState = {
            "query": query,
            "co_code": scoped,
            "period": period,
            "period_resolution": period_resolution.model_dump(mode="json"),
            "routes": routes,
            "evidence": [],
            "company_resolution": {
                "passed": True,
                "method": "company_master",
                "co_code": scoped,
            },
        }
        state.update(await self._knowledge_node(state))
        state.update(await self._data_node(state))
        state.update(await self._aggregate_node(state))
        state.update(await self._validate_node(state))
        return {
            "co_code": scoped,
            "period": period,
            "routes": routes,
            "evidence": state.get("evidence", []),
            "verification": state.get("verification", {}),
            "period_resolution": period_resolution.model_dump(mode="json"),
        }

    async def retrieve_transcript_conversation(
        self,
        query: str,
        co_code: str | None = None,
        cursor: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Resolve one call, then read ordered speaker turns without semantic ranking."""
        state = await self._scope_node({"query": query, "co_code": co_code})
        scoped = str(state["co_code"])
        resolution = dict(state["period_resolution"])
        period = state.get("period")
        if period is None and resolution.get("method") == "not_specified":
            available = await self.gateway.list_available_periods(scoped, "transcript")
            calendar = await self.gateway.get_fiscal_calendar(scoped)
            latest = resolve_period("最近一季", available, fiscal_calendar=calendar)
            period = latest.resolved_period
            resolution = latest.model_dump(mode="json")
        if resolution.get("input") and not period:
            return {
                "co_code": scoped,
                "page": None,
                "period_resolution": resolution,
            }
        period_or_fiscal_label = canonical_fiscal_label(query) or period
        page = await self.gateway.get_transcript_conversation(
            scoped,
            period_or_fiscal_label,
            max(cursor, 0),
            min(max(limit, 1), 50),
        )
        return {"co_code": scoped, "page": page, "period_resolution": resolution}

    async def list_earnings_calls(
        self, query: str, co_code: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        """Resolve one company and list its available calls deterministically."""
        state = await self._scope_node({"query": query, "co_code": co_code})
        scoped = str(state["co_code"])
        calls = await self.gateway.list_earnings_calls(scoped, min(max(limit, 1), 20))
        return {"co_code": scoped, "calls": calls}

    async def retrieve_multi_period_transcript_evidence(
        self,
        query: str,
        co_code: str | None = None,
        quarters: list[str] | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        """Retrieve separately scoped evidence for several calls without mixing periods."""
        state = await self._scope_node({"query": query, "co_code": co_code})
        scoped = str(state["co_code"])
        calls = await self.gateway.list_earnings_calls(scoped, 20)
        requested = [value.strip() for value in (quarters or []) if value.strip()]
        if len(requested) > 4:
            raise ValueError("At most four earnings-call quarters may be compared at once.")
        if requested:
            lookup = {
                key.casefold(): call
                for call in calls
                for key in (call.period, call.quarter)
            }
            missing = [value for value in requested if value.casefold() not in lookup]
            if missing:
                available = ", ".join(call.quarter for call in calls) or "none"
                raise ValueError(
                    f"Unknown earnings-call quarter(s): {', '.join(missing)}. "
                    f"Available: {available}."
                )
            selected_calls = []
            selected_source_ids: set[str] = set()
            for value in requested:
                call = lookup[value.casefold()]
                if call.source_id not in selected_source_ids:
                    selected_calls.append(call)
                    selected_source_ids.add(call.source_id)
        else:
            selected_calls = calls[: min(max(limit, 1), 4)]

        broad_summary = bool(
            re.search(r"重點|摘要|總結|幾(?:個)?季|幾個季度|分別|highlights?|summar", query, re.I)
        )
        coverage_queries = (
            [
                f"{query} 財務表現、營運與產品重點",
                f"{query} 策略、需求與成長動能",
                f"{query} 展望、指引、風險與資本支出",
                f"{query} 分析師問答的重要問題與管理層回答",
            ]
            if broad_summary
            else [query]
        )

        async def retrieve(call: Any) -> dict[str, Any]:
            batches = await asyncio.gather(
                *(
                    self.gateway.search_documents(
                        facet,
                        scoped,
                        top_k=3 if broad_summary else 5,
                        period=call.period,
                        source_types=("transcript",),
                    )
                    for facet in coverage_queries
                )
            )
            unique: dict[str, Evidence] = {}
            for item in self.validator.validate_evidence(
                scoped,
                (item for batch in batches for item in batch),
                call.period,
            ):
                current = unique.get(item.evidence_id)
                if current is None or item.score > current.score:
                    unique[item.evidence_id] = item
            evidence = sorted(unique.values(), key=lambda item: item.score, reverse=True)[
                : self.max_evidence_items
            ]
            return {
                "call": call,
                "evidence": evidence,
                "coverage_mode": (
                    "broad_facet_retrieval" if broad_summary else "topic_retrieval"
                ),
                "coverage_queries": coverage_queries,
            }

        groups = await asyncio.gather(*(retrieve(call) for call in selected_calls))
        return {"co_code": scoped, "groups": groups}
