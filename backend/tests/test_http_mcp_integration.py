import asyncio

import pytest
import uvicorn

from app.config import Settings
from app.mcp_gateway import MCPGateway
from mcp_servers.finance import mcp as finance_mcp
from mcp_servers.knowledge import mcp as knowledge_mcp


async def wait_started(*servers: uvicorn.Server) -> None:
    for _ in range(300):
        if all(server.started for server in servers):
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("MCP test servers did not start")


@pytest.mark.asyncio
async def test_langchain_gateway_over_streamable_http(monkeypatch) -> None:
    for name in (
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ):
        monkeypatch.delenv(name, raising=False)

    knowledge_server = uvicorn.Server(
        uvicorn.Config(
            knowledge_mcp.http_app(),
            host="127.0.0.1",
            port=18801,
            log_level="error",
        )
    )
    finance_server = uvicorn.Server(
        uvicorn.Config(
            finance_mcp.http_app(),
            host="127.0.0.1",
            port=18802,
            log_level="error",
        )
    )
    knowledge_server.install_signal_handlers = lambda: None
    finance_server.install_signal_handlers = lambda: None
    tasks = [
        asyncio.create_task(knowledge_server.serve()),
        asyncio.create_task(finance_server.serve()),
    ]

    try:
        await wait_started(knowledge_server, finance_server)
        settings = Settings(
            data_mode="mock",
            mcp_enabled=True,
            knowledge_mcp_url="http://127.0.0.1:18801/mcp",
            finance_mcp_url="http://127.0.0.1:18802/mcp",
        )
        gateway = MCPGateway(settings)
        documents = await gateway.search_documents("法說風險", "DEMO01")
        metrics = await gateway.get_metrics("DEMO01", "2026Q2")
        preview = await gateway.get_source_preview(
            "demo01-financial-metrics-2026q2", "DEMO01"
        )
        assert documents[0].co_code == "DEMO01"
        assert len(metrics) == 2
        assert preview is not None
        assert preview.database_record is not None
    finally:
        knowledge_server.should_exit = True
        finance_server.should_exit = True
        await asyncio.gather(*tasks)

