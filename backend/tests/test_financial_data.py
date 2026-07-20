import json
import sqlite3
from decimal import Decimal

import pytest

from app.config import Settings
from app.agents import FinancialAgentService
from app.financial_data import (
    MetricDefinition,
    NormalizationContext,
    ProviderMetricMapping,
    normalize_financial_payload,
    persist_normalized_financial_payload,
)
from app.repositories import SQLiteFinanceRepository
from app.models import Evidence, SourceLocator
from scripts.init_sqlite import SCHEMA, migrate_schema


def definitions() -> list[MetricDefinition]:
    return [
        MetricDefinition(
            metric_code="revenue",
            display_name="營業收入",
            statement_type="income_statement",
            data_type="monetary",
            default_unit="TWD",
            duration_type="quarter",
            aliases=["營收"],
            approved=True,
        ),
        MetricDefinition(
            metric_code="basic_eps",
            display_name="基本每股盈餘",
            statement_type="income_statement",
            data_type="per_share",
            default_unit="TWD_PER_SHARE",
            duration_type="quarter",
            approved=True,
        ),
    ]


def mappings() -> list[ProviderMetricMapping]:
    return [
        ProviderMetricMapping(
            provider_id="company_api",
            provider_metric_key="income_statement.營業收入合計",
            metric_code="revenue",
            approved=True,
        ),
        ProviderMetricMapping(
            provider_id="company_api",
            provider_metric_key="income_statement.基本每股盈餘",
            metric_code="basic_eps",
            approved=True,
        ),
    ]


def context(version: str = "rev-1") -> NormalizationContext:
    return NormalizationContext(
        provider_id="company_api",
        co_code="2330",
        period="2026Q1",
        fiscal_year=2026,
        fiscal_quarter=1,
        period_start="2026-01-01",
        period_end="2026-03-31",
        source_id="filing-2330-2026q1",
        data_version=version,
        consolidation_scope="consolidated",
    )


def payload(revenue: str = "839253000000") -> dict:
    return {
        "data": {
            "income_statement": {
                "營業收入合計": {"value": revenue, "unit": "TWD"},
                "基本每股盈餘": {"value": "13.94", "unit": "TWD_PER_SHARE"},
                "尚未定義的新指標": {"value": "77", "unit": "TWD"},
            }
        }
    }


def seed_prerequisites(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    migrate_schema(connection)
    connection.execute(
        "INSERT INTO companies VALUES ('2330', '台灣積體電路製造', 'Semiconductors', 0, 'now')"
    )
    connection.execute(
        """
        INSERT INTO data_sources
            (source_id, co_code, source_type, title, captured_at, content_hash, data_version)
        VALUES ('filing-2330-2026q1', '2330', 'database', '2330 2026Q1',
                'now', 'sha256:source', 'rev-1')
        """
    )


def test_dynamic_nested_keys_are_mapped_without_guessing_unknown_metrics() -> None:
    result = normalize_financial_payload(payload(), context(), definitions(), mappings())

    assert {fact.metric_code for fact in result.facts} == {"revenue", "basic_eps"}
    revenue = next(fact for fact in result.facts if fact.metric_code == "revenue")
    assert revenue.value_exact == Decimal("839253000000")
    assert revenue.provider_metric_key == "income_statement.營業收入合計"
    assert result.unmapped_metric_keys == ["income_statement.尚未定義的新指標"]


@pytest.mark.asyncio
async def test_v2_facts_preserve_exact_value_and_emit_stable_evidence(tmp_path) -> None:
    path = tmp_path / "financial-v2.sqlite3"
    with sqlite3.connect(path) as connection:
        seed_prerequisites(connection)
        persist_normalized_financial_payload(
            connection, payload(), context(), definitions(), mappings()
        )
        connection.commit()

    repository = SQLiteFinanceRepository(
        Settings(sqlite_path=str(path), sqlite_read_only=True, data_mode="local")
    )
    evidence = await repository.get_metrics("2330", "2026Q1")
    preview = await repository.get_source_preview("filing-2330-2026q1", "2330")

    assert len(evidence) == 2
    revenue = next(item for item in evidence if item.metadata["metric_code"] == "revenue")
    assert revenue.locator.table == "financial_facts"
    assert revenue.metadata["value_exact"] == "839253000000"
    assert revenue.metadata["statement_type"] == "income_statement"
    assert preview is not None
    assert preview.database_record["table"] == "financial_facts"


def test_new_revision_marks_previous_semantic_fact_non_current(tmp_path) -> None:
    path = tmp_path / "revisions.sqlite3"
    with sqlite3.connect(path) as connection:
        seed_prerequisites(connection)
        persist_normalized_financial_payload(
            connection, payload(), context("rev-1"), definitions(), mappings()
        )
        persist_normalized_financial_payload(
            connection, payload("840000000000"), context("rev-2"), definitions(), mappings()
        )
        rows = connection.execute(
            """
            SELECT value_exact, data_version, is_current
            FROM financial_facts
            WHERE metric_code = 'revenue'
            ORDER BY data_version
            """
        ).fetchall()
        raw_payload = connection.execute(
            "SELECT payload_json FROM financial_raw_payloads WHERE data_version = 'rev-1'"
        ).fetchone()[0]

    assert rows == [
        ("839253000000", "rev-1", 0),
        ("840000000000", "rev-2", 1),
    ]
    assert json.loads(raw_payload)["data"]["income_statement"]["營業收入合計"]["value"] == (
        "839253000000"
    )


def test_metric_dictionary_alias_ranks_requested_fact_before_arbitrary_metrics() -> None:
    items = [
        Evidence(
            evidence_id=f"ev-{index}",
            co_code="2330",
            source_id="source",
            source_type="database",
            title="fact",
            content="fact",
            score=1.0,
            period="2026Q1",
            locator=SourceLocator(table="financial_facts", primary_key=f"fact-{index}"),
            metadata={
                "metric_code": f"metric_{index}",
                "metric_display_name": f"指標 {index}",
                "metric_aliases": [],
                "value": index,
                "unit": "TWD",
                "scope": "consolidated_quarter",
            },
        )
        for index in range(10)
    ]
    items[-1].metadata.update(
        {
            "metric_code": "basic_eps",
            "metric_display_name": "基本每股盈餘",
            "metric_aliases": ["EPS", "每股盈餘"],
        }
    )
    service = object.__new__(FinancialAgentService)
    service.max_evidence_items = 3

    selected = service._select_diverse_evidence(items, "台積電 2026 Q1 EPS 是多少？")

    assert selected[0].metadata["metric_code"] == "basic_eps"
