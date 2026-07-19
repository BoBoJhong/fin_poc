from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import sqlite3
from collections.abc import Iterable
from typing import Any, Protocol

from app.config import Settings
from app.models import CompanySummary, Evidence, SourceLocator, SourcePreview, SourceType
from app.sample_data import COMPANIES, EVIDENCE, SOURCE_PREVIEWS, company_name


logger = logging.getLogger(__name__)


class KnowledgeRepository(Protocol):
    async def search_documents(
        self, query: str, co_code: str, top_k: int = 5
    ) -> list[Evidence]: ...

    async def search_graph(
        self, query: str, co_code: str, max_hops: int = 2
    ) -> list[Evidence]: ...

    async def get_source_preview(
        self, source_id: str, co_code: str
    ) -> SourcePreview | None: ...


class FinanceRepository(Protocol):
    async def list_companies(self) -> list[CompanySummary]: ...

    async def get_metrics(
        self, co_code: str, period: str | None = None
    ) -> list[Evidence]: ...

    async def get_source_preview(
        self, source_id: str, co_code: str
    ) -> SourcePreview | None: ...


class MockKnowledgeRepository:
    async def search_documents(
        self, query: str, co_code: str, top_k: int = 5
    ) -> list[Evidence]:
        del query
        return [
            item
            for item in EVIDENCE
            if item.co_code == co_code
            and item.source_type in {SourceType.TRANSCRIPT, SourceType.FINANCIAL_REPORT, SourceType.URL}
        ][:top_k]

    async def search_graph(
        self, query: str, co_code: str, max_hops: int = 2
    ) -> list[Evidence]:
        del query
        return [
            item
            for item in EVIDENCE
            if item.co_code == co_code
            and item.source_type == SourceType.GRAPH
            and int(item.metadata.get("hops", 1)) <= max_hops
        ]

    async def get_source_preview(
        self, source_id: str, co_code: str
    ) -> SourcePreview | None:
        preview = SOURCE_PREVIEWS.get(source_id)
        if preview and preview.co_code == co_code:
            return preview
        return None


class MockFinanceRepository:
    async def list_companies(self) -> list[CompanySummary]:
        return [
            CompanySummary(
                co_code=code,
                company_name=item["name"],
                industry=item["industry"],
                aliases=item.get("aliases", []),
            )
            for code, item in COMPANIES.items()
        ]

    async def get_metrics(
        self, co_code: str, period: str | None = None
    ) -> list[Evidence]:
        return [
            item
            for item in EVIDENCE
            if item.co_code == co_code
            and item.source_type == SourceType.DATABASE
            and (period is None or item.period == period)
        ]

    async def get_source_preview(
        self, source_id: str, co_code: str
    ) -> SourcePreview | None:
        preview = SOURCE_PREVIEWS.get(source_id)
        if preview and preview.co_code == co_code and preview.source_type == SourceType.DATABASE:
            return preview
        return None


class SQLiteFinanceRepository:
    """Read-only local SQLite queries. No model-generated SQL is accepted."""

    def __init__(self, settings: Settings):
        self.database_path = settings.sqlite_database_path
        self.read_only = settings.sqlite_read_only

    def _connect(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise RuntimeError(
                f"找不到 SQLite：{self.database_path}。請設定 SQLITE_PATH，"
                "或先執行 python -m scripts.init_sqlite。"
            )
        if self.read_only:
            connection = sqlite3.connect(
                f"file:{self.database_path.as_posix()}?mode=ro", uri=True
            )
        else:
            connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    async def list_companies(self) -> list[CompanySummary]:
        def run() -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT co_code, company_name, industry FROM companies ORDER BY co_code"
                ).fetchall()
                alias_table = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'company_aliases'"
                ).fetchone()
                aliases: dict[str, list[str]] = {}
                if alias_table:
                    alias_rows = connection.execute(
                        "SELECT co_code, alias FROM company_aliases ORDER BY co_code, alias"
                    ).fetchall()
                    for alias_row in alias_rows:
                        aliases.setdefault(alias_row["co_code"], []).append(alias_row["alias"])
                return [dict(row) for row in rows], aliases

        rows, aliases = await asyncio.to_thread(run)
        return [
            CompanySummary.model_validate(
                {**row, "aliases": aliases.get(row["co_code"], [])}
            )
            for row in rows
        ]

    async def get_metrics(
        self, co_code: str, period: str | None = None
    ) -> list[Evidence]:
        sql = """
            SELECT co_code, period, metric_code, value, unit, scope,
                   source_id, data_version, updated_at
            FROM financial_metrics
            WHERE co_code = :co_code
              AND (:period IS NULL OR period = :period)
            ORDER BY period DESC, metric_code
            LIMIT 100
            """

        def run() -> list[dict[str, Any]]:
            with self._connect() as connection:
                rows = connection.execute(
                    sql, {"co_code": co_code, "period": period}
                ).fetchall()
                return [dict(row) for row in rows]

        rows = await asyncio.to_thread(run)
        return [self._row_to_evidence(row) for row in rows]

    async def get_source_preview(
        self, source_id: str, co_code: str
    ) -> SourcePreview | None:
        sql = """
            SELECT ds.source_id, ds.co_code, ds.source_type, ds.title,
                   ds.captured_at, ds.content_hash, ds.data_version,
                   fm.period, fm.metric_code, fm.value, fm.unit, fm.scope
            FROM data_sources ds
            LEFT JOIN financial_metrics fm
              ON fm.source_id = ds.source_id AND fm.co_code = ds.co_code
            WHERE ds.source_id = :source_id AND ds.co_code = :co_code
            ORDER BY fm.period, fm.metric_code
            """

        def run() -> list[dict[str, Any]]:
            with self._connect() as connection:
                rows = connection.execute(
                    sql, {"source_id": source_id, "co_code": co_code}
                ).fetchall()
                return [dict(row) for row in rows]

        rows = await asyncio.to_thread(run)
        if not rows:
            return None
        first = rows[0]
        records = [
            {
                "co_code": row["co_code"],
                "period": row["period"],
                "metric_code": row["metric_code"],
                "value": float(row["value"]) if row["value"] is not None else None,
                "unit": row["unit"],
                "scope": row["scope"],
            }
            for row in rows
            if row["metric_code"] is not None
        ]
        return SourcePreview(
            source_id=first["source_id"],
            co_code=first["co_code"],
            source_type=SourceType.DATABASE,
            title=first["title"],
            captured_at=str(first["captured_at"] or "") or None,
            content_hash=first["content_hash"],
            database_record={
                "table": "financial_metrics",
                "records": records,
                "data_version": first["data_version"],
            },
        )

    @staticmethod
    def _row_to_evidence(row: dict[str, Any]) -> Evidence:
        primary_key = f"{row['co_code']}|{row['period']}|{row['metric_code']}"
        return Evidence(
            evidence_id=f"ev-db-{primary_key}",
            co_code=row["co_code"],
            source_id=row["source_id"],
            source_type=SourceType.DATABASE,
            title=f"{row['co_code']} {row['period']} 結構化財務指標",
            content=(
                f"{row['period']} {row['metric_code']} = {row['value']} {row['unit']} "
                f"({row['scope']})"
            ),
            score=1.0,
            period=row["period"],
            locator=SourceLocator(
                table="financial_metrics",
                primary_key=primary_key,
                columns=["co_code", "period", "metric_code", "value", "unit", "scope"],
            ),
            data_version=row["data_version"],
            metadata={
                "metric_code": row["metric_code"],
                "value": float(row["value"]),
                "unit": row["unit"],
                "scope": row["scope"],
                "updated_at": str(row["updated_at"]),
            },
        )


class Neo4jKnowledgeRepository:
    """Neo4j GraphRAG adapter with mandatory server-side co_code scoping."""

    def __init__(self, settings: Settings):
        from neo4j import GraphDatabase
        from neo4j_graphrag.embeddings import OllamaEmbeddings
        from neo4j_graphrag.retrievers import VectorRetriever

        self.settings = settings
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        self.embedder = OllamaEmbeddings(
            model=settings.ollama_embedding_model,
            host=settings.ollama_url,
        )
        self.vector_retriever = VectorRetriever(
            self.driver,
            settings.neo4j_vector_index,
            self.embedder,
            return_properties=[
                "chunk_id",
                "co_code",
                "source_id",
                "source_type",
                "title",
                "text",
                "period",
                "paragraph_id",
                "page",
                "captured_at",
                "content_hash",
                "data_version",
            ],
            neo4j_database=settings.neo4j_database,
        )

    async def search_documents(
        self, query: str, co_code: str, top_k: int = 5
    ) -> list[Evidence]:
        candidate_k = max(top_k * 4, 20)
        result = await asyncio.to_thread(
            self.vector_retriever.search,
            query_text=query,
            top_k=candidate_k,
            filters={"co_code": {"$eq": co_code}},
        )
        candidates = [self._vector_item_to_evidence(item) for item in result.items]
        lexical_ranks = await asyncio.to_thread(
            self._fulltext_ranks, query, co_code, candidate_k
        )
        vector_weight = min(max(self.settings.hybrid_vector_weight, 0.5), 1.0)
        for item in candidates:
            lexical_rank = lexical_ranks.get(item.evidence_id)
            lexical_score = 1.0 / lexical_rank if lexical_rank else 0.0
            vector_score = item.score
            item.score = min(
                1.0,
                vector_score + (1.0 - vector_score) * (1.0 - vector_weight) * lexical_score,
            )
            item.metadata.update(
                {
                    "retriever": "scoped_vector_fulltext_hybrid",
                    "vector_score": vector_score,
                    "fulltext_rank": lexical_rank,
                    "hybrid_score": item.score,
                }
            )
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:top_k]

    def _fulltext_ranks(self, query: str, co_code: str, limit: int) -> dict[str, int]:
        """Return lexical ranks only; vector retrieval remains the scoped candidate gate."""
        terms = re.findall(r"[0-9A-Za-z\u3400-\u9fff]+", query)
        if not terms:
            return {}
        lucene_query = " ".join(terms)
        cypher = """
        CALL db.index.fulltext.queryNodes(
            $index_name, $query, {limit: $candidate_limit}
        ) YIELD node, score
        WHERE node.co_code = $co_code
        RETURN node.chunk_id AS chunk_id, score
        ORDER BY score DESC
        LIMIT $limit
        """
        try:
            records, _, _ = self.driver.execute_query(
                cypher,
                index_name=self.settings.neo4j_fulltext_index,
                query=lucene_query,
                candidate_limit=max(limit * 20, 200),
                co_code=co_code,
                limit=limit,
                database_=self.settings.neo4j_database,
            )
        except Exception as exc:  # Full-text is a safe ranking enhancement, not a hard dependency.
            logger.warning("Full-text retrieval unavailable; using scoped vector results: %s", exc)
            return {}
        return {
            f"ev-neo4j-{record['chunk_id']}": rank
            for rank, record in enumerate(records, start=1)
            if record.get("chunk_id")
        }

    @staticmethod
    def _content_as_dict(content: Any) -> dict[str, Any]:
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                return parsed if isinstance(parsed, dict) else {"text": content}
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(content)
                    return parsed if isinstance(parsed, dict) else {"text": content}
                except (ValueError, SyntaxError):
                    return {"text": content}
        return {"text": str(content)}

    def _vector_item_to_evidence(self, item: Any) -> Evidence:
        data = self._content_as_dict(getattr(item, "content", item))
        metadata = getattr(item, "metadata", {}) or {}
        raw_score = metadata.get("score", data.get("score", 0.0))
        score = float(raw_score) if raw_score is not None else 0.0
        return Evidence(
            evidence_id=f"ev-neo4j-{data.get('chunk_id', data.get('source_id', 'unknown'))}",
            co_code=data["co_code"],
            source_id=data["source_id"],
            source_type=SourceType(data.get("source_type", "financial_report")),
            title=data.get("title", data["source_id"]),
            content=data.get("text", ""),
            score=max(0.0, min(score, 1.0)),
            period=data.get("period"),
            locator=SourceLocator(
                page=data.get("page"), paragraph_id=data.get("paragraph_id")
            ),
            captured_at=data.get("captured_at"),
            content_hash=data.get("content_hash"),
            data_version=data.get("data_version", "unknown"),
            metadata={"retriever": "neo4j_vector", **metadata},
        )

    async def search_graph(
        self, query: str, co_code: str, max_hops: int = 2
    ) -> list[Evidence]:
        hops = min(max(max_hops, 1), 2)
        seed_result = await asyncio.to_thread(
            self.vector_retriever.search,
            query_text=query,
            top_k=5,
            filters={"co_code": {"$eq": co_code}},
        )
        seed_data = [
            self._content_as_dict(getattr(item, "content", item))
            for item in seed_result.items
        ]
        chunk_ids = [item.get("chunk_id") for item in seed_data if item.get("chunk_id")]
        if not chunk_ids:
            return []

        cypher = f"""
        MATCH (chunk:Chunk)-[mention:MENTIONS]->(anchor)
        WHERE chunk.co_code = $co_code
          AND chunk.chunk_id IN $chunk_ids
          AND anchor.co_code = $co_code
          AND mention.co_code = $co_code
          AND mention.source_id IS NOT NULL
          AND mention.period IS NOT NULL
          AND mention.data_version IS NOT NULL
        MATCH path=(anchor)-[rels*1..{hops}]-(node)
        WHERE ALL(n IN nodes(path) WHERE n.co_code = $co_code)
          AND ALL(r IN rels WHERE type(r) IN
              ['SELLS', 'EXPOSED_TO', 'AFFECTS']
              AND r.co_code = $co_code
              AND r.source_id IS NOT NULL
              AND r.period IS NOT NULL
              AND r.data_version IS NOT NULL)
        WITH chunk, path, node, rels, mention
        LIMIT 10
        RETURN chunk.chunk_id AS chunk_id,
               [n IN nodes(path) | coalesce(n.name, n.title, n.co_code)] AS nodes,
               [r IN rels | type(r)] AS relationships,
               chunk.source_id AS source_id,
               chunk.period AS period,
               chunk.text AS provenance_text,
               chunk.paragraph_id AS paragraph_id,
               chunk.captured_at AS captured_at,
               chunk.content_hash AS content_hash,
               chunk.data_version AS data_version,
               [r IN [mention] + rels | {{
                   type: type(r),
                   co_code: r.co_code,
                   source_id: r.source_id,
                   period: r.period,
                   data_version: r.data_version,
                   provenance_text: r.provenance_text
               }}] AS relationship_provenance
        """

        def run() -> list[dict[str, Any]]:
            records, _, _ = self.driver.execute_query(
                cypher,
                co_code=co_code,
                chunk_ids=chunk_ids,
                database_=self.settings.neo4j_database,
            )
            return [record.data() for record in records]

        rows = await asyncio.to_thread(run)
        evidence: list[Evidence] = []
        for index, row in enumerate(rows):
            path: list[str] = [f"Chunk:{row['chunk_id']}", "MENTIONS"]
            nodes = row.get("nodes", [])
            relationships = row.get("relationships", [])
            for offset, node in enumerate(nodes):
                path.append(str(node))
                if offset < len(relationships):
                    path.append(str(relationships[offset]))
            source_id = row.get("source_id") or f"graph-{co_code}"
            evidence.append(
                Evidence(
                    evidence_id=f"ev-graph-{co_code}-{index}",
                    co_code=co_code,
                    source_id=source_id,
                    source_type=SourceType.GRAPH,
                    title=f"{company_name(co_code)} 關聯圖譜",
                    content=(
                        f"來源段落：{row.get('provenance_text', '')}；"
                        f"圖譜路徑：{' → '.join(path)}"
                    ),
                    score=0.8,
                    period=row.get("period"),
                    locator=SourceLocator(
                        paragraph_id=row.get("paragraph_id"), graph_path=path
                    ),
                    captured_at=str(row.get("captured_at") or "") or None,
                    content_hash=row.get("content_hash"),
                    data_version=row.get("data_version") or "unknown",
                    metadata={
                        "hops": len(relationships),
                        "retriever": "vector_seeded_bounded_cypher",
                        "relationship_provenance": row.get(
                            "relationship_provenance", []
                        ),
                    },
                )
            )
        return evidence

    async def get_source_preview(
        self, source_id: str, co_code: str
    ) -> SourcePreview | None:
        cypher = """
        MATCH (document:Document {source_id: $source_id, co_code: $co_code})
        OPTIONAL MATCH (document)-[:HAS_CHUNK]->(chunk:Chunk)
        WITH document, chunk ORDER BY chunk.sequence
        RETURN document.source_id AS source_id,
               document.co_code AS co_code,
               document.source_type AS source_type,
               document.title AS title,
               document.live_url AS live_url,
               document.snapshot_html AS snapshot_html,
               document.captured_at AS captured_at,
               document.content_hash AS content_hash,
               collect(chunk.text) AS chunks
        LIMIT 1
        """

        def run() -> dict[str, Any] | None:
            records, _, _ = self.driver.execute_query(
                cypher,
                source_id=source_id,
                co_code=co_code,
                database_=self.settings.neo4j_database,
            )
            return records[0].data() if records else None

        row = await asyncio.to_thread(run)
        if not row:
            return None
        return SourcePreview(
            source_id=row["source_id"],
            co_code=row["co_code"],
            source_type=SourceType(row["source_type"]),
            title=row["title"],
            snapshot_html=row.get("snapshot_html"),
            live_url=row.get("live_url"),
            text="\n\n".join(row.get("chunks") or []),
            captured_at=str(row.get("captured_at") or "") or None,
            content_hash=row.get("content_hash"),
        )

    async def close(self) -> None:
        await asyncio.to_thread(self.driver.close)


def build_knowledge_repository(settings: Settings) -> KnowledgeRepository:
    if settings.data_mode == "mock":
        return MockKnowledgeRepository()
    return Neo4jKnowledgeRepository(settings)


def build_finance_repository(settings: Settings) -> FinanceRepository:
    if settings.data_mode == "mock":
        return MockFinanceRepository()
    return SQLiteFinanceRepository(settings)


def dump_evidence(items: Iterable[Evidence]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]
