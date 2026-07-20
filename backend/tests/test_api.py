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
            readiness = client.get("/health/readiness")
            assert readiness.status_code == 200
            assert readiness.json()["schema_version"] == "1.1"
            assert readiness.json()["evidence_tools_ready"] is True
            companies = client.get("/api/v1/companies")
            assert companies.status_code == 200
            assert companies.json()[0]["co_code"] == "DEMO01"

            response = client.post(
                "/api/v1/chat",
                json={
                    "query": "範例科技 2026 Q2 的營收和毛利率是多少？",
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["verification"]["passed"] is True
            assert body["citations"]
            assert body["citations"][0]["co_code"] == "DEMO01"
            assert body["citations"][0]["quoted_text"]

            source = client.get(
                "/api/v1/sources/demo01-financial-metrics-2026q2",
                params={"co_code": "DEMO01"},
            )
            assert source.status_code == 200
            assert source.json()["database_record"]["data_version"] == "demo-v1"
    finally:
        app.dependency_overrides.clear()


def test_api_rejects_legacy_header_body_scope_mismatch() -> None:
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


def test_question_company_overrides_ui_default_and_source_is_recheckable() -> None:
    app.dependency_overrides[get_agent_service] = mock_service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/chat",
                json={
                    "query": "示範製造 2026 Q2 的營收是多少？",
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["co_code"] == "DEMO02"
            assert body["citations"][0]["co_code"] == "DEMO02"
            assert body["citations"][0]["source_id"].startswith("demo02-")
            assert "76.2" in body["citations"][0]["quoted_text"]

            source = client.get(
                "/api/v1/sources/demo02-financial-metrics-2026q2",
                params={"co_code": body["co_code"]},
            )
            assert source.status_code == 200
            assert source.json()["co_code"] == "DEMO02"
    finally:
        app.dependency_overrides.clear()


def test_chat_requires_company_in_question_without_legacy_default() -> None:
    app.dependency_overrides[get_agent_service] = mock_service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/chat",
                json={"query": "2026 Q2 的營收是多少？"},
            )
            assert response.status_code == 422
            assert "輸入公司" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
