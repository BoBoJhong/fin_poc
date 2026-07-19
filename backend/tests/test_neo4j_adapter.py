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
    assert "r.co_code = $co_code" in repository.driver.cypher
    assert "r.data_version IS NOT NULL" in repository.driver.cypher


@pytest.mark.asyncio
async def test_document_search_uses_scoped_vector_candidates_and_fulltext_boost() -> None:
    class Retriever:
        def search(self, **kwargs: object) -> SimpleNamespace:
            assert kwargs["filters"] == {"co_code": {"$eq": "DEMO01"}}
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
            assert kwargs["co_code"] == "DEMO01"
            return [{"chunk_id": "c-1", "score": 2.0}], None, None

    repository = object.__new__(Neo4jKnowledgeRepository)
    repository.vector_retriever = Retriever()
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
