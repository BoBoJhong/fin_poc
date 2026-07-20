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
        retrieval_profile="financial",
    )
    golden_path = Path(__file__).resolve().parents[2] / "eval" / "sec_golden_set.json"
    cases = json.loads(golden_path.read_text(encoding="utf-8"))
    results: list[dict] = []
    latencies: list[float] = []
    try:
        for case in cases:
            started = time.perf_counter()
            metrics = await gateway.get_metrics(case["co_code"], "2026Q1")
            metric_values = {
                item.metadata["metric_code"]: item.metadata["value"] for item in metrics
            }
            answer = await service.answer(case["query"])
            latencies.append((time.perf_counter() - started) * 1000)
            source_ids = {citation.source_id for citation in answer.citations}
            metric_source_match = case["expected_metric_source"] in source_ids
            document_source_match = any(
                source_id.startswith(case["expected_document_source_prefix"])
                for source_id in source_ids
            )
            correct_numbers = (
                metric_values.get("revenue") == case["expected_revenue"]
                and abs(metric_values.get("gross_margin", 0) - case["expected_gross_margin"])
                < 0.000001
            )
            citations_scoped = bool(answer.citations) and all(
                citation.co_code == case["co_code"] for citation in answer.citations
            )
            answer_number_match = (
                str(case["expected_revenue"]) in answer.answer
                and str(case["expected_gross_margin"]) in answer.answer
            )
            document_source_id = next(
                (
                    source_id
                    for source_id in source_ids
                    if source_id.startswith(case["expected_document_source_prefix"])
                ),
                None,
            )
            preview = (
                await gateway.get_source_preview(document_source_id, case["co_code"])
                if document_source_id
                else None
            )
            provenance_traceable = bool(
                preview
                and preview.content_hash
                and preview.live_url
                and preview.live_url.startswith("https://www.sec.gov/Archives/")
                and preview.text
            )
            passed = all(
                (
                    answer.co_code == case["co_code"],
                    answer.verification.get("passed") is True,
                    correct_numbers,
                    answer_number_match,
                    metric_source_match,
                    document_source_match,
                    citations_scoped,
                    provenance_traceable,
                )
            )
            results.append(
                {
                    "id": case["id"],
                    "passed": passed,
                    "company_match": answer.co_code == case["co_code"],
                    "verified": answer.verification.get("passed") is True,
                    "number_exact_match": correct_numbers,
                    "answer_number_match": answer_number_match,
                    "metric_source_match": metric_source_match,
                    "document_source_match": document_source_match,
                    "citations_scoped": citations_scoped,
                    "provenance_traceable": provenance_traceable,
                    "citation_count": len(answer.citations),
                    "lowest_evidence_score": answer.verification.get("reliability_policy", {}).get(
                        "lowest_evidence_score"
                    ),
                }
            )

        isolation_checks = []
        for ticker, wrong_ticker, prefix in (
            ("AAPL", "MSFT", "sec-aapl-000032019326000013"),
            ("MSFT", "NVDA", "sec-msft-000119312526191507"),
            ("NVDA", "AAPL", "sec-nvda-000104581026000052"),
        ):
            preview = await gateway.get_source_preview(f"{prefix}-10q", wrong_ticker)
            isolation_checks.append(preview is None)

        refusal = await service.answer("Apple 2035 Q4 revenue?")
        refusal_passed = (
            not refusal.citations
            and refusal.verification.get("passed") is False
            and "找不到" in refusal.answer
        )
    finally:
        close = getattr(gateway.knowledge, "close", None)
        if close:
            await close()

    passed_count = sum(result["passed"] for result in results)
    print(
        json.dumps(
            {
                "dataset": "official SEC Company Facts and Form 10-Q",
                "cases": len(results),
                "case_pass_rate": passed_count / len(results),
                "cross_company_isolation_rate": sum(isolation_checks) / len(isolation_checks),
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
