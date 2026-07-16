from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.agents import FinancialAgentService
from app.config import Settings
from app.llm import CompanyLLMClient
from app.mcp_gateway import MCPGateway
from app.validation import EvidenceValidator


async def main() -> None:
    settings = Settings(
        data_mode="mock",
        mcp_enabled=False,
        company_llm_mode="mock",
        allowed_co_codes="DEMO01,DEMO02",
    )
    service = FinancialAgentService(
        gateway=MCPGateway(settings),
        llm=CompanyLLMClient(settings),
        validator=EvidenceValidator(settings.allowed_co_code_set),
    )
    golden_path = Path(__file__).resolve().parents[2] / "eval" / "golden_set.json"
    cases = json.loads(golden_path.read_text(encoding="utf-8"))
    results = []
    recall_total = 0.0
    route_matches = 0
    verified = 0

    for case in cases:
        answer = await service.answer(case["query"], case["co_code"])
        actual_sources = {citation.source_id for citation in answer.citations}
        expected_sources = set(case["expected_source_ids"])
        recall = len(actual_sources & expected_sources) / max(len(expected_sources), 1)
        route_match = set(answer.routes) == set(case["expected_routes"])
        is_verified = bool(answer.verification.get("passed"))
        recall_total += recall
        route_matches += int(route_match)
        verified += int(is_verified)
        results.append(
            {
                "id": case["id"],
                "retrieval_recall": round(recall, 3),
                "route_match": route_match,
                "verified": is_verified,
                "actual_sources": sorted(actual_sources),
            }
        )

    count = len(cases)
    summary = {
        "cases": count,
        "retrieval_recall_at_5": round(recall_total / count, 3),
        "route_accuracy": round(route_matches / count, 3),
        "verified_answer_rate": round(verified / count, 3),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

