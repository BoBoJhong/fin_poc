# Configuration Reference

> 文件層級：內部技術附錄。一般產品與部署說明請見 [PROJECT_SPEC.md](PROJECT_SPEC.md)。

Configuration is loaded from environment variables and the repository-root `.env`. Never commit
real credentials. Defaults below match `.env.example` unless stated otherwise.

## Runtime and scope

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | Deployment label |
| `DATA_MODE` | `mock` | `mock` or `local` repository mode |
| `MCP_ENABLED` | `true` | Enable MCP gateway path |
| `FRONTEND_USE_PUBLIC_MCP` | `true` | Route UI answers through public MCP tools |
| `ALLOWED_CO_CODES` | `*` | All Company Master entries or comma-separated allowlist |
| `COMPANY_INDEX_TTL_SECONDS` | `300` | Company Master cache duration |

## Local and external structured data

| Variable | Default | Meaning |
|---|---|---|
| `SQLITE_PATH` | `data/local/financial.sqlite3` | Runtime SQLite path |
| `SQLITE_READ_ONLY` | `true` | Open SQLite in read-only mode |
| `FINANCE_REPOSITORY_MODE` | `hybrid` | `sqlite`, `external`, or `hybrid`; use `external` for production MariaDB-only finance |
| `EXTERNAL_DATABASE_CONFIG_PATH` | `config/external_databases.local.json` | SQL registry |
| `EXTERNAL_DATABASE_STRICT` | `false` | Fail startup/query when an external DB is unavailable |
| `EXTERNAL_API_CONFIG_PATH` | `config/external_apis.local.json` | REST API registry |
| `EXTERNAL_API_STRICT` | `false` | Fail when an approved REST provider is unavailable |

Provider database URLs and API keys use registry-selected environment variable names, for example
`INTERNAL_FINANCE_DATABASE_URL` and `VENDOR_FINANCE_API_KEY`. MariaDB through PyMySQL uses a URL
such as `mariadb+pymysql://readonly_user:<password>@db-host/finance?charset=utf8mb4`.

## Neo4j and embedding

| Variable | Default | Meaning |
|---|---|---|
| `NEO4J_URI` | `neo4j://127.0.0.1:7687` | Neo4j Bolt URI |
| `NEO4J_USERNAME` | `neo4j` | Neo4j user |
| `NEO4J_PASSWORD` | — | Neo4j secret |
| `NEO4J_DATABASE` | `neo4j` | Database name |
| `NEO4J_VECTOR_INDEX` | `chunk_embedding_v1` | Scoped chunk vector index |
| `NEO4J_FULLTEXT_INDEX` | `chunk_fulltext_v1` | Scoped full-text index |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `OLLAMA_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Query/document embedding model |
| `EMBEDDING_MAX_CONCURRENCY` | `8` | Embedding concurrency limit |

## Retrieval and reliability

| Variable | Default | Meaning |
|---|---|---|
| `DOCUMENT_MIN_RELEVANCE_SCORE` | `0.60` | Minimum document evidence score |
| `GRAPH_MIN_RELEVANCE_SCORE` | `0.70` | Minimum graph evidence score |
| `MAX_EVIDENCE_ITEMS` | `8` | Maximum evidence passed downstream |
| `REQUIRE_DOCUMENT_PROVENANCE` | `true` | Require locator/hash provenance |
| `HYBRID_VECTOR_WEIGHT` | `0.75` | Vector share of hybrid reranking |
| `FINANCIAL_FACT_QUERY_LIMIT` | `2000` | Maximum normalized facts returned by one repository query |
| `SEC_USER_AGENT` | example only | SEC-compliant contact identifier |

## Answer LLM

| Variable | Default | Meaning |
|---|---|---|
| `COMPANY_LLM_MODE` | `mock` | `mock` or `openai_compatible` |
| `COMPANY_LLM_BASE_URL` | example URL | OpenAI-compatible API base |
| `COMPANY_LLM_API_KEY` | empty | API secret |
| `COMPANY_LLM_MODEL` | example name | Deployed model identifier |
| `COMPANY_LLM_TIMEOUT_SECONDS` | `30` | Per-call timeout |
| `COMPANY_LLM_MAX_CONCURRENCY` | `16` | LLM in-flight limit per process |
| `COMPANY_LLM_MAX_CONNECTIONS` | `32` | LLM HTTP pool size |
| `COMPANY_LLM_QUEUE_TIMEOUT_SECONDS` | `5` | Wait limit for an LLM slot |

## HTTP API and concurrency

| Variable | Default | Meaning |
|---|---|---|
| `API_HOST` | `127.0.0.1` | Uvicorn bind address used by stack runner |
| `API_PORT` | `8000` | Uvicorn port |
| `API_WORKERS` | `1` | Uvicorn workers |
| `API_MAX_CONCURRENCY` | `64` | In-flight chat requests per worker |
| `API_QUEUE_TIMEOUT_SECONDS` | `2` | Wait before controlled HTTP 503 |
| `CORS_ORIGINS` | local Vite origins | Comma-separated allowed browser origins |

## MCP network and authentication

| Variable | Default | Meaning |
|---|---|---|
| `KNOWLEDGE_MCP_URL` | `http://127.0.0.1:8001/mcp` | Internal knowledge client URL |
| `FINANCE_MCP_URL` | `http://127.0.0.1:8002/mcp` | Internal finance client URL |
| `MCP_SERVER_HOST` | `127.0.0.1` | Host used by local public-MCP clients |
| `MCP_BIND_HOST` | `127.0.0.1` | Listening address for MCP servers |
| `MCP_AUTH_MODE` | `none` | `none` or private-deployment `static` |
| `MCP_SHARED_TOKEN` | placeholder | Bearer token sent between services |
| `KNOWLEDGE_MCP_PORT` | `8001` | Internal knowledge port |
| `FINANCE_MCP_PORT` | `8002` | Internal finance port |
| `RAG_MCP_PORT` | `8003` | Public Financial MCP port |
| `TRANSCRIPT_MCP_PORT` | `8004` | Public Earnings Call MCP port |

When `MCP_AUTH_MODE=static`, startup rejects empty and placeholder tokens. All MCP clients must send
`Authorization: Bearer <MCP_SHARED_TOKEN>`. Use OAuth/OIDC gateway authentication for public Internet
deployments.

## Strict-mode behavior

With external strict mode disabled, one unavailable provider is logged and isolated; healthy
repositories continue serving. With strict mode enabled, provider errors fail the composite query.
Use strict mode only when that provider is mandatory and operational alerting/retry behavior is in
place.
