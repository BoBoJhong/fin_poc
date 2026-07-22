from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from neo4j import GraphDatabase

from app.config import get_settings
from app.database_connectors import (
    ExternalSQLNarrativeReader,
    NarrativeRecord,
    discover_database,
    load_external_database_registry,
    resolve_environment_value,
)
from scripts.init_data import create_indexes, embed
from scripts.text_blocks import build_semantic_blocks


def build_catalog(report: dict[str, Any], database_id: str) -> dict[str, list[dict[str, Any]]]:
    schemas: dict[str, dict[str, Any]] = {}
    tables: list[dict[str, Any]] = []
    columns: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    for table in report.get("tables", []):
        schema_name = str(table.get("schema_name") or "default")
        schema_id = f"{database_id}:{schema_name}"
        table_id = f"{schema_id}:{table['table']}"
        schemas[schema_id] = {
            "schema_id": schema_id,
            "source_id": database_id,
            "name": schema_name,
        }
        tables.append(
            {
                "table_id": table_id,
                "schema_id": schema_id,
                "source_id": database_id,
                "name": table["table"],
                "primary_key": table.get("primary_key", []),
                "indexes": table.get("indexes", []),
            }
        )
        for position, column in enumerate(table.get("columns", [])):
            columns.append(
                {
                    "column_id": f"{table_id}:{column['name']}",
                    "table_id": table_id,
                    "source_id": database_id,
                    "name": column["name"],
                    "type": column["type"],
                    "nullable": column["nullable"],
                    "position": position,
                }
            )
        for foreign_key in table.get("foreign_keys", []):
            target_schema = str(foreign_key.get("referred_schema") or schema_name)
            target_table = foreign_key.get("referred_table")
            if not target_table:
                continue
            foreign_keys.append(
                {
                    "from_table_id": table_id,
                    "to_table_id": f"{database_id}:{target_schema}:{target_table}",
                    "name": foreign_key.get("name"),
                    "constrained_columns": foreign_key.get("constrained_columns", []),
                    "referred_columns": foreign_key.get("referred_columns", []),
                }
            )
    return {
        "schemas": list(schemas.values()),
        "tables": tables,
        "columns": columns,
        "foreign_keys": foreign_keys,
    }


def sync_catalog(
    driver: Any,
    *,
    database: str,
    database_id: str,
    report: dict[str, Any],
) -> dict[str, int]:
    catalog = build_catalog(report, database_id)
    sync_token = str(uuid.uuid4())
    constraints = (
        "CREATE CONSTRAINT data_source_id IF NOT EXISTS FOR (n:DataSource) REQUIRE n.source_id IS UNIQUE",
        "CREATE CONSTRAINT database_schema_id IF NOT EXISTS FOR (n:DatabaseSchema) REQUIRE n.schema_id IS UNIQUE",
        "CREATE CONSTRAINT database_table_id IF NOT EXISTS FOR (n:DatabaseTable) REQUIRE n.table_id IS UNIQUE",
        "CREATE CONSTRAINT database_column_id IF NOT EXISTS FOR (n:DatabaseColumn) REQUIRE n.column_id IS UNIQUE",
    )
    for query in constraints:
        driver.execute_query(query, database_=database)
    driver.execute_query(
        """
        MERGE (source:DataSource {source_id: $source_id})
          SET source.kind = 'sql_database', source.dialect = $dialect,
              source.driver = $driver, source.sync_token = $sync_token,
              source.schema_captured_at = $captured_at
        WITH source
        UNWIND $schemas AS item
        MERGE (schema:DatabaseSchema {schema_id: item.schema_id})
          SET schema += item, schema.sync_token = $sync_token
        MERGE (source)-[:HAS_SCHEMA]->(schema)
        """,
        source_id=database_id,
        dialect=report.get("dialect"),
        driver=report.get("driver"),
        sync_token=sync_token,
        captured_at=datetime.now(UTC).isoformat(),
        schemas=catalog["schemas"],
        database_=database,
    )
    driver.execute_query(
        """
        UNWIND $tables AS item
        MATCH (schema:DatabaseSchema {schema_id: item.schema_id})
        MERGE (table:DatabaseTable {table_id: item.table_id})
          SET table += item, table.sync_token = $sync_token
        MERGE (schema)-[:HAS_TABLE]->(table)
        """,
        tables=catalog["tables"],
        sync_token=sync_token,
        database_=database,
    )
    driver.execute_query(
        """
        UNWIND $columns AS item
        MATCH (table:DatabaseTable {table_id: item.table_id})
        MERGE (column:DatabaseColumn {column_id: item.column_id})
          SET column += item, column.sync_token = $sync_token
        MERGE (table)-[:HAS_COLUMN]->(column)
        """,
        columns=catalog["columns"],
        sync_token=sync_token,
        database_=database,
    )
    driver.execute_query(
        """
        UNWIND $foreign_keys AS item
        MATCH (source:DatabaseTable {table_id: item.from_table_id})
        MATCH (target:DatabaseTable {table_id: item.to_table_id})
        MERGE (source)-[relation:FOREIGN_KEY_TO {name: coalesce(item.name, '')}]->(target)
          SET relation.constrained_columns = item.constrained_columns,
              relation.referred_columns = item.referred_columns,
              relation.sync_token = $sync_token
        """,
        foreign_keys=catalog["foreign_keys"],
        sync_token=sync_token,
        database_=database,
    )
    driver.execute_query(
        """
        MATCH (node) WHERE (node:DatabaseSchema OR node:DatabaseTable OR node:DatabaseColumn)
          AND node.source_id = $source_id AND node.sync_token <> $sync_token
        DETACH DELETE node
        """,
        source_id=database_id,
        sync_token=sync_token,
        database_=database,
    )
    return {key: len(value) for key, value in catalog.items()}


def build_documents(records: Iterable[NarrativeRecord]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for record in records:
        blocks = build_semantic_blocks([record.text], max_chars=1400, min_chars=160)
        documents.append(
            {
                **record.model_dump(),
                "source_period_json": json.dumps(
                    record.source_period, ensure_ascii=False, default=str
                ),
                "table_id": (
                    f"{record.database_id}:{record.schema_name or 'default'}:{record.table}"
                ),
                "chunks": [
                    {
                        "chunk_id": f"{record.source_id}-c{index:04d}",
                        "sequence": index,
                        "paragraph_id": f"db-{index}",
                        "text": text,
                    }
                    for index, text in enumerate(blocks, start=1)
                ],
            }
        )
    return documents


def upsert_narratives(
    driver: Any,
    *,
    database: str,
    documents: list[dict[str, Any]],
    vectors: list[list[float]],
) -> None:
    offset = 0
    for document in documents:
        chunk_ids = [item["chunk_id"] for item in document["chunks"]]
        driver.execute_query(
            """
            MERGE (company:Company {co_code: $co_code})
              ON CREATE SET company.name = $co_code
            MERGE (document:Document {source_id: $source_id})
              SET document.co_code = $co_code, document.source_type = $source_type,
                  document.title = $title, document.period = $period,
                  document.captured_at = $captured_at,
                  document.content_hash = $content_hash,
                  document.data_version = $data_version,
                  document.upstream_database_id = $database_id,
                  document.upstream_dataset_id = $dataset_id,
                  document.upstream_table = $locator_table,
                  document.upstream_primary_key = $primary_key,
                  document.source_period = $source_period_json
            MERGE (company)-[:HAS_DOCUMENT]->(document)
            WITH document
            OPTIONAL MATCH (table:DatabaseTable {table_id: $table_id})
            FOREACH (_ IN CASE WHEN table IS NULL THEN [] ELSE [1] END |
              MERGE (table)-[:PROVIDES_DOCUMENT]->(document))
            WITH document
            OPTIONAL MATCH (document)-[:HAS_CHUNK]->(stale:Chunk)
            WHERE NOT stale.chunk_id IN $chunk_ids
            DETACH DELETE stale
            """,
            **{key: value for key, value in document.items() if key != "chunks"},
            locator_table=(
                f"{document['schema_name']}.{document['table']}"
                if document["schema_name"]
                else document["table"]
            ),
            chunk_ids=chunk_ids,
            database_=database,
        )
        for chunk in document["chunks"]:
            vector = vectors[offset]
            offset += 1
            driver.execute_query(
                """
                MATCH (document:Document {source_id: $source_id})
                MERGE (chunk:Chunk {chunk_id: $chunk_id})
                  SET chunk.co_code = $co_code, chunk.source_id = $source_id,
                      chunk.source_type = $source_type, chunk.title = $title,
                      chunk.period = $period, chunk.text = $text,
                      chunk.sequence = $sequence, chunk.paragraph_id = $paragraph_id,
                      chunk.embedding = $embedding, chunk.captured_at = $captured_at,
                      chunk.content_hash = $content_hash,
                      chunk.data_version = $data_version,
                      chunk.locator_table = $locator_table,
                      chunk.locator_primary_key = $primary_key,
                      chunk.source_period = $source_period_json
                MERGE (document)-[:HAS_CHUNK]->(chunk)
                """,
                **{key: value for key, value in document.items() if key not in {"chunks", "text"}},
                **chunk,
                embedding=vector,
                locator_table=(
                    f"{document['schema_name']}.{document['table']}"
                    if document["schema_name"]
                    else document["table"]
                ),
                database_=database,
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync approved internal SQL schemas and narratives to Neo4j."
    )
    parser.add_argument("--database-id", help="Sync one configured database only.")
    parser.add_argument("--schema-only", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    registry = load_external_database_registry(settings.external_database_config_file)
    selected = [
        item
        for item in registry.databases
        if item.enabled and (args.database_id is None or item.id == args.database_id)
    ]
    if not selected:
        raise SystemExit("No enabled database matched the requested id.")
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    summary: dict[str, Any] = {"databases": {}}
    try:
        driver.verify_connectivity()
        for config in selected:
            url = resolve_environment_value(config.url_env)
            if not url:
                raise RuntimeError(f"Missing database URL environment variable: {config.url_env}")
            catalog_counts = sync_catalog(
                driver,
                database=settings.neo4j_database,
                database_id=config.id,
                report=discover_database(url),
            )
            documents: list[dict[str, Any]] = []
            if not args.schema_only and any(item.approved for item in config.narrative_datasets):
                reader = ExternalSQLNarrativeReader(config)
                try:
                    documents = build_documents(reader.read())
                finally:
                    reader.close()
                chunks = [chunk for document in documents for chunk in document["chunks"]]
                if chunks:
                    vectors = embed(
                        [chunk["text"] for chunk in chunks],
                        settings.ollama_url,
                        settings.ollama_embedding_model,
                    )
                    create_indexes(
                        driver,
                        len(vectors[0]),
                        settings.neo4j_database,
                        settings.neo4j_vector_index,
                        settings.neo4j_fulltext_index,
                    )
                    upsert_narratives(
                        driver,
                        database=settings.neo4j_database,
                        documents=documents,
                        vectors=vectors,
                    )
            summary["databases"][config.id] = {
                "catalog": catalog_counts,
                "narrative_documents": len(documents),
                "narrative_chunks": sum(len(item["chunks"]) for item in documents),
            }
    finally:
        driver.close()
    print(json.dumps({"status": "ok", **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
