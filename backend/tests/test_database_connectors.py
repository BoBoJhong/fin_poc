import json
import sqlite3

import pytest

from app.database_connectors import (
    CompositeFinanceRepository,
    ExternalDatabaseConfig,
    ExternalSQLFinanceRepository,
    build_registry_draft,
    discover_database,
    load_external_database_registry,
)
from app.validation import EvidenceValidationError, EvidenceValidator


def external_config(*, approved: bool = True) -> ExternalDatabaseConfig:
    return ExternalDatabaseConfig.model_validate(
        {
            "id": "vendor_db",
            "url_env": "TEST_VENDOR_DATABASE_URL",
            "datasets": [
                {
                    "id": "facts",
                    "table": "vendor_facts",
                    "approved": approved,
                    "mapping": {
                        "company_code": "ticker_symbol",
                        "company_name": "issuer",
                        "period": "fiscal_quarter",
                        "metric": "measure_name",
                        "value": "measure_value",
                        "unit": "measure_unit",
                        "source_id": "document_key",
                        "source_url": "document_url",
                        "data_version": "revision_id",
                        "updated_at": "loaded_at",
                        "primary_key": ["ticker_symbol", "fiscal_quarter", "measure_name"],
                    },
                }
            ],
        }
    )


@pytest.fixture
def vendor_database(tmp_path, monkeypatch):
    path = tmp_path / "vendor.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE vendor_facts (
                ticker_symbol TEXT NOT NULL,
                issuer TEXT NOT NULL,
                fiscal_quarter TEXT NOT NULL,
                measure_name TEXT NOT NULL,
                measure_value REAL NOT NULL,
                measure_unit TEXT NOT NULL,
                document_key TEXT NOT NULL,
                document_url TEXT,
                revision_id TEXT NOT NULL,
                loaded_at TEXT NOT NULL,
                PRIMARY KEY (ticker_symbol, fiscal_quarter, measure_name)
            )
            """
        )
        connection.executemany(
            "INSERT INTO vendor_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "ACME",
                    "Acme Holdings",
                    "2026Q2",
                    "revenue",
                    321.5,
                    "USD_M",
                    "filing-77",
                    "https://example.test/filing-77",
                    "rev-2",
                    "2026-07-20T00:00:00Z",
                ),
                (
                    "ACME",
                    "Acme Holdings",
                    "2026Q2",
                    "net_income",
                    42.0,
                    "USD_M",
                    "filing-77",
                    "https://example.test/filing-77",
                    "rev-2",
                    "2026-07-20T00:00:00Z",
                ),
                (
                    "OTHER",
                    "Other Corp",
                    "2026Q2",
                    "revenue",
                    11.0,
                    "USD_M",
                    "filing-88",
                    "https://example.test/filing-88",
                    "rev-1",
                    "2026-07-20T00:00:00Z",
                ),
            ],
        )
        connection.commit()
    url = f"sqlite+pysqlite:///{path}"
    monkeypatch.setenv("TEST_VENDOR_DATABASE_URL", url)
    return path, url


@pytest.mark.asyncio
async def test_external_database_maps_unknown_schema_to_evidence(vendor_database) -> None:
    repository = ExternalSQLFinanceRepository(external_config())

    companies = await repository.list_companies()
    evidence = await repository.get_metrics("ACME", "2026Q2")
    preview = await repository.get_source_preview(evidence[0].source_id, "ACME")

    assert {item.co_code for item in companies} == {"ACME", "OTHER"}
    assert len(evidence) == 2
    assert len({item.evidence_id for item in evidence}) == 2
    revenue = next(item for item in evidence if item.metadata["metric_code"] == "revenue")
    assert revenue.metadata["value"] == 321.5
    assert evidence[0].locator.table == "vendor_facts"
    assert evidence[0].content_hash.startswith("sha256:")
    assert preview is not None
    assert preview.live_url == "https://example.test/filing-77"
    assert preview.database_record["data_version"] == "rev-2"
    assert len(preview.database_record["records"]) == 2


def test_unapproved_mapping_cannot_be_mounted(vendor_database) -> None:
    with pytest.raises(RuntimeError, match="no approved dataset mapping"):
        ExternalSQLFinanceRepository(external_config(approved=False))


def test_discovery_suggests_mapping_without_credentials(vendor_database) -> None:
    _, url = vendor_database
    report = discover_database(url)
    table = next(item for item in report["tables"] if item["table"] == "vendor_facts")

    assert report["credentials_included"] is False
    assert table["mapping_suggestion"]["source_url"] == "document_url"
    assert table["required_mapping_score"] == "4/4"
    assert all("password" not in json.dumps(item).lower() for item in report["tables"])

    draft = build_registry_draft(report, "vendor_db", "TEST_VENDOR_DATABASE_URL")
    dataset = draft["databases"][0]["datasets"][0]
    assert dataset["approved"] is False
    assert dataset["mapping"]["company_code"] == "ticker_symbol"


def test_registry_file_is_optional_and_validated(tmp_path) -> None:
    missing = load_external_database_registry(tmp_path / "missing.json")
    assert missing.databases == []

    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"version": 1, "databases": [external_config().model_dump()]}),
        encoding="utf-8",
    )
    loaded = load_external_database_registry(path)
    assert loaded.databases[0].id == "vendor_db"


@pytest.mark.asyncio
async def test_composite_repository_survives_one_unavailable_source(vendor_database) -> None:
    healthy = ExternalSQLFinanceRepository(external_config())

    class UnavailableRepository:
        async def list_companies(self):
            raise ConnectionError("offline")

        async def get_metrics(self, co_code, period=None):
            raise ConnectionError("offline")

        async def get_source_preview(self, source_id, co_code):
            raise ConnectionError("offline")

    repository = CompositeFinanceRepository([UnavailableRepository(), healthy], strict=False)
    evidence = await repository.get_metrics("ACME", "2026Q2")
    assert len(evidence) == 2


@pytest.mark.asyncio
async def test_conflicting_values_from_multiple_databases_are_rejected(vendor_database) -> None:
    repository = ExternalSQLFinanceRepository(external_config())
    evidence = await repository.get_metrics("ACME", "2026Q2")
    revenue = next(item for item in evidence if item.metadata["metric_code"] == "revenue")
    conflict = revenue.model_copy(deep=True)
    conflict.evidence_id = f"{revenue.evidence_id}-conflict"
    conflict.source_id = f"{revenue.source_id}-conflict"
    conflict.metadata["value"] = 999.0
    conflict.content = "2026Q2 revenue = 999.0 USD_M (external_database)"

    validator = EvidenceValidator(allowed_co_codes=set())
    with pytest.raises(EvidenceValidationError, match="相同財務指標出現衝突值"):
        validator.validate_evidence("ACME", [revenue, conflict], "2026Q2")
