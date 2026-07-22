# HTTP API Reference

> 文件層級：內部技術附錄。一般產品與部署說明請見 [PROJECT_SPEC.md](PROJECT_SPEC.md)。

- Service: Verified Financial RAG API
- API version: `1.0.0`
- MCP response schema: `1.1`
- Interactive documentation: `/docs`
- Machine-readable OpenAPI: `/openapi.json`
- Repository snapshot: [`openapi.json`](openapi.json)

The generated OpenAPI file is authoritative for HTTP field types. Regenerate it after
changing routes or Pydantic models:

```bash
make export-api
```

## Base URL and headers

Local base URL: `http://127.0.0.1:8000`

| Header | Required | Purpose |
|---|---:|---|
| `Content-Type: application/json` | POST only | JSON request body |
| `X-User-Id` | No | Audit identity placeholder; default is `poc-user` |
| `X-Co-Code` | No | Backward-compatible company scope hint |

`X-User-Id` is not production authentication. Put the API behind organization IAM or an
authenticated gateway before exposing it to the Internet.

## Health endpoints

### `GET /health`

Liveness check. HTTP `200` means the API process can serve requests.

```json
{"status":"ok","service":"financial-graphrag-api"}
```

### `GET /health/readiness`

Returns capability readiness. `evidence_only_ready` means evidence retrieval can be used,
but a production answer LLM has not been configured.

Important fields:

| Field | Meaning |
|---|---|
| `status` | `ready` or `evidence_only_ready` |
| `schema_version` | Public MCP envelope version |
| `frontend_uses_public_mcp` | Whether UI requests traverse ports 8003/8004 |
| `evidence_tools_ready` | Evidence-only tools are available |
| `answer_llm_ready` | Production-compatible answer LLM is configured |
| `api_max_concurrency` | Per-worker concurrency gate |

## Companies

### `GET /api/v1/companies`

Returns the merged, authorized Company Master from local and approved external providers.

```bash
curl http://127.0.0.1:8000/api/v1/companies
```

Response item:

```json
{
  "co_code": "MSFT",
  "company_name": "Microsoft Corporation",
  "industry": "Technology",
  "aliases": ["Microsoft"]
}
```

## Verified answer

### `POST /api/v1/chat`

Request:

```json
{
  "query": "Microsoft 最近一季 revenue?",
  "co_code": null,
  "conversation_id": "optional-client-id"
}
```

`query` is required and must contain 2–4000 characters. `co_code` is an optional hint;
normal clients should mention the company naturally in `query`.

This optional field is retained only for the legacy HTTP API. Public MCP Tool input contract
`2.0` does not accept `co_code`; MCP Agents must submit a self-contained natural-language `query`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'X-User-Id: integration-test' \
  -d '{"query":"Microsoft 最近一季 revenue?"}'
```

The response follows `VerifiedRAGResponse`. See
[VERIFIED_RAG_MCP_OUTPUT_SPEC.md](VERIFIED_RAG_MCP_OUTPUT_SPEC.md) for the complete contract.

### `POST /api/v1/chat/stream`

Uses the same request and final response as `/api/v1/chat`, delivered as Server-Sent Events.

Event order:

1. `status` — retrieval started.
2. `status` — verification completed.
3. zero or more `token` events.
4. `result` — complete `VerifiedRAGResponse`; this is the authoritative result.

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"query":"Microsoft 2026 Q1 法說會如何說明需求？"}'
```

Clients must use the final `result` event for citations, verification and status. Concatenated
`token` events are display-only.

## Source preview

### `GET /api/v1/sources/{source_id}?co_code={co_code}`

Returns the exact source snapshot, database/API record or provenance metadata behind a citation.
Always use the citation's own `source_id` and `co_code`.

```bash
curl --get 'http://127.0.0.1:8000/api/v1/sources/sec-msft-companyfacts' \
  --data-urlencode 'co_code=MSFT'
```

## Status and error handling

HTTP and domain status are separate:

| HTTP | Meaning |
|---:|---|
| `200` | Request completed; inspect domain `status` and `verified` |
| `403` | Requested company violates configured authorization scope |
| `404` | Source preview does not exist for the company |
| `422` | Invalid request or unresolved/ambiguous company |
| `503` | Concurrency queue timed out; retry with backoff |

Domain status:

- `answered`: evidence and answer gates passed.
- `refused`: request was understood, but evidence was unavailable or unsafe.
- `needs_clarification`: caller must ask the returned `clarification_question`.

Never convert `refused` into an answer using model memory.

## Compatibility

- Additive optional fields require a minor API/schema update.
- Required-field, type, status-semantic or route changes require a major version.
- Consumers should ignore unknown optional fields but must validate `schema_version`, `status`,
  `verified`, `citations` and `warnings`.
