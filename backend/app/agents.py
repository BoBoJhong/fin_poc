from __future__ import annotations

import re
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.llm import CompanyLLMClient
from app.mcp_gateway import MCPGateway
from app.models import ChatResponse, Citation, Evidence
from app.validation import EvidenceValidator


class AgentState(TypedDict, total=False):
    query: str
    co_code: str
    trace_id: str
    routes: list[str]
    period: str | None
    evidence: list[Evidence]
    answer: str
    verification: dict[str, Any]
    repaired: bool


class FinancialAgentService:
    """A bounded workflow: the LLM can route, but cannot invent tools or database queries."""

    def __init__(
        self,
        gateway: MCPGateway,
        llm: CompanyLLMClient,
        validator: EvidenceValidator,
    ):
        self.gateway = gateway
        self.llm = llm
        self.validator = validator
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
        scoped = self.validator.validate_scope(state["co_code"])
        mentioned_codes = {
            code for code in self.validator.allowed_co_codes if code in state["query"].upper()
        }
        if mentioned_codes and mentioned_codes != {scoped}:
            raise ValueError(
                f"問題中的公司 {sorted(mentioned_codes)} 與目前授權公司 {scoped} 不一致"
            )
        match = re.search(r"(20\d{2})\s*[-_/ ]?Q([1-4])", state["query"], re.IGNORECASE)
        period = f"{match.group(1)}Q{match.group(2)}" if match else None
        return {"co_code": scoped, "period": period, "evidence": []}

    async def _route_node(self, state: AgentState) -> AgentState:
        routes = await self.llm.route(state["query"])
        return {"routes": routes}

    async def _knowledge_node(self, state: AgentState) -> AgentState:
        if "knowledge" not in state["routes"]:
            return {}
        documents = await self.gateway.search_documents(
            state["query"], state["co_code"], top_k=5
        )
        graph = await self.gateway.search_graph(
            state["query"], state["co_code"], max_hops=2
        )
        return {"evidence": [*state.get("evidence", []), *documents, *graph]}

    async def _data_node(self, state: AgentState) -> AgentState:
        if "finance" not in state["routes"]:
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

    async def _validate_node(self, state: AgentState) -> AgentState:
        valid = self.validator.validate_evidence(
            state["co_code"], state.get("evidence", []), state.get("period")
        )
        valid.sort(key=lambda item: item.score, reverse=True)
        return {
            "evidence": valid,
            "verification": {
                "evidence": {
                    "passed": bool(valid),
                    "reason": "evidence_contract_valid" if valid else "no_evidence",
                    "evidence_count": len(valid),
                }
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
            return {
                "answer": answer,
                "verification": {
                    **verification,
                    "passed": False,
                    "semantic": {"passed": False, "reason": "no_evidence"},
                    "repair_attempted": False,
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
            answer = "來源或答案驗證未通過，因此系統拒絕輸出可能誤導的答案。請查看 Trace ID 後重試。"

        return {
            "answer": answer,
            "repaired": repaired,
            "verification": {
                **verification,
                "passed": passed,
                "semantic": semantic,
                "repair_attempted": repaired,
            },
        }

    async def answer(self, query: str, co_code: str) -> ChatResponse:
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
        citations = [
            Citation(
                index=index,
                evidence_id=item.evidence_id,
                source_id=item.source_id,
                title=item.title,
                source_type=item.source_type,
                locator=item.locator,
            )
            for index, item in enumerate(evidence, start=1)
        ]
        return ChatResponse(
            answer=final["answer"],
            co_code=final["co_code"],
            citations=citations,
            trace_id=trace_id,
            routes=final.get("routes", []),
            verification=final.get("verification", {}),
            data_versions=sorted({item.data_version for item in evidence}),
        )
