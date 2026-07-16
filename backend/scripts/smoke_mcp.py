from __future__ import annotations

import asyncio
import json

from app.config import Settings
from app.mcp_gateway import MCPGateway


async def main() -> None:
    settings = Settings(
        data_mode="mock",
        mcp_enabled=True,
        knowledge_mcp_url="http://127.0.0.1:8001/mcp",
        finance_mcp_url="http://127.0.0.1:8002/mcp",
    )
    gateway = MCPGateway(settings)
    documents, graph, metrics = await asyncio.gather(
        gateway.search_documents("法說風險", "DEMO01"),
        gateway.search_graph("產品風險", "DEMO01"),
        gateway.get_metrics("DEMO01", "2026Q2"),
    )
    preview = await gateway.get_source_preview(
        "demo01-financial-metrics-2026q2", "DEMO01"
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "documents": len(documents),
                "graph": len(graph),
                "metrics": len(metrics),
                "preview": preview.source_id if preview else None,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

