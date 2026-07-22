import json
import re
import sqlite3

import pytest

from scripts.ingest_sec import (
    Filing,
    chunk_text,
    html_to_text,
    quarterly_fact,
    relevant_text,
    seed_neo4j,
    seed_sqlite,
)
from scripts.init_sqlite import SCHEMA, migrate_schema


def test_html_adapter_handles_layout_variations() -> None:
    html = """
    <html><style>.hidden{display:none}</style><body>
      <div><b>ITEM 1A.</b>&nbsp;RISK FACTORS</div>
      <p>Supply chain interruptions could materially affect operations.</p>
      <table><tr><td>Cybersecurity incidents may cause financial loss.</td></tr></table>
      <h2>ITEM 2. Unregistered Sales</h2>
    </body></html>
    """
    text = html_to_text(html)
    section = relevant_text(text)
    assert "Supply chain interruptions" in section
    assert "Cybersecurity incidents" in section
    assert "display:none" not in section
    assert chunk_text(section)


def test_sec_chunking_is_bounded_and_preserves_short_and_long_text() -> None:
    text = "\n".join(
        (
            "ITEM 1A.",
            "Short but material heading",
            " ".join(f"risk-{index}" for index in range(300)),
            "Revenue declined.",
        )
    )
    chunks = chunk_text(text, max_chars=240, min_chars=60)

    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    assert chunks
    assert all(60 <= len(chunk) <= 240 for chunk in chunks)
    assert normalize(" ".join(chunks)) == normalize(text)


def test_sec_chunk_limit_refuses_instead_of_truncating() -> None:
    text = "\n".join(f"paragraph {index} " + "x" * 80 for index in range(10))

    with pytest.raises(ValueError, match="refusing to truncate"):
        chunk_text(text, max_chars=120, min_chars=30, max_chunks=2)


def test_sec_reingestion_removes_stale_chunks() -> None:
    class RecordingDriver:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def execute_query(self, query: str, **parameters) -> None:
            self.calls.append((query, parameters))

    driver = RecordingDriver()
    filing = Filing(
        ticker="TEST",
        cik="0000000001",
        company_name="Test Corp",
        accession="test-accession",
        filing_date="2026-04-30",
        report_date="2026-03-31",
        primary_document="test.htm",
        filing_url="https://example.test/test.htm",
        facts_url="https://example.test/facts.json",
        normalized_period="2026Q1",
    )
    seed_neo4j(
        driver,
        filing,
        {"name": "Test Corp", "industry": "Test"},
        ["Material risk disclosure."],
        [[0.1, 0.2]],
        "neo4j",
    )

    stale_call = next(call for call in driver.calls if "DETACH DELETE stale" in call[0])
    assert stale_call[1]["chunk_ids"] == ["sec-test-testaccession-10q-p001"]


def test_companyfacts_adapter_uses_shortest_quarter_and_tag_fallback() -> None:
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": "2026-01-01",
                                "end": "2026-03-31",
                                "val": 100,
                                "accn": "test-accession",
                            },
                            {
                                "start": "2025-07-01",
                                "end": "2026-03-31",
                                "val": 250,
                                "accn": "test-accession",
                            },
                        ]
                    }
                }
            }
        }
    }
    tag, value = quarterly_fact(
        facts,
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"),
        "test-accession",
        "2026-03-31",
    )
    assert tag == "Revenues"
    assert value["val"] == 100


def test_legacy_sqlite_schema_migrates_without_losing_rows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE data_sources (
          source_id TEXT PRIMARY KEY, co_code TEXT, source_type TEXT,
          title TEXT, captured_at TEXT, content_hash TEXT, data_version TEXT
        );
        INSERT INTO data_sources VALUES
          ('legacy', 'TEST', 'database', 'Legacy', NULL, NULL, 'v1');
        """
    )
    migrate_schema(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(data_sources)")}
    assert {"source_url", "raw_locator"} <= columns
    assert connection.execute("SELECT count(*) FROM data_sources").fetchone()[0] == 1
    json.dumps(SCHEMA)


def test_sec_v2_fact_preserves_exact_integer_value() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA)
    filing = Filing(
        ticker="TEST",
        cik="0000000001",
        company_name="Test Corp",
        accession="test-accession",
        filing_date="2026-04-30",
        report_date="2026-03-31",
        primary_document="test.htm",
        filing_url="https://example.test/test.htm",
        facts_url="https://example.test/facts.json",
        normalized_period="2026Q1",
    )
    seed_sqlite(
        connection,
        filing,
        {
            "name": "Test Corp",
            "industry": "Test",
            "aliases": ["Test"],
            "fiscal_year_end_month": 12,
        },
        "Revenues",
        {"val": 82886000000},
        "GrossProfit",
        {"val": 57000000000},
        b'{"facts":"source"}',
    )
    value = connection.execute(
        "SELECT value_exact FROM financial_facts WHERE metric_code = 'revenue'"
    ).fetchone()[0]
    assert value == "82886000000"
