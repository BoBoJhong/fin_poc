import httpx
import pytest

from app.external_api_connectors import ExternalAPIConfig, ExternalAPIFinanceRepository


def api_config(*, approved: bool = True) -> ExternalAPIConfig:
    return ExternalAPIConfig.model_validate(
        {
            "id": "vendor_api",
            "approved": approved,
            "base_url": "https://vendor.test/v1",
            "companies": {"path": "/companies", "items_path": "data.items"},
            "company_mapping": {
                "company_code": "ticker",
                "company_name": "name",
                "aliases": "aliases",
                "fiscal_year_end_month": "fiscal.month",
                "timezone": "fiscal.timezone",
            },
            "metrics": {
                "path": "/metrics",
                "items_path": "data.items",
                "query_params": {"ticker": "co_code", "quarter": "period"},
            },
            "metric_mapping": {
                "company_code": "ticker",
                "period": "quarter",
                "metric": "metric",
                "value": "value",
                "unit": "unit",
                "source_id": "source.id",
                "source_url": "source.url",
                "data_version": "revision",
                "updated_at": "updated_at",
            },
        }
    )


def dynamic_api_config() -> ExternalAPIConfig:
    payload = api_config().model_dump()
    payload["metric_mapping"] = None
    payload["dynamic_metric_mapping"] = {
        "company_code": "ticker",
        "period": "quarter",
        "metrics_path": "statements",
        "fiscal_year": "fiscal.year",
        "fiscal_quarter": "fiscal.quarter",
        "consolidation_scope": "scope",
        "source_id": "source.id",
        "source_url": "source.url",
        "data_version": "revision",
        "updated_at": "updated_at",
    }
    payload["metric_definitions"] = [
        {
            "metric_code": "revenue",
            "display_name": "營業收入",
            "statement_type": "income_statement",
            "data_type": "monetary",
            "default_unit": "TWD",
            "duration_type": "quarter",
            "aliases": ["營收"],
            "approved": True,
        }
    ]
    payload["provider_metric_mappings"] = [
        {
            "provider_id": "vendor_api",
            "provider_metric_key": "income_statement.營業收入合計",
            "metric_code": "revenue",
            "approved": True,
        }
    ]
    return ExternalAPIConfig.model_validate(payload)


@pytest.mark.asyncio
async def test_external_api_maps_json_and_preserves_provenance() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/companies":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [
                            {
                                "ticker": "ACME",
                                "name": "Acme Holdings",
                                "aliases": ["Acme"],
                                "fiscal": {"month": 6, "timezone": "Asia/Taipei"},
                            }
                        ]
                    }
                },
            )
        assert request.url.params["ticker"] == "ACME"
        assert request.url.params["quarter"] == "2026Q2"
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "items": [
                        {
                            "ticker": "ACME",
                            "quarter": "2026Q2",
                            "metric": "revenue",
                            "value": 321.5,
                            "unit": "USD_M",
                            "source": {
                                "id": "filing-77",
                                "url": "https://vendor.test/filing-77",
                            },
                            "revision": "rev-2",
                            "updated_at": "2026-07-20T00:00:00Z",
                        },
                        {
                            "ticker": "OTHER",
                            "quarter": "2026Q2",
                            "metric": "revenue",
                            "value": 999,
                            "unit": "USD_M",
                        },
                    ]
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = ExternalAPIFinanceRepository(api_config(), client=client)

    companies = await repository.list_companies()
    evidence = await repository.get_metrics("ACME", "2026Q2")
    preview = await repository.get_source_preview("filing-77", "ACME")
    calendar = await repository.get_fiscal_calendar("ACME")

    assert companies[0].aliases == ["Acme"]
    assert len(evidence) == 1
    assert evidence[0].metadata["metric_code"] == "revenue"
    assert evidence[0].content_hash.startswith("sha256:")
    assert preview is not None
    assert preview.live_url == "https://vendor.test/filing-77"
    assert preview.database_record["provider_id"] == "vendor_api"
    assert calendar is not None
    assert calendar.fiscal_year_end_month == 6
    await client.aclose()


def test_external_api_requires_explicit_approval() -> None:
    with pytest.raises(RuntimeError, match="has not been approved"):
        ExternalAPIFinanceRepository(api_config(approved=False))


def test_external_api_rejects_embedded_credentials_and_absolute_endpoint() -> None:
    payload = api_config().model_dump()
    payload["base_url"] = "https://user:password@vendor.test"
    with pytest.raises(ValueError, match="credentials"):
        ExternalAPIConfig.model_validate(payload)

    payload = api_config().model_dump()
    payload["metrics"]["path"] = "https://evil.test/metrics"
    with pytest.raises(ValueError, match="absolute URL path"):
        ExternalAPIConfig.model_validate(payload)


@pytest.mark.asyncio
async def test_external_api_normalizes_dynamic_metric_key_objects() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "items": [
                        {
                            "ticker": "ACME",
                            "quarter": "2026Q2",
                            "fiscal": {"year": 2026, "quarter": 2},
                            "scope": "consolidated",
                            "statements": {
                                "income_statement": {
                                    "營業收入合計": {"value": "321500000", "unit": "TWD"},
                                    "供應商新指標": {"value": "77", "unit": "TWD"},
                                }
                            },
                            "source": {
                                "id": "filing-dynamic-77",
                                "url": "https://vendor.test/filing-dynamic-77",
                            },
                            "revision": "rev-3",
                            "updated_at": "2026-07-20T00:00:00Z",
                        }
                    ]
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = ExternalAPIFinanceRepository(dynamic_api_config(), client=client)
    evidence = await repository.get_metrics("ACME", "2026Q2")
    preview = await repository.get_source_preview("filing-dynamic-77", "ACME")

    assert len(evidence) == 1
    assert evidence[0].metadata["metric_code"] == "revenue"
    assert evidence[0].metadata["value_exact"] == "321500000"
    assert evidence[0].metadata["unmapped_metric_keys"] == [
        "income_statement.供應商新指標"
    ]
    assert preview is not None
    assert preview.database_record["raw_payload_id"].startswith("raw:vendor_api:ACME")
    await client.aclose()
