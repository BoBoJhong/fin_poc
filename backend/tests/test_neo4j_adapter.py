from app.repositories import Neo4jKnowledgeRepository


def test_vector_result_parses_neo4j_mapping_string() -> None:
    parsed = Neo4jKnowledgeRepository._content_as_dict(
        "{'chunk_id': 'c-1', 'co_code': 'DEMO01', 'text': '內容', 'page': None}"
    )
    assert parsed["chunk_id"] == "c-1"
    assert parsed["co_code"] == "DEMO01"
    assert parsed["page"] is None

