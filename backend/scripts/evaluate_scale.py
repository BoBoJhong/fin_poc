from __future__ import annotations

import argparse
import asyncio
import json
import time
from statistics import median

from app.agents import FinancialAgentService
from app.config import Settings
from app.llm import CompanyLLMClient
from app.mcp_gateway import MCPGateway
from app.validation import EvidenceValidator
from scripts.seed_scale import company_row


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    index = min(round((len(ordered) - 1) * percentage), len(ordered) - 1)
    return round(ordered[index], 2)


async def evaluate(count: int, end_to_end_count: int) -> dict:
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
    )
    rows = [company_row(index) for index in range(1, count + 1)]
    retrieval_passes = 0
    finance_passes = 0
    graph_passes = 0
    isolation_passes = 0
    end_to_end_passes = 0
    refusal_passes = 0
    retrieval_ms: list[float] = []
    end_to_end_ms: list[float] = []

    try:
        for index, row in enumerate(rows):
            started = time.perf_counter()
            documents = await gateway.search_documents(
                f"{row['company_name']} {row['risk']} 主要風險", row["co_code"], 3
            )
            retrieval_ms.append((time.perf_counter() - started) * 1000)
            expected_document = f"scale-{row['co_code'].lower()}-2026q2-call"
            retrieval_passes += int(
                bool(documents)
                and documents[0].source_id == expected_document
                and all(item.co_code == row["co_code"] for item in documents)
            )

            metrics = await gateway.get_metrics(row["co_code"], "2026Q2")
            actual_metrics = {
                item.metadata["metric_code"]: item.metadata["value"] for item in metrics
            }
            finance_passes += int(
                actual_metrics
                == {
                    "revenue": row["revenue"],
                    "gross_margin": row["gross_margin"],
                }
            )

            wrong_row = rows[(index + 1) % len(rows)]
            preview = await gateway.get_source_preview(
                expected_document, wrong_row["co_code"]
            )
            isolation_passes += int(preview is None)

        graph_rows = rows[::5]
        for row in graph_rows:
            graph = await gateway.search_graph(
                f"{row['product']} 與 {row['risk']} 的關聯", row["co_code"], 2
            )
            graph_passes += int(
                bool(graph)
                and all(item.co_code == row["co_code"] for item in graph)
                and any(row["risk"] in item.content for item in graph)
            )

        selected = rows[: min(end_to_end_count, len(rows))]
        for row in selected:
            started = time.perf_counter()
            answer = await service.answer(
                f"{row['short_name']} 2026 Q2 的營收、毛利率與主要風險？"
            )
            end_to_end_ms.append((time.perf_counter() - started) * 1000)
            sources = {citation.source_id for citation in answer.citations}
            end_to_end_passes += int(
                answer.co_code == row["co_code"]
                and answer.verification.get("passed") is True
                and f"scale-{row['co_code'].lower()}-metrics-2026q2" in sources
                and f"scale-{row['co_code'].lower()}-2026q2-call" in sources
                and all(citation.co_code == row["co_code"] for citation in answer.citations)
            )

            refusal = await service.answer(
                f"{row['short_name']} 2035 Q4 的營收是多少？"
            )
            refusal_passes += int(
                not refusal.citations
                and refusal.verification.get("passed") is False
                and "找不到" in refusal.answer
            )
    finally:
        close = getattr(gateway.knowledge, "close", None)
        if close:
            await close()

    graph_count = len(rows[::5])
    e2e_count = min(end_to_end_count, len(rows))
    rates = {
        "document_retrieval_accuracy": retrieval_passes / count,
        "finance_exact_match": finance_passes / count,
        "cross_company_isolation": isolation_passes / count,
        "graph_retrieval_accuracy": graph_passes / graph_count,
        "end_to_end_pass_rate": end_to_end_passes / e2e_count,
        "missing_period_refusal_rate": refusal_passes / e2e_count,
    }
    return {
        "data_mode": "local",
        "embedding_model": settings.ollama_embedding_model,
        "companies_evaluated": count,
        "end_to_end_cases": e2e_count,
        "graph_cases": graph_count,
        "rates": {key: round(value, 4) for key, value in rates.items()},
        "latency_ms": {
            "retrieval_median": round(median(retrieval_ms), 2),
            "retrieval_p95": percentile(retrieval_ms, 0.95),
            "end_to_end_median": round(median(end_to_end_ms), 2),
            "end_to_end_p95": percentile(end_to_end_ms, 0.95),
        },
        "feasibility_gate_passed": all(value >= 0.95 for value in rates.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate synthetic local scale data.")
    parser.add_argument("--companies", type=int, default=60)
    parser.add_argument("--end-to-end", type=int, default=20)
    args = parser.parse_args()
    if not 2 <= args.companies <= 500:
        parser.error("--companies must be between 2 and 500")
    if args.end_to_end < 1:
        parser.error("--end-to-end must be positive")
    result = asyncio.run(evaluate(args.companies, args.end_to_end))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
