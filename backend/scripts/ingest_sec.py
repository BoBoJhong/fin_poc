from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
from neo4j import GraphDatabase

from app.config import PROJECT_ROOT, get_settings
from app.financial_data import (
    MetricDefinition,
    NormalizationContext,
    ProviderMetricMapping,
    persist_normalized_financial_payload,
)
from scripts.init_data import create_indexes, embed, sha256
from scripts.init_sqlite import SCHEMA, migrate_schema
from scripts.text_blocks import build_semantic_blocks


SEC_COMPANIES = {
    "AAPL": {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "industry": "Technology Hardware",
        "aliases": ["Apple", "蘋果", "蘋果公司"],
        "fiscal_year_end_month": 9,
    },
    "MSFT": {
        "cik": "0000789019",
        "name": "Microsoft Corporation",
        "industry": "Software and Cloud",
        "aliases": ["Microsoft", "微軟"],
        "fiscal_year_end_month": 6,
    },
    "NVDA": {
        "cik": "0001045810",
        "name": "NVIDIA Corporation",
        "industry": "Semiconductors",
        "aliases": ["NVIDIA", "Nvidia", "輝達", "英偉達"],
        "fiscal_year_end_month": 1,
    },
}

REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
RISK_CATEGORIES = {
    "Supply chain and component availability": (
        "supply chain",
        "supplier",
        "component availability",
        "manufacturing capacity",
    ),
    "Cybersecurity": ("cybersecurity", "cyber attack", "security incident"),
    "Competition": ("competition", "competitive", "competitors"),
    "Regulatory and export controls": (
        "export control",
        "regulatory",
        "government regulation",
        "trade restriction",
    ),
    "AI infrastructure capacity": (
        "data center",
        "datacenter",
        "energy availability",
        "ai infrastructure",
    ),
    "Macroeconomic and foreign exchange": (
        "foreign exchange",
        "macroeconomic",
        "interest rate",
        "inflation",
    ),
}


@dataclass(slots=True)
class Filing:
    ticker: str
    cik: str
    company_name: str
    accession: str
    filing_date: str
    report_date: str
    primary_document: str
    filing_url: str
    facts_url: str
    normalized_period: str | None = None

    @property
    def period(self) -> str:
        if self.normalized_period:
            return self.normalized_period
        report_date = date.fromisoformat(self.report_date)
        quarter = (report_date.month - 1) // 3 + 1
        return f"{report_date.year}Q{quarter}"

    @property
    def version(self) -> str:
        return f"sec:{self.accession}"


class TextExtractor(HTMLParser):
    BLOCKS = {
        "p",
        "div",
        "br",
        "tr",
        "td",
        "th",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self.ignored_depth += 1
        elif tag in self.BLOCKS and not self.ignored_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag in self.BLOCKS and not self.ignored_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    parser = TextExtractor()
    parser.feed(html)
    return parser.text()


def relevant_text(text: str) -> str:
    patterns = (
        (r"item\s+1a[.\s:-]*risk factors", r"item\s+2[.\s:-]"),
        (
            r"item\s+2[.\s:-]*management['’]?s discussion and analysis",
            r"item\s+3[.\s:-]",
        ),
    )
    sections: list[str] = []
    for start_pattern, end_pattern in patterns:
        starts = list(re.finditer(start_pattern, text, re.IGNORECASE))
        candidates: list[str] = []
        for start in starts:
            end = re.search(end_pattern, text[start.end() :], re.IGNORECASE)
            if end:
                candidate = text[start.start() : start.end() + end.start()]
                if 40 <= len(candidate) <= 120_000:
                    candidates.append(candidate)
        if candidates:
            sections.append(max(candidates, key=len))
    if sections:
        return "\n".join(sections)
    keyword_lines = [
        line
        for line in text.splitlines()
        if any(
            keyword in line.casefold()
            for keyword in (
                "risk",
                "revenue",
                "gross margin",
                "supply",
                "competition",
                "cybersecurity",
            )
        )
    ]
    return "\n".join(keyword_lines[:500]) or text[:80_000]


def chunk_text(
    text: str,
    max_chars: int = 1_200,
    min_chars: int = 240,
    max_chunks: int | None = None,
) -> list[str]:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    chunks = build_semantic_blocks(
        paragraphs,
        max_chars=max_chars,
        min_chars=min(min_chars, max_chars // 2),
    )
    if max_chunks is not None and len(chunks) > max_chunks:
        raise ValueError(
            f"SEC text requires {len(chunks)} chunks, exceeding explicit limit {max_chunks}; "
            "refusing to truncate source text"
        )
    return chunks


def latest_10q(ticker: str, payload: dict[str, Any]) -> Filing:
    company = SEC_COMPANIES[ticker]
    recent = payload["filings"]["recent"]
    rows = [dict(zip(recent, values, strict=True)) for values in zip(*recent.values(), strict=True)]
    item = next(row for row in rows if row["form"] == "10-Q")
    accession_path = item["accessionNumber"].replace("-", "")
    cik_path = str(int(company["cik"]))
    filing_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_path}/"
        f"{accession_path}/{item['primaryDocument']}"
    )
    return Filing(
        ticker=ticker,
        cik=company["cik"],
        company_name=company["name"],
        accession=item["accessionNumber"],
        filing_date=item["filingDate"],
        report_date=item["reportDate"],
        primary_document=item["primaryDocument"],
        filing_url=filing_url,
        facts_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company['cik']}.json",
    )


def quarterly_fact(
    facts: dict[str, Any], tags: tuple[str, ...], accession: str, report_date: str
) -> tuple[str, dict[str, Any]]:
    matches: list[tuple[str, dict[str, Any], int]] = []
    for tag in tags:
        fact = facts.get("facts", {}).get("us-gaap", {}).get(tag, {})
        for item in fact.get("units", {}).get("USD", []):
            if item.get("accn") != accession or item.get("end") != report_date:
                continue
            if not item.get("start"):
                continue
            duration = (date.fromisoformat(item["end"]) - date.fromisoformat(item["start"])).days
            if 70 <= duration <= 120:
                matches.append((tag, item, duration))
    if not matches:
        raise ValueError(f"No quarterly SEC fact for tags={tags}, accession={accession}")
    tag, item, _ = min(matches, key=lambda match: match[2])
    return tag, item


def risk_categories(text: str) -> list[str]:
    lowered = text.casefold()
    return [
        category
        for category, keywords in RISK_CATEGORIES.items()
        if any(keyword in lowered for keyword in keywords)
    ]


def download(client: httpx.Client, url: str, path: Path) -> bytes:
    response = client.get(url)
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return response.content


def seed_sqlite(
    connection: sqlite3.Connection,
    filing: Filing,
    company: dict[str, Any],
    revenue_tag: str,
    revenue: dict[str, Any],
    gross_tag: str,
    gross_profit: dict[str, Any],
    facts_bytes: bytes,
) -> None:
    now = datetime.now(UTC).isoformat()
    source_id = f"sec-{filing.ticker.lower()}-{filing.period.lower()}-companyfacts"
    gross_margin_exact = (
        Decimal(str(gross_profit["val"])) / Decimal(str(revenue["val"])) * Decimal("100")
    ).quantize(Decimal("0.000001"))
    gross_margin = float(gross_margin_exact)
    connection.execute(
        """
        INSERT OR REPLACE INTO companies
            (co_code, company_name, industry, is_synthetic, updated_at)
        VALUES (?, ?, ?, 0, ?)
        """,
        (filing.ticker, company["name"], company["industry"], now),
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO company_aliases (co_code, alias, alias_type)
        VALUES (?, ?, ?)
        """,
        [(filing.ticker, alias, "name") for alias in company["aliases"]],
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO company_fiscal_calendars
            (co_code, fiscal_year_end_month, timezone, source, updated_at)
        VALUES (?, ?, 'America/New_York', 'SEC company profile', ?)
        """,
        (filing.ticker, company["fiscal_year_end_month"], now),
    )
    locator = {
        "cik": filing.cik,
        "accession": filing.accession,
        "revenue_tag": f"us-gaap:{revenue_tag}",
        "gross_profit_tag": f"us-gaap:{gross_tag}",
        "report_date": filing.report_date,
    }
    connection.execute(
        """
        INSERT OR REPLACE INTO data_sources
            (source_id, co_code, source_type, title, captured_at, content_hash,
             data_version, source_url, raw_locator)
        VALUES (?, ?, 'database', ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            filing.ticker,
            f"{filing.company_name} SEC Company Facts {filing.period}",
            now,
            "sha256:" + hashlib.sha256(facts_bytes).hexdigest(),
            filing.version,
            filing.facts_url,
            json.dumps(locator, ensure_ascii=False),
        ),
    )
    metrics = (
        ("revenue", float(revenue["val"]), "USD"),
        ("gross_profit", float(gross_profit["val"]), "USD"),
        ("gross_margin", gross_margin, "PERCENT"),
    )
    exact_values = {
        "revenue": str(revenue["val"]),
        "gross_profit": str(gross_profit["val"]),
        "gross_margin": str(gross_margin_exact),
    }
    connection.executemany(
        """
        INSERT OR REPLACE INTO financial_metrics
            (co_code, period, metric_code, value, unit, scope,
             source_id, data_version, updated_at)
        VALUES (?, ?, ?, ?, ?, 'consolidated_quarter', ?, ?, ?)
        """,
        [
            (
                filing.ticker,
                filing.period,
                metric_code,
                value,
                unit,
                source_id,
                filing.version,
                now,
            )
            for metric_code, value, unit in metrics
        ],
    )
    definitions = [
        MetricDefinition(
            metric_code="revenue",
            display_name="Revenue",
            statement_type="income_statement",
            data_type="monetary",
            default_unit="USD",
            duration_type="quarter",
            aliases=["revenue", "sales", "營收"],
            approved=True,
        ),
        MetricDefinition(
            metric_code="gross_profit",
            display_name="Gross profit",
            statement_type="income_statement",
            data_type="monetary",
            default_unit="USD",
            duration_type="quarter",
            aliases=["gross profit", "毛利"],
            approved=True,
        ),
        MetricDefinition(
            metric_code="gross_margin",
            display_name="Gross margin",
            statement_type="income_statement",
            data_type="percentage",
            default_unit="PERCENT",
            duration_type="quarter",
            aliases=["gross margin", "毛利率"],
            approved=True,
        ),
    ]
    provider_keys = {
        "revenue": f"us-gaap:{revenue_tag}",
        "gross_profit": f"us-gaap:{gross_tag}",
        "gross_margin": "derived:gross_margin",
    }
    mappings = [
        ProviderMetricMapping(
            provider_id="sec_companyfacts",
            provider_metric_key=provider_key,
            metric_code=metric_code,
            approved=True,
        )
        for metric_code, provider_key in provider_keys.items()
    ]
    persist_normalized_financial_payload(
        connection,
        {
            "data": {
                provider_keys[metric_code]: {"value": exact_values[metric_code], "unit": unit}
                for metric_code, value, unit in metrics
            },
            "source_payload_hash": "sha256:" + hashlib.sha256(facts_bytes).hexdigest(),
            "source_locator": locator,
        },
        NormalizationContext(
            provider_id="sec_companyfacts",
            co_code=filing.ticker,
            period=filing.period,
            fiscal_year=int(filing.period[:4]),
            fiscal_quarter=int(filing.period[-1]),
            period_end=filing.report_date,
            source_id=source_id,
            data_version=filing.version,
            captured_at=now,
            consolidation_scope="consolidated",
            dimensions={"form_type": "10-Q", "accession": filing.accession},
        ),
        definitions,
        mappings,
    )


def seed_neo4j(
    driver: Any,
    filing: Filing,
    company: dict[str, Any],
    chunks: list[str],
    vectors: list[list[float]],
    database: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    source_id = f"sec-{filing.ticker.lower()}-{filing.accession.replace('-', '')}-10q"
    rows = [
        {
            "chunk_id": f"{source_id}-p{index:03d}",
            "co_code": filing.ticker,
            "source_id": source_id,
            "title": f"{filing.company_name} Form 10-Q ({filing.report_date})",
            "period": filing.period,
            "text": text,
            "sequence": index,
            "paragraph_id": f"sec-chunk-{index:03d}",
            "embedding": vector,
            "captured_at": now,
            "content_hash": sha256(text),
            "categories": risk_categories(text),
        }
        for index, (text, vector) in enumerate(zip(chunks, vectors, strict=True), start=1)
    ]
    driver.execute_query(
        """
        MERGE (company:Company {co_code: $co_code})
          SET company.name = $company_name, company.industry = $industry
        MERGE (document:Document {source_id: $source_id})
          SET document.co_code = $co_code,
              document.source_type = 'financial_report',
              document.title = $title,
              document.period = $period,
              document.live_url = $live_url,
              document.captured_at = $captured_at,
              document.content_hash = $content_hash,
              document.data_version = $data_version,
              document.accession = $accession,
              document.report_date = $report_date
        MERGE (company)-[:HAS_DOCUMENT]->(document)
        """,
        co_code=filing.ticker,
        company_name=company["name"],
        industry=company["industry"],
        source_id=source_id,
        title=f"{filing.company_name} Form 10-Q ({filing.report_date})",
        period=filing.period,
        live_url=filing.filing_url,
        captured_at=now,
        content_hash=sha256("\n".join(chunks)),
        data_version=filing.version,
        accession=filing.accession,
        report_date=filing.report_date,
        database_=database,
    )
    driver.execute_query(
        """
        MATCH (document:Document {source_id: $source_id})-[:HAS_CHUNK]->(stale:Chunk)
        WHERE NOT stale.chunk_id IN $chunk_ids
        DETACH DELETE stale
        """,
        source_id=source_id,
        chunk_ids=[row["chunk_id"] for row in rows],
        database_=database,
    )
    driver.execute_query(
        """
        UNWIND $rows AS item
        MATCH (document:Document {source_id: item.source_id})
        MATCH (company:Company {co_code: item.co_code})
        MERGE (chunk:Chunk {chunk_id: item.chunk_id})
          SET chunk.co_code = item.co_code,
              chunk.source_id = item.source_id,
              chunk.source_type = 'financial_report',
              chunk.title = item.title,
              chunk.period = item.period,
              chunk.text = item.text,
              chunk.sequence = item.sequence,
              chunk.paragraph_id = item.paragraph_id,
              chunk.embedding = item.embedding,
              chunk.captured_at = item.captured_at,
              chunk.content_hash = item.content_hash,
              chunk.data_version = $data_version
        MERGE (document)-[:HAS_CHUNK]->(chunk)
        FOREACH (category IN item.categories |
          MERGE (risk:Risk {name: category, co_code: item.co_code})
          MERGE (chunk)-[m:MENTIONS]->(risk)
            SET m.co_code = item.co_code, m.source_id = item.source_id,
                m.period = item.period, m.data_version = $data_version,
                m.provenance_text = item.text
          MERGE (risk)-[a:AFFECTS]->(company)
            SET a.co_code = item.co_code, a.source_id = item.source_id,
                a.period = item.period, a.data_version = $data_version,
                a.provenance_text = item.text
        )
        """,
        rows=rows,
        data_version=filing.version,
        database_=database,
    )


def ingest(tickers: list[str]) -> dict[str, Any]:
    settings = get_settings()
    raw_root = PROJECT_ROOT / "data" / "raw" / "sec"
    headers = {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
    filings: list[Filing] = []
    normalized: list[tuple[Filing, dict[str, Any], bytes, list[str]]] = []
    with httpx.Client(headers=headers, timeout=60, follow_redirects=True) as client:
        for ticker in tickers:
            company = SEC_COMPANIES[ticker]
            ticker_root = raw_root / ticker.lower()
            submissions_url = f"https://data.sec.gov/submissions/CIK{company['cik']}.json"
            submissions_bytes = download(client, submissions_url, ticker_root / "submissions.json")
            filing = latest_10q(ticker, json.loads(submissions_bytes))
            facts_bytes = download(client, filing.facts_url, ticker_root / "companyfacts.json")
            html_bytes = download(
                client,
                filing.filing_url,
                ticker_root / f"{filing.accession.replace('-', '')}.html",
            )
            text = relevant_text(html_to_text(html_bytes.decode("utf-8", errors="replace")))
            chunks = chunk_text(text)
            if not chunks:
                raise ValueError(f"No usable filing chunks for {ticker}")
            filings.append(filing)
            normalized.append((filing, json.loads(facts_bytes), facts_bytes, chunks))

    sqlite_path = settings.sqlite_database_path
    with sqlite3.connect(sqlite_path) as connection:
        connection.executescript(SCHEMA)
        migrate_schema(connection)
        for filing, facts, facts_bytes, _ in normalized:
            revenue_tag, revenue = quarterly_fact(
                facts, REVENUE_TAGS, filing.accession, filing.report_date
            )
            frame = str(revenue.get("frame", ""))
            frame_match = re.fullmatch(r"CY(20\d{2})Q([1-4])", frame)
            if frame_match:
                filing.normalized_period = f"{frame_match.group(1)}Q{frame_match.group(2)}"
            gross_tag, gross_profit = quarterly_fact(
                facts, ("GrossProfit",), filing.accession, filing.report_date
            )
            seed_sqlite(
                connection,
                filing,
                SEC_COMPANIES[filing.ticker],
                revenue_tag,
                revenue,
                gross_tag,
                gross_profit,
                facts_bytes,
            )
        connection.commit()

    all_chunks = [chunk for _, _, _, chunks in normalized for chunk in chunks]
    vectors: list[list[float]] = []
    for offset in range(0, len(all_chunks), 24):
        vectors.extend(
            embed(
                all_chunks[offset : offset + 24],
                settings.ollama_url,
                settings.ollama_embedding_model,
            )
        )
    dimensions = len(vectors[0])
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        driver.verify_connectivity()
        create_indexes(
            driver,
            dimensions,
            settings.neo4j_database,
            settings.neo4j_vector_index,
            settings.neo4j_fulltext_index,
        )
        offset = 0
        for filing, _, _, chunks in normalized:
            chunk_vectors = vectors[offset : offset + len(chunks)]
            offset += len(chunks)
            seed_neo4j(
                driver,
                filing,
                SEC_COMPANIES[filing.ticker],
                chunks,
                chunk_vectors,
                settings.neo4j_database,
            )
    finally:
        driver.close()
    return {
        "status": "ok",
        "companies": len(filings),
        "documents": len(filings),
        "chunks": len(all_chunks),
        "embedding_dimensions": dimensions,
        "filings": [
            {
                "ticker": filing.ticker,
                "period": filing.period,
                "accession": filing.accession,
                "filing_url": filing.filing_url,
            }
            for filing in filings
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest official SEC 10-Q and XBRL data.")
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=list(SEC_COMPANIES),
        choices=sorted(SEC_COMPANIES),
    )
    args = parser.parse_args()
    print(json.dumps(ingest(args.tickers), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
