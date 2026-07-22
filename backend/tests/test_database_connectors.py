import json
import sqlite3

import pytest

from app.database_connectors import (
    CompositeFinanceRepository,
    ExternalDatabaseConfig,
    ExternalSQLNarrativeReader,
    ExternalSQLFinanceRepository,
    build_registry_draft,
    discover_database,
    load_external_database_registry,
    normalize_mapped_period,
    resolve_environment_value,
)
from app.config import Settings
from app.repositories import build_finance_repository
from scripts.sync_internal_database import build_catalog, build_documents
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
            "company_datasets": [
                {
                    "id": "companies",
                    "table": "company_master",
                    "approved": approved,
                    "mapping": {
                        "company_code": "ticker_symbol",
                        "company_name": "issuer",
                        "industry": "sector",
                        "aliases": "aliases_json",
                        "fiscal_year_end_month": "fy_end_month",
                        "timezone": "timezone",
                        "primary_key": ["ticker_symbol"],
                    },
                }
            ],
            "narrative_datasets": [
                {
                    "id": "management_notes",
                    "table": "management_notes",
                    "approved": approved,
                    "mapping": {
                        "company_code": "ticker_symbol",
                        "period": "fiscal_quarter",
                        "title": "note_title",
                        "text": "note_body",
                        "source_id": "note_id",
                        "updated_at": "loaded_at",
                        "primary_key": ["note_id"],
                    },
                }
            ],
        }
    )


def split_period_config() -> ExternalDatabaseConfig:
    return ExternalDatabaseConfig.model_validate(
        {
            "id": "vendor_db",
            "url_env": "TEST_VENDOR_DATABASE_URL",
            "datasets": [
                {
                    "id": "split_facts",
                    "table": "split_period_facts",
                    "approved": True,
                    "mapping": {
                        "company_code": "co_cd",
                        "period": {
                            "type": "year_quarter",
                            "year_column": "fiscal_year",
                            "quarter_column": "fiscal_quarter",
                        },
                        "metric": "metric_code",
                        "value": "metric_value",
                        "unit": "unit",
                        "primary_key": [
                            "co_cd",
                            "fiscal_year",
                            "fiscal_quarter",
                            "metric_code",
                        ],
                    },
                }
            ],
            "narrative_datasets": [
                {
                    "id": "split_notes",
                    "table": "split_period_notes",
                    "approved": True,
                    "mapping": {
                        "company_code": "co_cd",
                        "period": {
                            "type": "year_quarter",
                            "year_column": "fiscal_year",
                            "quarter_column": "fiscal_quarter",
                        },
                        "title": "title",
                        "text": "body",
                        "source_id": "note_id",
                        "primary_key": ["note_id"],
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
            CREATE TABLE company_master (
                ticker_symbol TEXT PRIMARY KEY,
                issuer TEXT NOT NULL,
                sector TEXT,
                aliases_json TEXT,
                fy_end_month INTEGER,
                timezone TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO company_master VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("ACME", "Acme Holdings", "Software", '["Acme", "艾克米"]', 6, "Asia/Taipei"),
                ("OTHER", "Other Corp", "Industrial", "Other|Other Company", 12, "UTC"),
            ],
        )
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
                PRIMARY KEY (ticker_symbol, fiscal_quarter, measure_name),
                FOREIGN KEY (ticker_symbol) REFERENCES company_master(ticker_symbol)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE management_notes (
                note_id TEXT PRIMARY KEY,
                ticker_symbol TEXT NOT NULL,
                fiscal_quarter TEXT NOT NULL,
                note_title TEXT NOT NULL,
                note_body TEXT NOT NULL,
                loaded_at TEXT NOT NULL,
                FOREIGN KEY (ticker_symbol) REFERENCES company_master(ticker_symbol)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE split_period_facts (
                co_cd TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_quarter TEXT NOT NULL,
                metric_code TEXT NOT NULL,
                metric_value REAL NOT NULL,
                unit TEXT NOT NULL,
                PRIMARY KEY (co_cd, fiscal_year, fiscal_quarter, metric_code)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE split_period_notes (
                note_id TEXT PRIMARY KEY,
                co_cd TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_quarter TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO management_notes VALUES (?, ?, ?, ?, ?, ?)",
            (
                "note-1",
                "ACME",
                "2026Q2",
                "Management outlook",
                "Management expects cloud demand to remain strong while capacity is constrained.",
                "2026-07-20T00:00:00Z",
            ),
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
        connection.executemany(
            "INSERT INTO split_period_facts VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("ACME", 2025, "Q3", "revenue", 300.0, "USD_M"),
                ("ACME", 2025, "Q4", "revenue", 330.0, "USD_M"),
                ("OTHER", 2025, "Q3", "revenue", 10.0, "USD_M"),
            ],
        )
        connection.execute(
            "INSERT INTO split_period_notes VALUES (?, ?, ?, ?, ?, ?)",
            (
                "call-2025-q3",
                "ACME",
                2025,
                "Q3",
                "2025 Q3 earnings call",
                "Management discussed AI demand and capacity.",
            ),
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
    acme = next(item for item in companies if item.co_code == "ACME")
    assert acme.aliases == ["Acme", "艾克米"]
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

    calendar = await repository.get_fiscal_calendar("ACME")
    assert calendar is not None
    assert calendar.fiscal_year_end_month == 6
    assert calendar.timezone == "Asia/Taipei"


@pytest.mark.asyncio
async def test_split_year_quarter_columns_are_normalized_and_filtered(vendor_database) -> None:
    repository = ExternalSQLFinanceRepository(split_period_config())

    evidence = await repository.get_metrics("ACME", "2025Q3")
    periods = await repository.list_periods("ACME")

    assert len(evidence) == 1
    assert evidence[0].period == "2025Q3"
    assert evidence[0].metadata["source_period"] == {
        "year_column": "fiscal_year",
        "year_value": 2025,
        "quarter_column": "fiscal_quarter",
        "quarter_value": "Q3",
    }
    assert "fiscal_year" in evidence[0].locator.columns
    assert "fiscal_quarter" in evidence[0].locator.columns
    assert periods == ["2025Q3", "2025Q4"]


def test_split_year_quarter_narrative_period_is_normalized(vendor_database) -> None:
    reader = ExternalSQLNarrativeReader(split_period_config())
    try:
        records = reader.read()
    finally:
        reader.close()

    assert records[0].period == "2025Q3"
    assert records[0].source_period["year_value"] == 2025
    assert records[0].source_period["quarter_value"] == "Q3"


@pytest.mark.parametrize(
    ("year", "quarter", "expected"),
    [(2025, "Q3", "2025Q3"), ("2025", "q3", "2025Q3"), (2025, 3, "2025Q3")],
)
def test_year_quarter_normalizer_accepts_expected_source_values(
    year, quarter, expected
) -> None:
    mapping = split_period_config().datasets[0].mapping.period
    period, _ = normalize_mapped_period(
        {"fiscal_year": year, "fiscal_quarter": quarter}, mapping
    )
    assert period == expected


@pytest.mark.parametrize(("year", "quarter"), [(2025, "Q5"), (2025, ""), (25, "Q1")])
def test_year_quarter_normalizer_rejects_invalid_source_values(year, quarter) -> None:
    mapping = split_period_config().datasets[0].mapping.period
    with pytest.raises(ValueError, match="Invalid fiscal"):
        normalize_mapped_period({"fiscal_year": year, "fiscal_quarter": quarter}, mapping)


def test_approved_narrative_mapping_is_read_with_database_provenance(vendor_database) -> None:
    reader = ExternalSQLNarrativeReader(external_config())
    try:
        records = reader.read()
    finally:
        reader.close()
    documents = build_documents(records)

    assert len(records) == 1
    assert records[0].co_code == "ACME"
    assert records[0].period == "2026Q2"
    assert records[0].source_id.startswith("dbdoc-vendor_db-management_notes-")
    assert records[0].content_hash.startswith("sha256:")
    assert documents[0]["table_id"] == "vendor_db:default:management_notes"
    assert "capacity is constrained" in documents[0]["chunks"][0]["text"]


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
    assert report["dialect"] == "sqlite"
    assert table["foreign_keys"][0]["referred_table"] == "company_master"
    assert all("password" not in json.dumps(item).lower() for item in report["tables"])

    draft = build_registry_draft(report, "vendor_db", "TEST_VENDOR_DATABASE_URL")
    dataset = next(
        item for item in draft["databases"][0]["datasets"] if item["table"] == "vendor_facts"
    )
    assert dataset["approved"] is False
    assert dataset["mapping"]["company_code"] == "ticker_symbol"

    split_table = next(
        item for item in report["tables"] if item["table"] == "split_period_facts"
    )
    assert split_table["mapping_suggestion"]["period"] == {
        "type": "year_quarter",
        "year_column": "fiscal_year",
        "quarter_column": "fiscal_quarter",
    }
    split_draft = next(
        item
        for item in draft["databases"][0]["datasets"]
        if item["table"] == "split_period_facts"
    )
    assert split_draft["mapping"]["period"]["type"] == "year_quarter"

    catalog = build_catalog(report, "vendor_db")
    assert any(item["name"] == "vendor_facts" for item in catalog["tables"])
    assert any(item["name"] == "measure_value" for item in catalog["columns"])
    assert catalog["foreign_keys"]


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


def test_dynamic_database_url_can_be_loaded_from_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DOTENV_ONLY_DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DOTENV_ONLY_DATABASE_URL=mariadb+pymysql://readonly:secret@db/finance\n",
        encoding="utf-8",
    )

    assert resolve_environment_value("DOTENV_ONLY_DATABASE_URL", env_file) == (
        "mariadb+pymysql://readonly:secret@db/finance"
    )


def test_external_finance_mode_requires_an_approved_database(tmp_path) -> None:
    settings = Settings(
        data_mode="local",
        finance_repository_mode="external",
        external_database_config_path=str(tmp_path / "missing.json"),
    )
    with pytest.raises(RuntimeError, match="requires at least one"):
        build_finance_repository(settings)


def test_external_finance_mode_does_not_mount_sqlite(vendor_database, tmp_path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"version": 1, "databases": [external_config().model_dump()]}),
        encoding="utf-8",
    )
    repository = build_finance_repository(
        Settings(
            data_mode="local",
            finance_repository_mode="external",
            sqlite_path=str(tmp_path / "must-not-be-used.sqlite3"),
            external_database_config_path=str(path),
            external_database_strict=True,
        )
    )
    assert isinstance(repository, ExternalSQLFinanceRepository)


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
