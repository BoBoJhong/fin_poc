from fastapi.testclient import TestClient

from app.agents import FinancialAgentService
from app.config import Settings
from app.llm import CompanyLLMClient
from app.main import app, get_agent_service
from app.mcp_gateway import MCPGateway
from app.validation import EvidenceValidator


def mock_service() -> FinancialAgentService:
    settings = Settings(
        data_mode="mock",
        mcp_enabled=False,
        company_llm_mode="mock",
        allowed_co_codes="DEMO01,DEMO02",
    )
    return FinancialAgentService(
        gateway=MCPGateway(settings),
        llm=CompanyLLMClient(settings),
        validator=EvidenceValidator(settings.allowed_co_code_set),
    )


def test_chat_and_source_preview_api() -> None:
    app.dependency_overrides[get_agent_service] = mock_service
    try:
        with TestClient(app) as client:
            companies = client.get("/api/v1/companies")
            assert companies.status_code == 200
            assert companies.json()[0]["co_code"] == "DEMO01"

            headers = {"X-User-Id": "test-user", "X-Co-Code": "DEMO01"}
            response = client.post(
                "/api/v1/chat",
                headers=headers,
                json={
                    "query": "2026 Q2 的營收和毛利率是多少？",
                    "co_code": "DEMO01",
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["verification"]["passed"] is True
            assert body["citations"]

            source = client.get(
                "/api/v1/sources/demo01-financial-metrics-2026q2",
                headers=headers,
                params={"co_code": "DEMO01"},
            )
            assert source.status_code == 200
            assert source.json()["database_record"]["data_version"] == "demo-v1"
    finally:
        app.dependency_overrides.clear()


def test_api_rejects_header_body_scope_mismatch() -> None:
    app.dependency_overrides[get_agent_service] = mock_service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/chat",
                headers={"X-Co-Code": "DEMO01"},
                json={"query": "營收？", "co_code": "DEMO02"},
            )
            assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
