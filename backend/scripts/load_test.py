from __future__ import annotations

import argparse
import asyncio
import json
import time
from statistics import median

import httpx


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = min(int((len(ordered) - 1) * ratio), len(ordered) - 1)
    return round(ordered[index], 2)


async def run(url: str, query: str, concurrency: int, requests: int) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    verified = 0
    timeout = httpx.Timeout(120)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        async def one() -> None:
            nonlocal verified
            async with semaphore:
                started = time.perf_counter()
                response = await client.post(url, json={"query": query})
                latencies.append((time.perf_counter() - started) * 1000)
                statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
                if response.status_code == 200 and response.json().get("verified") is True:
                    verified += 1

        started = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(requests)))
        elapsed = time.perf_counter() - started

    successful = statuses.get(200, 0)
    return {
        "url": url,
        "concurrency": concurrency,
        "requests": requests,
        "status_counts": statuses,
        "verified_responses": verified,
        "success_rate": round(successful / requests, 4),
        "verified_rate": round(verified / requests, 4),
        "throughput_rps": round(requests / elapsed, 2),
        "latency_ms": {
            "median": round(median(latencies), 2),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 2),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Concurrent HTTP load test for verified RAG.")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/v1/chat")
    parser.add_argument("--query", default="Microsoft 2026 Q1 revenue and gross margin?")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=100)
    args = parser.parse_args()
    if args.concurrency < 1 or args.requests < args.concurrency:
        parser.error("requests must be >= concurrency and both must be positive")
    result = asyncio.run(run(args.url, args.query, args.concurrency, args.requests))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
