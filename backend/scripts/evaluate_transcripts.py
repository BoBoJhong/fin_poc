from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from statistics import median

from app.agents import FinancialAgentService
from app.config import Settings
from app.llm import CompanyLLMClient
from app.mcp_gateway import MCPGateway
from app.validation import EvidenceValidator


async def main() -> None:
    settings = Settings(
        data_mode="local",
        mcp_enabled=False,
        company_llm_mode="mock",
        allowed_co_codes="*",
    )
    gateway = MCPGateway(settings)
    service = FinancialAgentService(
        gateway=gateway,
        llm=CompanyLLMClient(settings),
        validator=EvidenceValidator.from_settings(settings),
        max_evidence_items=settings.max_evidence_items,
        retrieval_profile="transcript",
    )
    golden_path = Path(__file__).resolve().parents[2] / "eval" / "transcript_golden_set.json"
    cases = json.loads(golden_path.read_text(encoding="utf-8"))
    results = []
    latencies = []
    try:
        for case in cases:
            started = time.perf_counter()
            evidence = await gateway.search_documents(
                case["query"], case["co_code"], top_k=5, period=case["period"]
            )
            answer = await service.answer(case["query"])
            latencies.append((time.perf_counter() - started) * 1000)

            expected_terms = [term.casefold() for term in case["expected_terms"]]
            matching_evidence = [
                item
                for item in evidence
                if item.source_id == case["source_id"]
                and item.metadata.get("speaker") == case["speaker"]
                and item.metadata.get("section") == case["section"]
                and all(term in item.content.casefold() for term in expected_terms)
            ]
            transcript_citations = [
                item
                for item in answer.citations
                if item.source_id == case["source_id"] and item.source_type == "transcript"
            ]
            cited_text = " ".join(item.quoted_text for item in transcript_citations).casefold()
            answer_terms_supported = all(term in cited_text for term in expected_terms)
            preview = await gateway.get_source_preview(case["source_id"], case["co_code"])
            provenance_traceable = bool(
                preview
                and preview.live_url
                and preview.live_url.startswith("https://www.microsoft.com/")
                and preview.content_hash
                and preview.text
            )
            period_isolated = bool(evidence) and all(
                item.period == case["period"] for item in evidence
            )
            passed = all(
                (
                    bool(matching_evidence),
                    answer.co_code == case["co_code"],
                    answer.verification.get("passed") is True,
                    bool(transcript_citations),
                    answer_terms_supported,
                    provenance_traceable,
                    period_isolated,
                )
            )
            results.append(
                {
                    "id": case["id"],
                    "passed": passed,
                    "retrieval_match": bool(matching_evidence),
                    "verified": answer.verification.get("passed") is True,
                    "transcript_cited": bool(transcript_citations),
                    "answer_terms_supported": answer_terms_supported,
                    "provenance_traceable": provenance_traceable,
                    "period_isolated": period_isolated,
                    "citation_count": len(answer.citations),
                }
            )

        wrong_scope = await gateway.get_source_preview("ir-msft-fy2026-q3-transcript", "AAPL")
        missing = await service.answer("Microsoft 2035 Q4 earnings call outlook?")
        refusal_passed = (
            not missing.citations
            and missing.verification.get("passed") is False
            and "找不到" in missing.answer
        )
    finally:
        close = getattr(gateway.knowledge, "close", None)
        if close:
            await close()

    print(
        json.dumps(
            {
                "dataset": "Microsoft official Investor Relations earnings transcripts",
                "cases": len(results),
                "case_pass_rate": sum(item["passed"] for item in results) / len(results),
                "cross_company_isolation": wrong_scope is None,
                "missing_period_refusal": refusal_passed,
                "median_latency_ms": round(median(latencies), 2),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
