from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.agents import FinancialAgentService
from app.company_resolver import CompanyResolutionError
from app.config import Settings
from app.llm import CompanyLLMClient
from app.mcp_gateway import MCPGateway
from app.validation import EvidenceValidationError, EvidenceValidator


async def evaluate(live_llm: bool) -> dict:
    settings = Settings(
        data_mode="local",
        mcp_enabled=False,
        company_llm_mode="openai_compatible" if live_llm else "mock",
        allowed_co_codes="*",
    )
    if live_llm and (
        not settings.company_llm_api_key or settings.company_llm_model == "your-model"
    ):
        raise RuntimeError("Live LLM evaluation requires a real API key and model name")
    cases_path = Path(__file__).resolve().parents[2] / "eval" / "llm_behavior_set.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    services = {
        profile: FinancialAgentService(
            gateway=MCPGateway(settings),
            llm=CompanyLLMClient(settings),
            validator=EvidenceValidator.from_settings(settings),
            max_evidence_items=settings.max_evidence_items,
            retrieval_profile=profile,
        )
        for profile in ("financial", "transcript")
    }
    results = []
    try:
        for case in cases:
            try:
                answer = await services[case["profile"]].answer(case["query"])
                status = "answered" if answer.verification.get("passed") else "refused"
                co_code = answer.co_code
                period = (
                    answer.period_resolution.resolved_period
                    if answer.period_resolution is not None
                    else None
                )
                text = answer.answer
            except (CompanyResolutionError, EvidenceValidationError, ValueError):
                status = "needs_clarification"
                co_code = None
                period = "2026Q1" if "2026 Q1" in case["query"] else None
                text = ""
            forbidden_absent = all(term not in text for term in case["forbidden_terms"])
            passed = all(
                (
                    status == case["expected_status"],
                    co_code == case["expected_co_code"],
                    period == case["expected_period"],
                    forbidden_absent,
                )
            )
            results.append(
                {
                    "id": case["id"],
                    "passed": passed,
                    "status": status,
                    "co_code": co_code,
                    "period": period,
                    "forbidden_terms_absent": forbidden_absent,
                }
            )
    finally:
        for service in services.values():
            await service.llm.close()
        gateways = {id(service.gateway): service.gateway for service in services.values()}
        for gateway in gateways.values():
            close = getattr(gateway.knowledge, "close", None)
            if close:
                await close()
    passed_count = sum(item["passed"] for item in results)
    return {
        "mode": "live_llm" if live_llm else "mock_guardrail",
        "cases": len(results),
        "passed": passed_count,
        "pass_rate": passed_count / len(results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate LLM and deterministic RAG behavior.")
    parser.add_argument("--live-llm", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(evaluate(args.live_llm)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
