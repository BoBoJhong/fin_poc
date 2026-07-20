import pytest

from app.config import Settings
from app.mcp_auth import build_mcp_auth


def test_mcp_auth_is_opt_in_for_local_development() -> None:
    assert build_mcp_auth(Settings(mcp_auth_mode="none")) is None


def test_static_mcp_auth_rejects_default_secret() -> None:
    with pytest.raises(RuntimeError, match="non-default"):
        build_mcp_auth(Settings(mcp_auth_mode="static", mcp_shared_token="change-me"))


@pytest.mark.asyncio
async def test_static_mcp_auth_accepts_only_configured_token() -> None:
    verifier = build_mcp_auth(
        Settings(mcp_auth_mode="static", mcp_shared_token="a-real-random-secret")
    )
    assert verifier is not None
    assert await verifier.verify_token("wrong") is None
    token = await verifier.verify_token("a-real-random-secret")
    assert token is not None
    assert token.client_id == "verified-rag-client"
