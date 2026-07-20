from __future__ import annotations

from fastmcp.server.auth import StaticTokenVerifier

from app.config import Settings


def build_mcp_auth(settings: Settings) -> StaticTokenVerifier | None:
    """Build optional Bearer-token authentication for private MCP deployments."""

    if settings.mcp_auth_mode == "none":
        return None
    token = settings.mcp_shared_token.strip()
    if not token or token in {"change-me", "replace-me"}:
        raise RuntimeError("MCP_AUTH_MODE=static requires a non-default MCP_SHARED_TOKEN")
    return StaticTokenVerifier(
        tokens={
            token: {
                "client_id": "verified-rag-client",
                "scopes": ["mcp:tools"],
            }
        },
        required_scopes=["mcp:tools"],
    )
