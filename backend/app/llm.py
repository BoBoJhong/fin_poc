from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import Settings
from app.models import CompanySummary, Evidence


class CompanyLLMClient:
    """Company LLM boundary; replace only this adapter when the API contract arrives."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def resolve_company_reference(
        self, query: str, companies: list[CompanySummary]
    ) -> dict[str, Any]:
        """Use the LLM only to select codes from the supplied company master."""
        if self.settings.company_llm_mode == "mock":
            return {"status": "not_mentioned", "companies": [], "reason": "mock_mode"}
        candidates = [
            {
                "co_code": company.co_code,
                "company_name": company.company_name,
                "aliases": company.aliases,
            }
            for company in companies
        ]
        content = await self._chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Identify explicit company references in the question. Select only from "
                        "COMPANY_MASTER and never invent a co_code. Return JSON only with "
                        'status as "matched", "ambiguous", "unknown", or "not_mentioned"; '
                        'co_codes as an array; and reason as a short string. Use "unknown" when '
                        "a company is explicitly named but is absent from COMPANY_MASTER."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"query": query, "company_master": candidates},
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        parsed = self._parse_json(content)
        status = str(parsed.get("status", "unknown"))
        if status not in {"matched", "ambiguous", "unknown", "not_mentioned"}:
            status = "unknown"
        master = {company.co_code: company for company in companies}
        codes = [
            str(code).strip().upper()
            for code in parsed.get("co_codes", [])
            if str(code).strip().upper() in master
        ]
        selected = [master[code] for code in dict.fromkeys(codes)]
        if status == "matched" and len(selected) != 1:
            status = "ambiguous" if selected else "unknown"
        if status == "not_mentioned":
            selected = []
        return {
            "status": status,
            "companies": selected,
            "reason": str(parsed.get("reason", "company_llm_resolution")),
        }

    async def route(self, query: str) -> list[str]:
        if self.settings.company_llm_mode == "mock":
            return self._heuristic_routes(query)
        content = await self._chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You route a financial question. Return JSON only: "
                        '{"routes":["knowledge","finance"]}. '
                        "Allowed routes are knowledge and finance. Select only necessary routes. "
                        "knowledge covers document vector retrieval and graph expansion."
                    ),
                },
                {"role": "user", "content": query},
            ]
        )
        parsed = self._parse_json(content)
        routes = [
            route
            for route in parsed.get("routes", [])
            if route in {"knowledge", "finance"}
        ]
        return routes or ["knowledge"]

    @staticmethod
    def _heuristic_routes(query: str) -> list[str]:
        lowered = query.lower()
        routes: list[str] = []
        data_terms = ["營收", "毛利", "eps", "數字", "多少", "比較", "成長率", "財務指標"]
        graph_terms = ["關聯", "影響", "風險", "供應鏈", "產品", "客戶", "上下游"]
        rag_terms = ["財報", "法說", "逐字稿", "說明", "原因", "展望", "風險"]
        if any(term in lowered for term in data_terms):
            routes.append("finance")
        if any(term in lowered for term in rag_terms) or not routes:
            routes.append("knowledge")
        if any(term in lowered for term in graph_terms):
            routes.append("knowledge")
        return list(dict.fromkeys(routes))

    async def synthesize(
        self, query: str, co_code: str, evidence: list[Evidence], repair: bool = False
    ) -> str:
        if not evidence:
            return "目前找不到足以回答此問題的授權來源，因此不產生推測性答案。"
        if self.settings.company_llm_mode == "mock":
            return self._mock_synthesis(query, co_code, evidence)

        evidence_payload = [
            {
                "citation": index,
                "evidence_id": item.evidence_id,
                "content": item.content,
                "period": item.period,
                "source_type": item.source_type,
                "locator": item.locator.model_dump(exclude_none=True),
                "metadata": item.metadata,
            }
            for index, item in enumerate(evidence, start=1)
        ]
        system = (
            "你是金融資料問答系統。只能依據 EVIDENCE 回答；每個事實緊接 [n] 引註。"
            "數字必須保留期間、單位與範圍。資料不足要明說，不可使用模型記憶補齊。"
        )
        if repair:
            system += "上一版未通過驗證，請刪除所有無證據主張。"
        return await self._chat(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"query": query, "co_code": co_code, "evidence": evidence_payload},
                        ensure_ascii=False,
                    ),
                },
            ]
        )

    @staticmethod
    def _mock_synthesis(query: str, co_code: str, evidence: list[Evidence]) -> str:
        del query
        metric_claims: list[str] = []
        qualitative: list[str] = []
        graph_claims: list[str] = []
        for index, item in enumerate(evidence, start=1):
            citation = f"[{index}]"
            if item.source_type == "database":
                metric_claims.append(f"{item.content}{citation}")
            elif item.source_type == "graph":
                graph_claims.append(f"{item.content}{citation}")
            else:
                qualitative.append(f"{item.content}{citation}")

        sections = [f"以下回答僅依據 {co_code} 的 PoC 授權資料（內建資料為虛構）："]
        if metric_claims:
            sections.append("\n**財務數據**\n" + "\n".join(f"- {x}" for x in metric_claims))
        if qualitative:
            sections.append("\n**法說／文件重點**\n" + "\n".join(f"- {x}" for x in qualitative))
        if graph_claims:
            sections.append("\n**GraphRAG 關聯**\n" + "\n".join(f"- {x}" for x in graph_claims))
        sections.append("\n這些資料只能支持來源回查，不構成投資建議或未來預測。")
        return "\n".join(sections)

    async def semantic_verify(self, answer: str, evidence: list[Evidence]) -> dict[str, Any]:
        if self.settings.company_llm_mode == "mock":
            cited = {int(value) for value in re.findall(r"\[(\d+)]", answer)}
            return {
                "passed": bool(cited) and max(cited, default=0) <= len(evidence),
                "reason": "mock_citation_check",
            }
        content = await self._chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Check every factual claim only against the evidence indices cited next "
                        "to that claim. A claim supported by uncited evidence must fail. "
                        'Return JSON only: {"passed":true,"reason":"..."}.'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "answer": answer,
                            "evidence": [
                                {
                                    "citation": index,
                                    "evidence_id": item.evidence_id,
                                    "content": item.content,
                                }
                                for index, item in enumerate(evidence, start=1)
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        parsed = self._parse_json(content)
        return {
            "passed": bool(parsed.get("passed", False)),
            "reason": str(parsed.get("reason", "company_llm_verifier")),
        }

    async def _chat(self, messages: list[dict[str, str]]) -> str:
        if not self.settings.company_llm_api_key:
            raise RuntimeError("COMPANY_LLM_API_KEY 尚未設定")
        url = self.settings.company_llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.company_llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.company_llm_model,
            "messages": messages,
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=self.settings.company_llm_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        return str(body["choices"][0]["message"]["content"])

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("LLM structured response must be a JSON object")
        return parsed
