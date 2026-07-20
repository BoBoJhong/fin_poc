from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime

from neo4j import GraphDatabase

from app.config import get_settings
from scripts.init_data import create_indexes, embed, sha256
from scripts.init_sqlite import SCHEMA, migrate_schema


DATA_VERSION = "scale-v1"


def company_row(index: int) -> dict:
    suffix = f"{index:04d}"
    co_code = f"TST{suffix}"
    return {
        "co_code": co_code,
        "company_name": f"壓測企業{suffix}股份有限公司",
        "short_name": f"壓測企業{suffix}",
        "alias": f"壓企{suffix}",
        "industry": ("半導體", "企業軟體", "智慧製造")[index % 3],
        "product": f"產品P{suffix}",
        "risk": f"供應鏈節點風險R{suffix}",
        "revenue": round(100 + index * 1.25, 2),
        "gross_margin": round(30 + (index % 20) * 0.5, 2),
    }


def build_rows(count: int) -> list[dict]:
    return [company_row(index) for index in range(1, count + 1)]


def seed_sqlite(rows: list[dict]) -> None:
    settings = get_settings()
    path = settings.sqlite_database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        migrate_schema(connection)
        connection.executemany(
            """
            INSERT OR REPLACE INTO companies
                (co_code, company_name, industry, is_synthetic, updated_at)
            VALUES (:co_code, :company_name, :industry, 1, :updated_at)
            """,
            [{**row, "updated_at": now} for row in rows],
        )
        aliases = [
            {
                "co_code": row["co_code"],
                "alias": alias,
                "alias_type": alias_type,
            }
            for row in rows
            for alias, alias_type in (
                (row["short_name"], "short_name"),
                (row["alias"], "alias"),
            )
        ]
        connection.executemany(
            """
            INSERT OR REPLACE INTO company_aliases (co_code, alias, alias_type)
            VALUES (:co_code, :alias, :alias_type)
            """,
            aliases,
        )
        sources = [
            {
                "source_id": f"scale-{row['co_code'].lower()}-metrics-2026q2",
                "co_code": row["co_code"],
                "title": f"{row['company_name']} 2026 Q2 財務指標（合成）",
                "captured_at": now,
                "content_hash": sha256(
                    f"{row['co_code']}|2026Q2|{row['revenue']}|{row['gross_margin']}"
                ),
            }
            for row in rows
        ]
        connection.executemany(
            """
            INSERT OR REPLACE INTO data_sources
                (source_id, co_code, source_type, title, captured_at,
                 content_hash, data_version)
            VALUES (:source_id, :co_code, 'database', :title, :captured_at,
                    :content_hash, 'scale-v1')
            """,
            sources,
        )
        metrics = [
            {
                "co_code": row["co_code"],
                "period": "2026Q2",
                "metric_code": metric_code,
                "value": row[value_key],
                "unit": unit,
                "source_id": f"scale-{row['co_code'].lower()}-metrics-2026q2",
                "updated_at": now,
            }
            for row in rows
            for metric_code, value_key, unit in (
                ("revenue", "revenue", "TWD_100M"),
                ("gross_margin", "gross_margin", "PERCENT"),
            )
        ]
        connection.executemany(
            """
            INSERT OR REPLACE INTO financial_metrics
                (co_code, period, metric_code, value, unit, scope,
                 source_id, data_version, updated_at)
            VALUES (:co_code, :period, :metric_code, :value, :unit,
                    'consolidated_quarter', :source_id, 'scale-v1', :updated_at)
            """,
            metrics,
        )
        connection.commit()


def build_documents(rows: list[dict], vectors: list[list[float]]) -> tuple[list[dict], list[dict]]:
    now = datetime.now(UTC).isoformat()
    documents: list[dict] = []
    chunks: list[dict] = []
    vector_index = 0
    for row in rows:
        source_id = f"scale-{row['co_code'].lower()}-2026q2-call"
        texts = [
            (
                f"{row['company_name']}財務長表示，2026 Q2 主要風險為"
                f"{row['risk']}，目前尚未調整全年展望。"
            ),
            (
                f"{row['company_name']}的主要產品是{row['product']}；"
                f"該產品可能受到{row['risk']}影響。"
            ),
        ]
        documents.append(
            {
                "source_id": source_id,
                "co_code": row["co_code"],
                "title": f"{row['company_name']} 2026 Q2 法說會（合成）",
                "period": "2026Q2",
                "captured_at": now,
                "content_hash": sha256("\n".join(texts)),
            }
        )
        for sequence, text in enumerate(texts, start=1):
            chunks.append(
                {
                    "chunk_id": f"{source_id}-p{sequence}",
                    "source_id": source_id,
                    "co_code": row["co_code"],
                    "title": f"{row['company_name']} 2026 Q2 法說會（合成）",
                    "period": "2026Q2",
                    "text": text,
                    "sequence": sequence,
                    "paragraph_id": f"p-{sequence}",
                    "embedding": vectors[vector_index],
                    "captured_at": now,
                    "content_hash": sha256(text),
                }
            )
            vector_index += 1
    return documents, chunks


def seed_neo4j(rows: list[dict]) -> int:
    settings = get_settings()
    texts = [
        text
        for row in rows
        for text in (
            (
                f"{row['company_name']}財務長表示，2026 Q2 主要風險為"
                f"{row['risk']}，目前尚未調整全年展望。"
            ),
            (
                f"{row['company_name']}的主要產品是{row['product']}；"
                f"該產品可能受到{row['risk']}影響。"
            ),
        )
    ]
    vectors: list[list[float]] = []
    for offset in range(0, len(texts), 32):
        vectors.extend(
            embed(
                texts[offset : offset + 32],
                settings.ollama_url,
                settings.ollama_embedding_model,
            )
        )
    dimensions = len(vectors[0])
    documents, chunks = build_documents(rows, vectors)
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
        driver.execute_query(
            """
            UNWIND $rows AS item
            MERGE (company:Company {co_code: item.co_code})
              SET company.name = item.company_name,
                  company.industry = item.industry
            """,
            rows=rows,
            database_=settings.neo4j_database,
        )
        driver.execute_query(
            """
            UNWIND $documents AS item
            MATCH (company:Company {co_code: item.co_code})
            MERGE (document:Document {source_id: item.source_id})
              SET document.co_code = item.co_code,
                  document.source_type = 'transcript',
                  document.title = item.title,
                  document.period = item.period,
                  document.captured_at = item.captured_at,
                  document.content_hash = item.content_hash,
                  document.data_version = 'scale-v1'
            MERGE (company)-[:HAS_DOCUMENT]->(document)
            """,
            documents=documents,
            database_=settings.neo4j_database,
        )
        driver.execute_query(
            """
            UNWIND $chunks AS item
            MATCH (document:Document {source_id: item.source_id})
            MERGE (chunk:Chunk {chunk_id: item.chunk_id})
              SET chunk.co_code = item.co_code,
                  chunk.source_id = item.source_id,
                  chunk.source_type = 'transcript',
                  chunk.title = item.title,
                  chunk.period = item.period,
                  chunk.text = item.text,
                  chunk.sequence = item.sequence,
                  chunk.paragraph_id = item.paragraph_id,
                  chunk.embedding = item.embedding,
                  chunk.captured_at = item.captured_at,
                  chunk.content_hash = item.content_hash,
                  chunk.data_version = 'scale-v1'
            MERGE (document)-[:HAS_CHUNK]->(chunk)
            """,
            chunks=chunks,
            database_=settings.neo4j_database,
        )
        driver.execute_query(
            """
            UNWIND $rows AS item
            MATCH (company:Company {co_code: item.co_code})
            MATCH (chunk:Chunk {
                chunk_id: 'scale-' + toLower(item.co_code) + '-2026q2-call-p1'
            })
            MERGE (product:Product {name: item.product, co_code: item.co_code})
            MERGE (risk:Risk {name: item.risk, co_code: item.co_code})
            MERGE (company)-[s:SELLS]->(product)
              SET s.co_code = item.co_code,
                  s.source_id = chunk.source_id,
                  s.period = '2026Q2', s.data_version = 'scale-v1',
                  s.provenance_text = chunk.text
            MERGE (product)-[e:EXPOSED_TO]->(risk)
              SET e.co_code = item.co_code,
                  e.source_id = chunk.source_id,
                  e.period = '2026Q2', e.data_version = 'scale-v1',
                  e.provenance_text = chunk.text
            MERGE (chunk)-[m:MENTIONS]->(risk)
              SET m.co_code = item.co_code,
                  m.source_id = chunk.source_id,
                  m.period = '2026Q2', m.data_version = 'scale-v1',
                  m.provenance_text = chunk.text
            """,
            rows=rows,
            database_=settings.neo4j_database,
        )
    finally:
        driver.close()
    return dimensions


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic scale-test data.")
    parser.add_argument("--companies", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.companies <= 500:
        parser.error("--companies must be between 1 and 500")
    rows = build_rows(args.companies)
    seed_sqlite(rows)
    dimensions = seed_neo4j(rows)
    print(
        json.dumps(
            {
                "status": "ok",
                "companies": len(rows),
                "financial_metrics": len(rows) * 2,
                "documents": len(rows),
                "chunks": len(rows) * 2,
                "embedding_dimensions": dimensions,
                "data_version": DATA_VERSION,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
