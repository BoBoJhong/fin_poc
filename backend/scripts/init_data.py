from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import httpx
from neo4j import GraphDatabase
from neo4j_graphrag.indexes import create_fulltext_index, create_vector_index

from app.config import get_settings


SAMPLE_COMPANIES = [
    {
        "co_code": "DEMO01",
        "company_name": "範例科技股份有限公司",
        "industry": "企業軟體",
    },
    {
        "co_code": "DEMO02",
        "company_name": "示範製造股份有限公司",
        "industry": "智慧製造",
    },
]


SAMPLE_DOCUMENTS = [
    {
        "source_id": "demo01-2026q2-call",
        "co_code": "DEMO01",
        "source_type": "transcript",
        "title": "範例科技 2026 Q2 法說會逐字稿（虛構）",
        "period": "2026Q2",
        "live_url": None,
        "snapshot_html": None,
        "chunks": [
            {
                "chunk_id": "demo01-call-p18",
                "sequence": 18,
                "paragraph_id": "p-18",
                "text": (
                    "財務長表示，下半年主要不確定性包括海外專案驗收遞延、"
                    "匯率波動，以及雲端基礎設施成本上升；公司尚未因此調整全年展望。"
                ),
            }
        ],
    },
    {
        "source_id": "demo01-2026q2-report",
        "co_code": "DEMO01",
        "source_type": "url",
        "title": "範例科技 2026 Q2 投資人報告頁面快照（虛構）",
        "period": "2026Q2",
        "live_url": "https://example.com/investor/demo01/2026q2",
        "snapshot_html": (
            "<!doctype html><html lang='zh-Hant'><body><h2>範例科技 2026 Q2 投資人報告"
            "</h2><p>此為 PoC 虛構快照。</p><h3>主要風險</h3>"
            "<p>海外專案驗收遞延、匯率波動與雲端成本。</p></body></html>"
        ),
        "chunks": [
            {
                "chunk_id": "demo01-report-risk",
                "sequence": 1,
                "paragraph_id": "risk",
                "text": "主要風險包括海外專案驗收遞延、匯率波動與雲端基礎設施成本上升。",
            }
        ],
    },
]


def sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed(texts: list[str], base_url: str, model: str) -> list[list[float]]:
    response = httpx.post(
        base_url.rstrip("/") + "/api/embed",
        json={"model": model, "input": texts},
        timeout=120,
    )
    response.raise_for_status()
    vectors = response.json().get("embeddings")
    if not vectors or len(vectors) != len(texts):
        raise RuntimeError("Ollama /api/embed 未回傳預期數量的 embeddings")
    return vectors


def create_indexes(driver, dimensions: int, database: str, vector_index: str, fulltext_index: str):
    driver.execute_query(
        "CREATE CONSTRAINT company_code IF NOT EXISTS FOR (n:Company) REQUIRE n.co_code IS UNIQUE",
        database_=database,
    )
    driver.execute_query(
        "CREATE CONSTRAINT document_source IF NOT EXISTS "
        "FOR (n:Document) REQUIRE n.source_id IS UNIQUE",
        database_=database,
    )
    driver.execute_query(
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.chunk_id IS UNIQUE",
        database_=database,
    )
    driver.execute_query(
        "CREATE CONSTRAINT speaker_id IF NOT EXISTS FOR (n:Speaker) REQUIRE n.speaker_id IS UNIQUE",
        database_=database,
    )
    driver.execute_query(
        "CREATE CONSTRAINT speaker_turn_id IF NOT EXISTS "
        "FOR (n:SpeakerTurn) REQUIRE n.turn_id IS UNIQUE",
        database_=database,
    )
    driver.execute_query(
        "CREATE INDEX earnings_call_scope IF NOT EXISTS "
        "FOR (n:EarningsCall) ON (n.co_code, n.event_date)",
        database_=database,
    )
    driver.execute_query(
        "CREATE INDEX chunk_company_scope IF NOT EXISTS FOR (n:Chunk) ON (n.co_code)",
        database_=database,
    )
    create_vector_index(
        driver,
        vector_index,
        label="Chunk",
        embedding_property="embedding",
        dimensions=dimensions,
        similarity_fn="cosine",
        fail_if_exists=False,
        neo4j_database=database,
        filterable_properties=["co_code", "period", "source_type"],
    )
    create_fulltext_index(
        driver,
        fulltext_index,
        label="Chunk",
        node_properties=["text", "title"],
        fail_if_exists=False,
        neo4j_database=database,
    )


def upsert_companies(driver, companies: list[dict], database: str) -> None:
    driver.execute_query(
        """
        UNWIND $companies AS item
        MERGE (company:Company {co_code: item.co_code})
          SET company.name = item.company_name,
              company.industry = item.industry
        """,
        companies=companies,
        database_=database,
    )


def upsert_documents(driver, documents: list[dict], vectors: list[list[float]], database: str):
    captured_at = datetime.now(UTC).isoformat()
    vector_offset = 0
    for document in documents:
        doc_hash = sha256(
            "\n".join(chunk["text"] for chunk in document["chunks"])
            + (document.get("snapshot_html") or "")
        )
        driver.execute_query(
            """
            MERGE (company:Company {co_code: $co_code})
              SET company.name = $company_name,
                  company.industry = '企業軟體'
            MERGE (document:Document {source_id: $source_id})
              SET document.co_code = $co_code,
                  document.source_type = $source_type,
                  document.title = $title,
                  document.period = $period,
                  document.live_url = $live_url,
                  document.snapshot_html = $snapshot_html,
                  document.captured_at = $captured_at,
                  document.content_hash = $content_hash,
                  document.data_version = 'demo-v1'
            MERGE (company)-[:HAS_DOCUMENT]->(document)
            """,
            co_code=document["co_code"],
            company_name="範例科技股份有限公司",
            source_id=document["source_id"],
            source_type=document["source_type"],
            title=document["title"],
            period=document["period"],
            live_url=document.get("live_url"),
            snapshot_html=document.get("snapshot_html"),
            captured_at=captured_at,
            content_hash=doc_hash,
            database_=database,
        )
        for chunk in document["chunks"]:
            vector = vectors[vector_offset]
            vector_offset += 1
            driver.execute_query(
                """
                MATCH (document:Document {source_id: $source_id})
                MERGE (chunk:Chunk {chunk_id: $chunk_id})
                  SET chunk.co_code = $co_code,
                      chunk.source_id = $source_id,
                      chunk.source_type = $source_type,
                      chunk.title = $title,
                      chunk.period = $period,
                      chunk.text = $text,
                      chunk.sequence = $sequence,
                      chunk.paragraph_id = $paragraph_id,
                      chunk.embedding = $embedding,
                      chunk.captured_at = $captured_at,
                      chunk.content_hash = $content_hash,
                      chunk.data_version = 'demo-v1'
                MERGE (document)-[:HAS_CHUNK]->(chunk)
                """,
                source_id=document["source_id"],
                co_code=document["co_code"],
                source_type=document["source_type"],
                title=document["title"],
                period=document["period"],
                chunk_id=chunk["chunk_id"],
                text=chunk["text"],
                sequence=chunk["sequence"],
                paragraph_id=chunk["paragraph_id"],
                embedding=vector,
                captured_at=captured_at,
                content_hash=sha256(chunk["text"]),
                database_=database,
            )


def upsert_graph_relations(driver, database: str):
    driver.execute_query(
        """
        MATCH (company:Company {co_code: 'DEMO01'})
        MERGE (product:Product {name: 'Atlas ERP', co_code: 'DEMO01'})
        MERGE (risk:Risk {name: '海外專案驗收遞延', co_code: 'DEMO01'})
        MERGE (company)-[s:SELLS]->(product)
          SET s.co_code = 'DEMO01',
              s.source_id = 'demo01-2026q2-call',
              s.period = '2026Q2',
              s.data_version = 'demo-v1',
              s.provenance_text =
                '範例科技銷售 Atlas ERP；來源為 2026 Q2 法說會第 18 段。'
        MERGE (product)-[e:EXPOSED_TO]->(risk)
          SET e.co_code = 'DEMO01',
              e.source_id = 'demo01-2026q2-call',
              e.period = '2026Q2',
              e.data_version = 'demo-v1',
              e.provenance_text =
                'Atlas ERP 海外專案可能受到客戶驗收時程遞延影響；來源為法說會第 18 段。'
        WITH risk
        MATCH (chunk:Chunk {chunk_id: 'demo01-call-p18'})
        MERGE (chunk)-[m:MENTIONS]->(risk)
          SET m.co_code = 'DEMO01',
              m.source_id = 'demo01-2026q2-call',
              m.period = '2026Q2',
              m.data_version = 'demo-v1',
              m.provenance_text =
                '法說會第 18 段提及海外專案驗收遞延風險。'
        """,
        database_=database,
    )


def main() -> None:
    settings = get_settings()
    chunks = [chunk for document in SAMPLE_DOCUMENTS for chunk in document["chunks"]]
    vectors = embed(
        [chunk["text"] for chunk in chunks],
        settings.ollama_url,
        settings.ollama_embedding_model,
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
        upsert_companies(driver, SAMPLE_COMPANIES, settings.neo4j_database)
        upsert_documents(driver, SAMPLE_DOCUMENTS, vectors, settings.neo4j_database)
        upsert_graph_relations(driver, settings.neo4j_database)
    finally:
        driver.close()
    print(
        json.dumps(
            {
                "status": "ok",
                "documents": len(SAMPLE_DOCUMENTS),
                "chunks": len(chunks),
                "embedding_dimensions": dimensions,
                "model": settings.ollama_embedding_model,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
