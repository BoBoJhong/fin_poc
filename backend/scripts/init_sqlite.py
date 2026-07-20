from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.financial_data import (
    FINANCIAL_SCHEMA_V2,
    MetricDefinition,
    NormalizationContext,
    ProviderMetricMapping,
    persist_normalized_financial_payload,
)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    co_code TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    industry TEXT,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_aliases (
    co_code TEXT NOT NULL,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'name',
    PRIMARY KEY (co_code, alias),
    FOREIGN KEY (co_code) REFERENCES companies(co_code)
);

CREATE INDEX IF NOT EXISTS idx_company_alias
    ON company_aliases (alias);

CREATE TABLE IF NOT EXISTS company_fiscal_calendars (
    co_code TEXT PRIMARY KEY,
    fiscal_year_end_month INTEGER NOT NULL CHECK (fiscal_year_end_month BETWEEN 1 AND 12),
    timezone TEXT NOT NULL DEFAULT 'UTC',
    source TEXT NOT NULL DEFAULT 'company_master',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (co_code) REFERENCES companies(co_code)
);

CREATE TABLE IF NOT EXISTS data_sources (
    source_id TEXT PRIMARY KEY,
    co_code TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    captured_at TEXT,
    content_hash TEXT,
    data_version TEXT NOT NULL,
    source_url TEXT,
    raw_locator TEXT,
    FOREIGN KEY (co_code) REFERENCES companies(co_code)
);

CREATE INDEX IF NOT EXISTS idx_sources_scope
    ON data_sources (co_code, source_type);

CREATE TABLE IF NOT EXISTS financial_metrics (
    co_code TEXT NOT NULL,
    period TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    scope TEXT NOT NULL,
    source_id TEXT NOT NULL,
    data_version TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (co_code, period, metric_code),
    FOREIGN KEY (co_code) REFERENCES companies(co_code),
    FOREIGN KEY (source_id) REFERENCES data_sources(source_id)
);

CREATE INDEX IF NOT EXISTS idx_metrics_period
    ON financial_metrics (co_code, period);
"""

SCHEMA += FINANCIAL_SCHEMA_V2


def migrate_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(data_sources)").fetchall()
    }
    for name, column_type in (
        ("source_url", "TEXT"),
        ("raw_locator", "TEXT"),
    ):
        if name not in columns:
            connection.execute(f"ALTER TABLE data_sources ADD COLUMN {name} {column_type}")


def seed_demo(connection: sqlite3.Connection) -> None:
    now = datetime.now(UTC).isoformat()
    connection.executemany(
        """
        INSERT OR REPLACE INTO companies
            (co_code, company_name, industry, is_synthetic, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("DEMO01", "範例科技股份有限公司", "企業軟體", 1, now),
            ("DEMO02", "示範製造股份有限公司", "智慧製造", 1, now),
        ],
    )
    definitions = [
        MetricDefinition(
            metric_code="revenue",
            display_name="營業收入",
            statement_type="income_statement",
            data_type="monetary",
            default_unit="TWD_100M",
            duration_type="quarter",
            aliases=["營收", "營業收入", "revenue"],
            approved=True,
        ),
        MetricDefinition(
            metric_code="gross_margin",
            display_name="毛利率",
            statement_type="income_statement",
            data_type="percentage",
            default_unit="PERCENT",
            duration_type="quarter",
            aliases=["毛利率", "gross margin"],
            approved=True,
        ),
    ]
    mappings = [
        ProviderMetricMapping(
            provider_id="demo",
            provider_metric_key="revenue",
            metric_code="revenue",
            approved=True,
        ),
        ProviderMetricMapping(
            provider_id="demo",
            provider_metric_key="gross_margin",
            metric_code="gross_margin",
            approved=True,
        ),
    ]
    connection.executemany(
        """
        INSERT OR REPLACE INTO company_fiscal_calendars
            (co_code, fiscal_year_end_month, timezone, source, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("DEMO01", 12, "Asia/Taipei", "demo", now),
            ("DEMO02", 12, "Asia/Taipei", "demo", now),
        ],
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO data_sources
            (source_id, co_code, source_type, title, captured_at, content_hash, data_version)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "demo01-financial-metrics-2026q2",
                "DEMO01",
                "database",
                "範例科技 2026 Q2 財務指標（虛構）",
                now,
                "sha256:demo01-metrics-v1",
                "demo-v1",
            ),
            (
                "demo02-financial-metrics-2026q2",
                "DEMO02",
                "database",
                "示範製造 2026 Q2 財務指標（虛構）",
                now,
                "sha256:demo02-metrics-v1",
                "demo-v1",
            ),
        ],
    )
    for code, source_id, data in (
        (
            "DEMO01",
            "demo01-financial-metrics-2026q2",
            {
                "revenue": {"value": "128.4", "unit": "TWD_100M"},
                "gross_margin": {"value": "42.1", "unit": "PERCENT"},
            },
        ),
        (
            "DEMO02",
            "demo02-financial-metrics-2026q2",
            {"revenue": {"value": "76.2", "unit": "TWD_100M"}},
        ),
    ):
        persist_normalized_financial_payload(
            connection,
            {"data": data},
            NormalizationContext(
                provider_id="demo",
                co_code=code,
                period="2026Q2",
                fiscal_year=2026,
                fiscal_quarter=2,
                source_id=source_id,
                data_version="demo-v1",
                captured_at=now,
                consolidation_scope="consolidated",
            ),
            definitions,
            mappings,
        )
    connection.executemany(
        """
        INSERT OR REPLACE INTO company_aliases (co_code, alias, alias_type)
        VALUES (?, ?, ?)
        """,
        [
            ("DEMO01", "範例科技", "short_name"),
            ("DEMO01", "範科", "alias"),
            ("DEMO02", "示範製造", "short_name"),
            ("DEMO02", "示製", "alias"),
        ],
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO financial_metrics
            (co_code, period, metric_code, value, unit, scope,
             source_id, data_version, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "DEMO01", "2026Q2", "revenue", 128.4, "TWD_100M",
                "consolidated_quarter", "demo01-financial-metrics-2026q2",
                "demo-v1", now,
            ),
            (
                "DEMO01", "2026Q2", "gross_margin", 42.1, "PERCENT",
                "consolidated_quarter", "demo01-financial-metrics-2026q2",
                "demo-v1", now,
            ),
            (
                "DEMO02", "2026Q2", "revenue", 76.2, "TWD_100M",
                "consolidated_quarter", "demo02-financial-metrics-2026q2",
                "demo-v1", now,
            ),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the local SQLite schema.")
    parser.add_argument("--path", help="Override SQLITE_PATH")
    parser.add_argument("--seed-demo", action="store_true")
    args = parser.parse_args()
    configured = get_settings().sqlite_database_path
    path = Path(args.path).expanduser().resolve() if args.path else configured
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        migrate_schema(connection)
        if args.seed_demo:
            seed_demo(connection)
        connection.commit()
    print(f"SQLite ready: {path}")


if __name__ == "__main__":
    main()
