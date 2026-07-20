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
from app.database_connectors import CompositeFinanceRepository, build_external_repositories
from app.external_api_connectors import build_external_api_repositories
from app.models import (
    CompanySummary,
    Evidence,
    FiscalCalendar,
    SourceLocator,
    SourcePreview,
    SourceType,
)
from app.sample_data import COMPANIES, EVIDENCE, SOURCE_PREVIEWS, company_name


logger = logging.getLogger(__name__)


class KnowledgeRepository(Protocol):
    async def search_documents(
        self,
        query: str,
        co_code: str,
        top_k: int = 5,
        period: str | None = None,
        source_types: tuple[str, ...] | None = None,
    ) -> list[Evidence]: ...

    async def search_graph(
        self, query: str, co_code: str, max_hops: int = 2, period: str | None = None
    ) -> list[Evidence]: ...

    async def get_source_preview(self, source_id: str, co_code: str) -> SourcePreview | None: ...

    async def list_periods(
        self, co_code: str, source_types: tuple[str, ...] | None = None
    ) -> list[str]: ...


class FinanceRepository(Protocol):
    async def list_companies(self) -> list[CompanySummary]: ...

    async def get_metrics(self, co_code: str, period: str | None = None) -> list[Evidence]: ...

    async def get_source_preview(self, source_id: str, co_code: str) -> SourcePreview | None: ...

    async def list_periods(self, co_code: str) -> list[str]: ...

    async def get_fiscal_calendar(self, co_code: str) -> FiscalCalendar | None: ...


class MockKnowledgeRepository:
    async def search_documents(
        self,
        query: str,
        co_code: str,
        top_k: int = 5,
        period: str | None = None,
        source_types: tuple[str, ...] | None = None,
    ) -> list[Evidence]:
        del query
        allowed_types = set(source_types) if source_types else None
        return [
            item
            for item in EVIDENCE
            if item.co_code == co_code
            and item.source_type
            in {SourceType.TRANSCRIPT, SourceType.FINANCIAL_REPORT, SourceType.URL}
            and (period is None or item.period == period)
            and (allowed_types is None or str(item.source_type) in allowed_types)
        ][:top_k]

    async def search_graph(
        self, query: str, co_code: str, max_hops: int = 2, period: str | None = None
    ) -> list[Evidence]:
        del query
        return [
            item
            for item in EVIDENCE
            if item.co_code == co_code
            and item.source_type == SourceType.GRAPH
            and int(item.metadata.get("hops", 1)) <= max_hops
            and (period is None or item.period == period)
        ]

    async def get_source_preview(self, source_id: str, co_code: str) -> SourcePreview | None:
        preview = SOURCE_PREVIEWS.get(source_id)
        if preview and preview.co_code == co_code:
            return preview
        return None

    async def list_periods(
        self, co_code: str, source_types: tuple[str, ...] | None = None
    ) -> list[str]:
        allowed = set(source_types) if source_types else None
        return sorted(
            {
                item.period
                for item in EVIDENCE
                if item.co_code == co_code
                and item.period
                and (allowed is None or str(item.source_type) in allowed)
            }
        )


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

    async def get_metrics(self, co_code: str, period: str | None = None) -> list[Evidence]:
        return [
            item
            for item in EVIDENCE
            if item.co_code == co_code
            and item.source_type == SourceType.DATABASE
            and (period is None or item.period == period)
        ]

    async def get_source_preview(self, source_id: str, co_code: str) -> SourcePreview | None:
        preview = SOURCE_PREVIEWS.get(source_id)
        if preview and preview.co_code == co_code and preview.source_type == SourceType.DATABASE:
            return preview
        return None

    async def list_periods(self, co_code: str) -> list[str]:
        return sorted(
            {
                item.period
                for item in EVIDENCE
                if item.co_code == co_code
                and item.period
                and item.source_type == SourceType.DATABASE
            }
        )

    async def get_fiscal_calendar(self, co_code: str) -> FiscalCalendar | None:
        if co_code not in COMPANIES:
            return None
        return FiscalCalendar(co_code=co_code, fiscal_year_end_month=12)


class SQLiteFinanceRepository:
    """Read-only local SQLite queries. No model-generated SQL is accepted."""

    def __init__(self, settings: Settings):
        self.database_path = settings.sqlite_database_path
        self.read_only = settings.sqlite_read_only
        self.fact_query_limit = settings.financial_fact_query_limit

    def _connect(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise RuntimeError(
                f"找不到 SQLite：{self.database_path}。請設定 SQLITE_PATH，"
                "或先執行 python -m scripts.init_sqlite。"
            )
        if self.read_only:
            connection = sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)
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
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'company_aliases'"
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
            CompanySummary.model_validate({**row, "aliases": aliases.get(row["co_code"], [])})
            for row in rows
        ]

    async def get_metrics(self, co_code: str, period: str | None = None) -> list[Evidence]:
        legacy_sql = """
            SELECT co_code, period, metric_code, value, unit, scope,
                   source_id, data_version, updated_at
            FROM financial_metrics
            WHERE co_code = :co_code
              AND (:period IS NULL OR period = :period)
            ORDER BY period DESC, metric_code
            LIMIT :limit
            """

        facts_sql = """
            SELECT ff.fact_id, ff.co_code, ff.fiscal_year, ff.fiscal_quarter, ff.period,
                   ff.period_start, ff.period_end, ff.metric_code, ff.provider_id,
                   ff.provider_metric_key, ff.value_exact, ff.unit, ff.scale,
                   ff.statement_type, ff.duration_type, ff.consolidation_scope,
                   ff.dimensions_json, ff.source_id, ff.raw_payload_id, ff.data_version,
                   ff.captured_at, ff.content_hash,
                   md.display_name AS metric_display_name,
                   md.aliases_json AS metric_aliases_json
            FROM financial_facts ff
            JOIN financial_metric_definitions md ON md.metric_code = ff.metric_code
            WHERE ff.co_code = :co_code AND ff.is_current = 1 AND md.approved = 1
              AND (:period IS NULL OR ff.period = :period)
            ORDER BY ff.period DESC, ff.statement_type, ff.metric_code
            LIMIT :limit
            """

        def run() -> tuple[str, list[dict[str, Any]]]:
            with self._connect() as connection:
                has_v2 = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='financial_facts'"
                ).fetchone()
                parameters = {
                    "co_code": co_code,
                    "period": period,
                    "limit": self.fact_query_limit,
                }
                if has_v2:
                    rows = connection.execute(facts_sql, parameters).fetchall()
                    if rows:
                        return "v2", [dict(row) for row in rows]
                rows = connection.execute(legacy_sql, parameters).fetchall()
                return "legacy", [dict(row) for row in rows]

        schema, rows = await asyncio.to_thread(run)
        converter = self._fact_to_evidence if schema == "v2" else self._row_to_evidence
        return [converter(row) for row in rows]

    async def get_source_preview(self, source_id: str, co_code: str) -> SourcePreview | None:
        def run() -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
            with self._connect() as connection:
                source = connection.execute(
                    """
                    SELECT source_id, co_code, source_type, title, captured_at,
                           content_hash, data_version, source_url, raw_locator
                    FROM data_sources
                    WHERE source_id = :source_id AND co_code = :co_code
                    """,
                    {"source_id": source_id, "co_code": co_code},
                ).fetchone()
                if source is None:
                    return None, "none", []
                has_v2 = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='financial_facts'"
                ).fetchone()
                if has_v2:
                    facts = connection.execute(
                        """
                        SELECT fact_id, period, metric_code, provider_id,
                               provider_metric_key, value_exact, unit, scale,
                               statement_type, duration_type, consolidation_scope,
                               dimensions_json, raw_payload_id, data_version
                        FROM financial_facts
                        WHERE source_id = :source_id AND co_code = :co_code AND is_current = 1
                        ORDER BY period, statement_type, metric_code
                        """,
                        {"source_id": source_id, "co_code": co_code},
                    ).fetchall()
                    if facts:
                        return dict(source), "financial_facts", [dict(row) for row in facts]
                legacy = connection.execute(
                    """
                    SELECT period, metric_code, value, unit, scope, data_version
                    FROM financial_metrics
                    WHERE source_id = :source_id AND co_code = :co_code
                    ORDER BY period, metric_code
                    """,
                    {"source_id": source_id, "co_code": co_code},
                ).fetchall()
                return dict(source), "financial_metrics", [dict(row) for row in legacy]

        source, table, rows = await asyncio.to_thread(run)
        if source is None:
            return None
        records = rows
        return SourcePreview(
            source_id=source["source_id"],
            co_code=source["co_code"],
            source_type=SourceType.DATABASE,
            title=source["title"],
            live_url=source["source_url"],
            captured_at=str(source["captured_at"] or "") or None,
            content_hash=source["content_hash"],
            database_record={
                "table": table,
                "records": records,
                "data_version": source["data_version"],
                "source_url": source["source_url"],
                "raw_locator": source["raw_locator"],
            },
        )

    async def list_periods(self, co_code: str) -> list[str]:
        def run() -> list[str]:
            with self._connect() as connection:
                periods = {
                    str(row["period"])
                    for row in connection.execute(
                        "SELECT DISTINCT period FROM financial_metrics WHERE co_code = :co_code",
                        {"co_code": co_code},
                    ).fetchall()
                }
                has_v2 = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='financial_facts'"
                ).fetchone()
                if has_v2:
                    periods.update(
                        str(row["period"])
                        for row in connection.execute(
                            "SELECT DISTINCT period FROM financial_facts "
                            "WHERE co_code = :co_code AND is_current = 1",
                            {"co_code": co_code},
                        ).fetchall()
                    )
                return sorted(periods)

        return await asyncio.to_thread(run)

    async def get_fiscal_calendar(self, co_code: str) -> FiscalCalendar | None:
        def run() -> dict[str, Any] | None:
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='company_fiscal_calendars'"
                ).fetchone()
                if not exists:
                    return None
                row = connection.execute(
                    "SELECT co_code, fiscal_year_end_month, timezone, source "
                    "FROM company_fiscal_calendars WHERE co_code = :co_code",
                    {"co_code": co_code},
                ).fetchone()
                return dict(row) if row else None

        row = await asyncio.to_thread(run)
        return FiscalCalendar.model_validate(row) if row else None

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

    @staticmethod
    def _fact_to_evidence(row: dict[str, Any]) -> Evidence:
        scope = f"{row['consolidation_scope']}_{row['duration_type']}"
        return Evidence(
            evidence_id=f"ev-db-{row['fact_id']}",
            co_code=row["co_code"],
            source_id=row["source_id"],
            source_type=SourceType.DATABASE,
            title=f"{row['co_code']} {row['period']} 正規化財務指標",
            content=(
                f"{row['period']} {row['metric_code']} = {row['value_exact']} "
                f"{row['unit']} ({scope})"
            ),
            score=1.0,
            period=row["period"],
            locator=SourceLocator(
                table="financial_facts",
                primary_key=row["fact_id"],
                columns=[
                    "co_code",
                    "period",
                    "metric_code",
                    "value_exact",
                    "unit",
                    "statement_type",
                    "duration_type",
                    "consolidation_scope",
                ],
            ),
            captured_at=row["captured_at"],
            content_hash=row["content_hash"],
            data_version=row["data_version"],
            metadata={
                "metric_code": row["metric_code"],
                "provider_id": row["provider_id"],
                "provider_metric_key": row["provider_metric_key"],
                "value": float(row["value_exact"]),
                "value_exact": row["value_exact"],
                "unit": row["unit"],
                "scale": row["scale"],
                "scope": scope,
                "statement_type": row["statement_type"],
                "duration_type": row["duration_type"],
                "consolidation_scope": row["consolidation_scope"],
                "fiscal_year": row["fiscal_year"],
                "fiscal_quarter": row["fiscal_quarter"],
                "period_start": row["period_start"],
                "period_end": row["period_end"],
                "dimensions": json.loads(row["dimensions_json"] or "{}"),
                "raw_payload_id": row["raw_payload_id"],
                "metric_display_name": row["metric_display_name"],
                "metric_aliases": json.loads(row["metric_aliases_json"] or "[]"),
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
                "speaker",
                "section",
                "event_date",
                "captured_at",
                "content_hash",
                "data_version",
            ],
            neo4j_database=settings.neo4j_database,
        )
        self.embedding_semaphore = asyncio.Semaphore(settings.embedding_max_concurrency)
        self._enable_composite_vector_filters()

    def _enable_composite_vector_filters(self) -> None:
        """Bridge Neo4j 2026 composite-index metadata across GraphRAG SDK versions."""
        required = ["co_code", "period", "source_type"]
        records, _, _ = self.driver.execute_query(
            "SHOW INDEXES YIELD name, properties WHERE name = $name RETURN properties",
            name=self.settings.neo4j_vector_index,
            database_=self.settings.neo4j_database,
        )
        properties = set(records[0]["properties"] if records else [])
        if {"embedding", *required}.issubset(properties):
            # Some SDK releases inspect only the legacy indexConfig field and miss
            # filter columns exposed by Neo4j in SHOW INDEXES.properties.
            self.vector_retriever._filterable_properties = required

    @staticmethod
    def _expand_financial_query(query: str) -> str:
        """Add compact bilingual retrieval hints while preserving the original query."""
        expansions = {
            "法說會": "earnings call transcript",
            "逐字稿": "transcript",
            "展望": "outlook guidance",
            "下一季": "next quarter Q4",
            "總營收": "total company revenue",
            "應用程式": "apps application platform",
            "代理人": "agents",
            "資料中心": "datacenter capacity",
            "資本支出": "capital expenditures capex",
            "營收": "revenue",
            "毛利率": "gross margin",
            "供應": "supply",
        }
        hints = [english for chinese, english in expansions.items() if chinese in query]
        return f"{query} {' '.join(hints)}".strip()

    async def search_documents(
        self,
        query: str,
        co_code: str,
        top_k: int = 5,
        period: str | None = None,
        source_types: tuple[str, ...] | None = None,
    ) -> list[Evidence]:
        candidate_k = max(top_k * 4, 20)
        filters: dict[str, dict[str, str]] = {"co_code": {"$eq": co_code}}
        if period:
            filters["period"] = {"$eq": period}
        expanded_query = self._expand_financial_query(query)
        async with self.embedding_semaphore:
            query_vector = await asyncio.to_thread(self.embedder.embed_query, expanded_query)
        selected_source_types = source_types or ("financial_report", "transcript", "url")
        results = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self.vector_retriever.search,
                    query_vector=query_vector,
                    top_k=candidate_k,
                    filters={**filters, "source_type": {"$eq": source_type}},
                )
                for source_type in selected_source_types
            )
        )
        unique: dict[str, Evidence] = {}
        for result in results:
            for raw_item in result.items:
                item = self._vector_item_to_evidence(raw_item)
                current = unique.get(item.evidence_id)
                if current is None or item.score > current.score:
                    unique[item.evidence_id] = item
        candidates = list(unique.values())
        lexical_ranks = await asyncio.to_thread(
            self._fulltext_ranks, expanded_query, co_code, candidate_k
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
        selected: list[Evidence] = []
        seen_types: set[str] = set()
        for item in candidates:
            source_type = str(item.source_type)
            if source_type not in seen_types:
                selected.append(item)
                seen_types.add(source_type)
            if len(selected) >= top_k:
                return selected
        selected_ids = {item.evidence_id for item in selected}
        selected.extend(item for item in candidates if item.evidence_id not in selected_ids)
        return selected[:top_k]

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
            locator=SourceLocator(page=data.get("page"), paragraph_id=data.get("paragraph_id")),
            captured_at=data.get("captured_at"),
            content_hash=data.get("content_hash"),
            data_version=data.get("data_version", "unknown"),
            metadata={
                "retriever": "neo4j_vector",
                "speaker": data.get("speaker"),
                "section": data.get("section"),
                "event_date": data.get("event_date"),
                **metadata,
            },
        )

    async def search_graph(
        self,
        query: str,
        co_code: str,
        max_hops: int = 2,
        period: str | None = None,
    ) -> list[Evidence]:
        hops = min(max(max_hops, 1), 2)
        filters: dict[str, dict[str, str]] = {"co_code": {"$eq": co_code}}
        if period:
            filters["period"] = {"$eq": period}
        seed_result = await asyncio.to_thread(
            self.vector_retriever.search,
            query_text=self._expand_financial_query(query),
            top_k=5,
            filters=filters,
        )
        seed_data = [
            self._content_as_dict(getattr(item, "content", item)) for item in seed_result.items
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
          AND NOT (anchor:Risk AND node:Risk AND node <> anchor)
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
                        f"來源段落：{row.get('provenance_text', '')}；圖譜路徑：{' → '.join(path)}"
                    ),
                    score=0.8,
                    period=row.get("period"),
                    locator=SourceLocator(paragraph_id=row.get("paragraph_id"), graph_path=path),
                    captured_at=str(row.get("captured_at") or "") or None,
                    content_hash=row.get("content_hash"),
                    data_version=row.get("data_version") or "unknown",
                    metadata={
                        "hops": len(relationships),
                        "retriever": "vector_seeded_bounded_cypher",
                        "relationship_provenance": row.get("relationship_provenance", []),
                    },
                )
            )
        return evidence

    async def get_source_preview(self, source_id: str, co_code: str) -> SourcePreview | None:
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

    async def list_periods(
        self, co_code: str, source_types: tuple[str, ...] | None = None
    ) -> list[str]:
        selected = list(source_types or ("financial_report", "transcript", "url"))
        cypher = """
        MATCH (chunk:Chunk {co_code: $co_code})
        WHERE chunk.source_type IN $source_types AND chunk.period IS NOT NULL
        RETURN DISTINCT chunk.period AS period
        ORDER BY period
        """

        def run() -> list[str]:
            records, _, _ = self.driver.execute_query(
                cypher,
                co_code=co_code,
                source_types=selected,
                database_=self.settings.neo4j_database,
            )
            return [str(record["period"]) for record in records if record.get("period")]

        return await asyncio.to_thread(run)

    async def close(self) -> None:
        await asyncio.to_thread(self.driver.close)


def build_knowledge_repository(settings: Settings) -> KnowledgeRepository:
    if settings.data_mode == "mock":
        return MockKnowledgeRepository()
    return Neo4jKnowledgeRepository(settings)


def build_finance_repository(settings: Settings) -> FinanceRepository:
    if settings.data_mode == "mock":
        return MockFinanceRepository()
    repositories: list[FinanceRepository] = [SQLiteFinanceRepository(settings)]
    repositories.extend(
        build_external_repositories(
            settings.external_database_config_file,
            strict=settings.external_database_strict,
        )
    )
    repositories.extend(
        build_external_api_repositories(
            settings.external_api_config_file,
            strict=settings.external_api_strict,
        )
    )
    if len(repositories) == 1:
        return repositories[0]
    return CompositeFinanceRepository(
        repositories,
        strict=settings.external_database_strict or settings.external_api_strict,
    )


def dump_evidence(items: Iterable[Evidence]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]
