from app.mcp_gateway import MCPGateway


def test_coerce_langchain_text_block_result() -> None:
    result = MCPGateway._coerce_mapping(
        [{"type": "text", "text": '{"evidence": [], "metadata": {"ok": true}}'}]
    )
    assert result["metadata"]["ok"] is True

