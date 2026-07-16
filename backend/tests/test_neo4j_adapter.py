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
