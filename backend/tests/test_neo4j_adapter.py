import asyncio
from types import SimpleNamespace

import pytest

from app.repositories import Neo4jKnowledgeRepository


def test_vector_result_parses_neo4j_mapping_string() -> None:
    parsed = Neo4jKnowledgeRepository._content_as_dict(
        "{'chunk_id': 'c-1', 'co_code': 'DEMO01', 'text': '內容', 'page': None}"
    )
    assert parsed["chunk_id"] == "c-1"
    assert parsed["co_code"] == "DEMO01"
    assert parsed["page"] is None


@pytest.mark.asyncio
async def test_graph_query_scopes_every_node_and_relationship() -> None:
    class Retriever:
        def search(self, **_: object) -> SimpleNamespace:
            return SimpleNamespace(
                items=[
                    SimpleNamespace(
                        content={
                            "chunk_id": "c-1",
                            "co_code": "DEMO01",
                            "source_id": "source-1",
                        }
                    )
                ]
            )

    class Driver:
        cypher = ""

        def execute_query(self, cypher: str, **_: object):
            self.cypher = cypher
            return [], None, None

    repository = object.__new__(Neo4jKnowledgeRepository)
    repository.vector_retriever = Retriever()
    repository.driver = Driver()
    repository.settings = SimpleNamespace(neo4j_database="neo4j")

    await repository.search_graph("產品風險", "DEMO01", max_hops=2)

    assert "ALL(n IN nodes(path) WHERE n.co_code = $co_code)" in repository.driver.cypher
    assert "NOT (anchor:Risk AND node:Risk AND node <> anchor)" in repository.driver.cypher
    assert "r.co_code = $co_code" in repository.driver.cypher
    assert "r.data_version IS NOT NULL" in repository.driver.cypher


@pytest.mark.asyncio
async def test_document_search_uses_scoped_vector_candidates_and_fulltext_boost() -> None:
    class Retriever:
        def search(self, **kwargs: object) -> SimpleNamespace:
            assert kwargs["filters"]["co_code"] == {"$eq": "DEMO01"}
            assert kwargs["filters"]["source_type"]["$eq"] in {
                "financial_report",
                "transcript",
                "url",
            }
            assert kwargs["query_vector"] == [0.1, 0.2]
            return SimpleNamespace(
                items=[
                    SimpleNamespace(
                        content={
                            "chunk_id": "c-1",
                            "co_code": "DEMO01",
                            "source_id": "source-1",
                            "source_type": "transcript",
                            "title": "法說會",
                            "text": "海外專案驗收遞延",
                            "period": "2026Q2",
                            "paragraph_id": "p-1",
                            "content_hash": "sha256:c-1",
                            "data_version": "v1",
                        },
                        metadata={"score": 0.8},
                    )
                ]
            )

    class Driver:
        def execute_query(self, cypher: str, **kwargs: object):
            assert "WHERE node.co_code = $co_code" in cypher
            assert "$period IS NULL OR node.period = $period" in cypher
            assert "node.source_type IN $source_types" in cypher
            assert kwargs["co_code"] == "DEMO01"
            return [{"chunk_id": "c-1", "score": 2.0}], None, None

    repository = object.__new__(Neo4jKnowledgeRepository)
    repository.vector_retriever = Retriever()
    repository.embedder = SimpleNamespace(embed_query=lambda _: [0.1, 0.2])
    repository.embedding_semaphore = asyncio.Semaphore(1)
    repository.driver = Driver()
    repository.settings = SimpleNamespace(
        hybrid_vector_weight=0.75,
        neo4j_fulltext_index="chunk_fulltext_v1",
        neo4j_database="neo4j",
    )

    results = await repository.search_documents("海外專案", "DEMO01", top_k=5)
    assert len(results) == 1
    assert results[0].score > 0.8
    assert results[0].metadata["fulltext_rank"] == 1
    assert results[0].metadata["retriever"] == "scoped_vector_fulltext_hybrid"


@pytest.mark.asyncio
async def test_transcript_search_hard_filters_uniquely_mentioned_speaker() -> None:
    embedded_queries: list[str] = []

    class Retriever:
        def search(self, **_: object) -> SimpleNamespace:
            raise AssertionError("speaker-scoped search must not use the unfiltered vector index")

    class Driver:
        def execute_query(self, cypher: str, **kwargs: object):
            if "UNWIND coalesce" in cypher:
                return (
                    [
                        {"speaker": "AMY HOOD"},
                        {"speaker": "JONATHAN NEILSON"},
                        {"speaker": "SATYA NADELLA"},
                    ],
                    None,
                    None,
                )
            if "vector.similarity.cosine" in cypher:
                assert kwargs["co_code"] == "MSFT"
                assert kwargs["period"] == "2026Q1"
                assert kwargs["speakers"] == ["SATYA NADELLA"]
                assert kwargs["query_vectors"] == [[0.1, 0.2]]
                return (
                    [
                        {
                            "data": {
                                "chunk_id": "satya-1",
                                "co_code": "MSFT",
                                "source_id": "call-1",
                                "source_type": "transcript",
                                "title": "Microsoft earnings call",
                                "text": "We added another gigawatt of capacity.",
                                "period": "2026Q1",
                                "speaker": "JONATHAN NEILSON",
                                "speakers": ["JONATHAN NEILSON", "SATYA NADELLA"],
                                "section": "prepared_remarks",
                                "fiscal_label": "FY2026 Q3",
                                "content_hash": "sha256:satya-1",
                                "data_version": "ir:test",
                            },
                            "score": 0.9,
                            "facet_scores": [0.9],
                        }
                    ],
                    None,
                    None,
                )
            if "db.index.fulltext.queryNodes" in cypher:
                return [], None, None
            raise AssertionError(f"unexpected query: {cypher}")

    repository = object.__new__(Neo4jKnowledgeRepository)
    repository.vector_retriever = Retriever()
    repository.embedder = SimpleNamespace(
        embed_query=lambda query: embedded_queries.append(query) or [0.1, 0.2]
    )
    repository.embedding_semaphore = asyncio.Semaphore(1)
    repository.driver = Driver()
    repository.settings = SimpleNamespace(
        hybrid_vector_weight=0.75,
        neo4j_fulltext_index="chunk_fulltext_v1",
        neo4j_database="neo4j",
    )

    results = await repository.search_documents(
        "微軟 2026 Q1 法說會中 Satya 如何說明資料中心容量？",
        "MSFT",
        top_k=5,
        period="2026Q1",
        source_types=("transcript",),
    )

    assert results
    assert {item.metadata["speaker"] for item in results} == {"SATYA NADELLA"}
    assert results[0].metadata["primary_speaker"] == "JONATHAN NEILSON"
    assert results[0].metadata["speaker_filter"] == ["SATYA NADELLA"]
    assert results[0].metadata["fiscal_label"] == "FY2026 Q3"
    assert embedded_queries[0].startswith("Instruct: Retrieve verbatim earnings-call")


def test_multi_part_query_is_split_into_bounded_facets() -> None:
    query = "What were capital expenditures and what did Amy Hood say about demand?"

    assert Neo4jKnowledgeRepository._query_facets(query) == [
        query,
        "What were capital expenditures",
        "what did Amy Hood say about demand",
    ]


def test_chinese_multi_part_query_is_split_into_bounded_facets() -> None:
    query = "微軟如何看待 AI 需求，以及資本支出與毛利率展望？"

    assert Neo4jKnowledgeRepository._query_facets(query) == [
        query,
        "微軟如何看待 AI 需求",
        "資本支出與毛利率展望",
    ]


@pytest.mark.asyncio
async def test_transcript_reader_selects_latest_official_call_and_pages_turns() -> None:
    class Driver:
        def execute_query(self, cypher: str, **kwargs: object):
            if "MATCH (call:EarningsCall" in cypher:
                assert "call.official_source = true" in cypher
                assert kwargs["co_code"] == "MSFT"
                assert kwargs["period"] is None
                return (
                    [
                        {
                            "source_id": "call-latest",
                            "period": "2026Q1",
                            "quarter": "FY2026 Q3",
                            "event_date": "2026-04-29",
                            "source_url": "https://example.test/call",
                        }
                    ],
                    None,
                    None,
                )
            if "HAS_TURN" in cypher:
                assert kwargs["cursor"] == 0
                assert kwargs["fetch_limit"] == 2
                return (
                    [
                        {
                            "sequence": 1,
                            "speaker": "SATYA NADELLA",
                            "speaker_title": "Chairman and CEO",
                            "content": "Opening remarks.",
                        },
                        {
                            "sequence": 2,
                            "speaker": "AMY HOOD",
                            "speaker_title": "EVP & CFO",
                            "content": "Financial remarks.",
                        },
                    ],
                    None,
                    None,
                )
            raise AssertionError(f"unexpected query: {cypher}")

    repository = object.__new__(Neo4jKnowledgeRepository)
    repository.driver = Driver()
    repository.settings = SimpleNamespace(neo4j_database="neo4j")

    page = await repository.get_transcript_conversation("MSFT", limit=1)

    assert page is not None
    assert page.quarter == "FY2026 Q3"
    assert page.conversations[0].speaker.model_dump() == {
        "name": "SATYA NADELLA",
        "title": "Chairman and CEO",
    }
    assert page.next_cursor == 1
