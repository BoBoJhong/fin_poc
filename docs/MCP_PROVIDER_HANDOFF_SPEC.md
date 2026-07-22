# Verified Financial RAG MCP — Provider Handoff Specification

> 文件角色：三份正式規格之一。這是唯一應直接提供給 MCP 使用者／外部 Agent 團隊的人工規格。
> 專案內部架構與部署說明請見 [PROJECT_SPEC.md](PROJECT_SPEC.md)。

- Provider product: Verified Financial RAG MCP
- Product version: `1.0.0`
- Public Tool input contract: `2.0`
- Public response schema: `1.1`
- Transport: MCP Streamable HTTP
- Document version: `1.1`
- Last updated: `2026-07-23`

This is the self-contained integration document to give an external Agent/MCP client team. Replace
all `<...>` placeholders with deployment values. Send bearer tokens through a secret manager or
another protected channel, never inside this document.

## 0. 對外提供方式

將本文件與 [`mcp-tools.json`](mcp-tools.json) 一起交給串接方，並在交付前填入正式 HTTPS
端點、資料涵蓋範圍、流量限制、SLA 與支援窗口。Token 或 OAuth Client Secret 必須使用密碼
管理器等獨立安全管道提供，不得寫入本文件、範例設定或原始碼。正式對外只提供 `8003`、
`8004` 所代表的公開 MCP Gateway；不得提供內部 Knowledge／Finance MCP 位址。

## 1. Handoff package

Give the integrator these items:

1. This document.
2. Financial MCP URL.
3. Earnings Call MCP URL.
4. Authentication method and token through a separate secure channel.
5. Machine-readable [`mcp-tools.json`](mcp-tools.json).
6. Complete response semantics in
   [`VERIFIED_RAG_MCP_OUTPUT_SPEC.md`](VERIFIED_RAG_MCP_OUTPUT_SPEC.md).
7. Supported company/data coverage and production readiness statement.

Do not give external clients the internal Knowledge MCP or Finance MCP endpoints.

## 2. Public endpoints

| Service | Endpoint | Purpose |
|---|---|---|
| Verified Financial RAG | `https://<host>/<financial-mcp-path>` | Structured financial facts, SEC filings and official financial documents |
| Verified Earnings Call RAG | `https://<host>/<transcript-mcp-path>` | Earnings-call transcript statements, speakers, prepared remarks and Q&A |

Direct private-network defaults are:

```text
http://<server>:8003/mcp
http://<server>:8004/mcp
```

Ports `8001` and `8002` are internal implementation services and are not part of the public
contract.

## 3. Authentication

Private deployments can use a bearer token:

```http
Authorization: Bearer <MCP_TOKEN>
```

Missing or invalid credentials return HTTP `401`. Internet-facing production deployments should
use the organization's OAuth/OIDC gateway. The organization IAM/gateway policy is deployment-owned
and does not change Tool input/output schemas.

Token requirements:

- transmit out-of-band;
- do not commit it to source control or MCP client configuration templates;
- rotate it according to deployment policy;
- never include it in Tool arguments, logs, citations or prompts.

## 4. Public tools

### 4.1 Financial MCP

#### `ask_financial_rag`

Generates a verified answer for one-company financial questions.

Allowed evidence types:

```text
database
financial_report
url
```

It must never return transcript evidence.

#### `retrieve_financial_evidence`

Returns validated financial evidence without answer generation. Use this when the calling Agent
owns synthesis or when no production answer LLM is configured.

### 4.2 Earnings Call MCP

#### `ask_earnings_call`

Generates a verified answer from earnings-call transcripts only. Successful responses also include
the deterministic `display` structure with title, period, speakers, answer content and original
source passages.

Allowed evidence type:

```text
transcript
```

#### `list_earnings_calls`

Resolves one company and returns its available calls newest first. Agents must use this before
interpreting requests such as “recent quarters”; they must not infer quarter availability from the
current date.

#### `retrieve_multi_period_earnings_call_evidence`

Returns separately grouped transcript evidence for up to four requested or latest calls. `quarters`
may contain the internal period or company fiscal label. Broad highlight requests run four retrieval
facets per call and return `coverage_mode: broad_facet_retrieval`. This improves topic coverage but
does not claim every transcript turn was summarized. Each evidence item retains its original period,
locator and content hash.

#### `get_earnings_call_transcript`

Use this for “latest call”, “specific-quarter call” or “show the conversation” requests. It does
not perform vector Top-K retrieval. It deterministically selects one official call and returns
ordered, cursor-paginated speaker turns:

```json
{
  "status": "retrieved",
  "company_code": "MSFT",
  "quarter": "FY2026 Q3",
  "conversations": [
    {
      "speaker": {"name": "SATYA NADELLA", "title": "Chairman and CEO"},
      "content": "Verbatim speaker-turn content"
    }
  ],
  "next_cursor": 5
}
```

`title` is an official job title. It is `null` when the source does not provide one. It never means
`prepared_remarks`, `question_and_answer`, or document title.

The public conversation response intentionally does not expose internal `section` metadata. The
plain-text ingestion adapter accepts `姓名：內容`, `姓名: 內容`, `[姓名] 內容`,
`姓名（職稱）：內容`, and `Speaker` / `Title` / `Content` field layouts; all normalize to the
same `speaker.name`, optional `speaker.title`, and verbatim `content` contract shown above.

#### `retrieve_earnings_call_evidence`

Returns validated transcript evidence without answer generation.

#### `retrieve_earnings_call_blocks`

Returns validated transcript passages as nested JSON objects. Each item contains `period`,
`fiscal_label`, matched `speaker`, all contributing `speakers`, `title`, `score`, and a `content`
object with verbatim `text`, `section`, `paragraph_id`, `source_id`, `content_hash`, and
`source_url`. Use this tool when the caller needs structured transcript blocks rather than the
generic Evidence contract.

## 5. Tool selection

| User intent | Tool call |
|---|---|
| Revenue, EPS, profit, balance sheet, cash flow, financial ratios | `ask_financial_rag` |
| SEC filing, financial-report risk or accounting disclosure | `ask_financial_rag` |
| What management said, outlook, prepared remarks, earnings-call Q&A | `ask_earnings_call` |
| Discover available or recent calls | `list_earnings_calls` |
| Compare topics or highlights across several calls | `retrieve_multi_period_earnings_call_evidence`, then Agent synthesis |
| Read the latest/specific call or obtain ordered dialogue | `get_earnings_call_transcript` |
| External Agent performs generation | corresponding `retrieve_*_evidence` |
| Actual number plus management explanation | call both public MCP services separately |

For mixed questions, preserve each service's citations and verification independently. Do not merge
the two retrieval result sets before verification.

## 6. Input contract

Every public tool requires one common natural-language field:

```json
{
  "query": "Microsoft 最近一季 revenue?"
}
```

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `query` | string | yes | Self-contained natural-language question containing company and intent |

`get_earnings_call_transcript` additionally accepts `cursor` (default `0`) and `limit` (default
`20`, maximum `50`). `next_cursor` is passed back unchanged to read the following page.
`list_earnings_calls` accepts `limit` (maximum `20`). The multi-period evidence tool accepts
`quarters` and `limit`; it compares at most four calls and never merges their evidence arrays.

Company fiscal labels are resolved through the available call records before retrieval. For
example, Microsoft `FY2026 Q2` maps to stored canonical period `2025Q4` in answer, evidence, block
and transcript-reader paths. To retrieve several complete transcripts, call `list_earnings_calls`,
then call `get_earnings_call_transcript` separately for every selected quarter and continue each
cursor until `next_cursor` is `null`. This bounded workflow avoids oversized MCP responses while
preserving complete ordered content.

The other accepted fields are Tool controls, not company selectors: transcript pagination accepts
`cursor` and `limit`; multi-period retrieval accepts `quarters` and `limit`.

Input rules:

- Prefer identifying the company naturally in `query`.
- Public Tool schemas do not accept `co_code`; an extra `co_code` fails strict validation.
- The service resolves formal names, aliases and stock codes against its Company Master.
- The service must not create an unknown `co_code`.
- A body hint must not override an obvious conflicting company in the query.
- Explicit periods and relative expressions such as “最近一季”, “上一季” and “去年同期” are
  resolved against verified available periods and the company fiscal calendar.
- One Tool request covers one company. Multi-company questions require clarification or separate
  calls according to the agreed product scope.
- MCP Tools are stateless. For “那上一季呢？”, the calling Agent uses its dialogue context to send
  a self-contained query such as “Microsoft 上一季的法說會說了什麼？”. It must not invent a
  company absent from both the current message and established conversation.

## 7. Answer response contract

`ask_financial_rag` and `ask_earnings_call` return this required top-level envelope:

```json
{
  "schema_version": "1.1",
  "status": "answered",
  "answer": "Microsoft 2026Q1 revenue was ... [1]",
  "co_code": "MSFT",
  "display": null,
  "citations": [
    {
      "index": 1,
      "evidence_id": "ev-db-fact:example",
      "co_code": "MSFT",
      "source_id": "sec-msft-companyfacts",
      "title": "Microsoft financial facts",
      "source_type": "database",
      "locator": {
        "table": "financial_facts",
        "primary_key": "fact:example",
        "columns": ["metric_code", "value_exact", "unit"]
      },
      "quoted_text": "2026Q1 revenue = ... USD",
      "period": "2026Q1",
      "metadata": {"metric_code": "revenue", "value_exact": "...", "unit": "USD"},
      "live_url": "https://official-source.example/...",
      "content_hash": "sha256:...",
      "captured_at": "2026-07-21T00:00:00Z"
    }
  ],
  "routes": ["finance"],
  "trace_id": "uuid",
  "verification": {"passed": true},
  "verified": true,
  "confidence": 0.97,
  "verification_notes": [],
  "warnings": [],
  "data_versions": ["sec:accession"],
  "latency_ms": 112.5,
  "clarification_question": null,
  "period_resolution": {
    "input": "最近一季",
    "resolved_period": "2026Q1",
    "period_type": "fiscal_quarter",
    "as_of": "2026-07-21",
    "method": "latest_verified_available",
    "confidence": 1.0,
    "available_periods": ["2026Q1"],
    "fiscal_calendar": {
      "co_code": "MSFT",
      "fiscal_year_end_month": 6,
      "timezone": "America/New_York",
      "source": "SEC company profile"
    }
  }
}
```

All top-level fields above are present in Runtime output schema. Nullable fields remain present with
`null`; arrays remain present as empty arrays when applicable.

## 8. Evidence-only response contract

`retrieve_financial_evidence` and `retrieve_earnings_call_evidence` return:

```json
{
  "schema_version": "1.1",
  "status": "retrieved",
  "co_code": "MSFT",
  "period": "2026Q1",
  "evidence": [
    {
      "evidence_id": "ev-db-fact:example",
      "co_code": "MSFT",
      "source_id": "sec-msft-companyfacts",
      "source_type": "database",
      "title": "Microsoft financial facts",
      "content": "2026Q1 revenue = ... USD",
      "score": 0.97,
      "period": "2026Q1",
      "locator": {
        "table": "financial_facts",
        "primary_key": "fact:example",
        "columns": ["metric_code", "value_exact", "unit"]
      },
      "captured_at": "2026-07-21T00:00:00Z",
      "content_hash": "sha256:...",
      "data_version": "sec:accession",
      "metadata": {"metric_code": "revenue", "value_exact": "...", "unit": "USD"}
    }
  ],
  "verified": true,
  "verification": {"passed": true},
  "warnings": [],
  "latency_ms": 95.2,
  "clarification_question": null,
  "period_resolution": {
    "input": "最近一季",
    "resolved_period": "2026Q1",
    "period_type": "fiscal_quarter",
    "as_of": "2026-07-21",
    "method": "latest_verified_available",
    "confidence": 1.0,
    "available_periods": ["2026Q1"],
    "fiscal_calendar": {
      "co_code": "MSFT",
      "fiscal_year_end_month": 6,
      "timezone": "America/New_York",
      "source": "SEC company profile"
    }
  }
}
```

Required fields are exactly those shown. Full nested Evidence JSON Schema is included in
[`mcp-tools.json`](mcp-tools.json).

## 9. Status handling

### `answered`

The caller may display/use the answer only when all conditions hold:

```text
status == answered
verified == true
verification.passed == true
citations.length > 0
```

### `retrieved`

Evidence-only retrieval succeeded. The caller may synthesize only from returned evidence and must
preserve source attribution.

### `refused`

The service understood the request but evidence was missing, unavailable, conflicting or unsafe.
The caller must not supplement the response with model memory.

### `needs_clarification`

Show `clarification_question` to the user, obtain a response and retry the same selected tool.

## 10. Citation contract

Every answer citation contains:

```json
{
  "index": 1,
  "evidence_id": "ev-db-fact:...",
  "co_code": "MSFT",
  "source_id": "sec-msft-companyfacts",
  "title": "Microsoft financial facts",
  "source_type": "database",
  "locator": {
    "table": "financial_facts",
    "primary_key": "fact:...",
    "columns": ["metric_code", "value_exact", "unit"]
  },
  "quoted_text": "2026Q1 revenue = ... USD",
  "period": "2026Q1",
  "metadata": {
    "metric_code": "revenue",
    "value_exact": "...",
    "unit": "USD",
    "statement_type": "income_statement",
    "duration_type": "quarter",
    "consolidation_scope": "consolidated"
  },
  "live_url": "https://official-source.example/...",
  "content_hash": "sha256:...",
  "captured_at": "2026-07-21T00:00:00Z"
}
```

The caller must preserve citation indices, quoted text, company, period, locator and source type.
For source preview, use the citation's own `source_id` and `co_code`.

## 11. Earnings-call display contract

Successful `ask_earnings_call` responses include:

```json
{
  "display": {
    "title": "MSFT 2026Q1 法說會",
    "period": "2026Q1",
    "speakers": ["Satya Nadella"],
    "content": "Verified answer ... [1]",
    "sources": [
      {
        "citation_index": 1,
        "speaker": "Satya Nadella",
        "section": "Prepared Remarks",
        "source_content": "Original transcript passage",
        "source_url": "https://official-source.example/...",
        "locator": {"paragraph_id": "paragraph-18"},
        "content_hash": "sha256:..."
      }
    ]
  }
}
```

`source_content` is copied from verified citation text and must not be rewritten by the caller.
Financial and refused responses use `display: null`.

## 12. Agent integration policy

The calling Agent must:

1. Choose the correct public service.
2. Pass the original user question, not a fabricated keyword-only query.
3. Inspect status and verification before using content.
4. Preserve citations and warnings.
5. Treat management statements as statements, not realized financial facts.
6. Split mixed financial/transcript questions into two calls.
7. Retry HTTP `503` with bounded exponential backoff and jitter.

The calling Agent must not:

- invent a company code, period, number, speaker or citation;
- use internal model memory after `refused`;
- remove warnings or verification failures;
- treat `confidence` as proof without checking `verified` and citations;
- send arbitrary SQL, Cypher, URL or provider parameters;
- call ports 8001/8002 directly;
- mix transcript evidence into a financial fact citation.

## 13. Generic MCP client configuration

The exact client configuration syntax depends on the host application. A conceptual configuration
is:

```json
{
  "mcpServers": {
    "verified-financial-rag": {
      "transport": "http",
      "url": "https://<host>/<financial-mcp-path>",
      "headers": {
        "Authorization": "Bearer ${VERIFIED_RAG_MCP_TOKEN}"
      }
    },
    "verified-earnings-call": {
      "transport": "http",
      "url": "https://<host>/<transcript-mcp-path>",
      "headers": {
        "Authorization": "Bearer ${VERIFIED_RAG_MCP_TOKEN}"
      }
    }
  }
}
```

Use the client application's supported secret-reference mechanism. Do not replace the environment
reference with a committed plaintext token.

### 13.1 OpenCode configuration

OpenCode supports remote MCP servers in `opencode.json`. Only register the two public services;
never register internal ports `8001` or `8002`. The current syntax is documented by
[OpenCode MCP servers](https://dev.opencode.ai/docs/mcp-servers/).

Local development without MCP authentication:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "financial-rag": {
      "type": "remote",
      "url": "http://127.0.0.1:8003/mcp",
      "enabled": true,
      "oauth": false
    },
    "earnings-call": {
      "type": "remote",
      "url": "http://127.0.0.1:8004/mcp",
      "enabled": true,
      "oauth": false
    }
  }
}
```

Private deployment using this project's static bearer-token mode:

```bash
export VERIFIED_RAG_MCP_TOKEN='<token-from-secure-channel>'
```

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "financial-rag": {
      "type": "remote",
      "url": "https://<host>/<financial-mcp-path>",
      "enabled": true,
      "oauth": false,
      "headers": {
        "Authorization": "Bearer {env:VERIFIED_RAG_MCP_TOKEN}"
      }
    },
    "earnings-call": {
      "type": "remote",
      "url": "https://<host>/<transcript-mcp-path>",
      "enabled": true,
      "oauth": false,
      "headers": {
        "Authorization": "Bearer {env:VERIFIED_RAG_MCP_TOKEN}"
      }
    }
  }
}
```

Verification commands:

```bash
opencode mcp list
opencode mcp debug financial-rag
opencode mcp debug earnings-call
```

OpenCode registers MCP tools with the configured server name as a prefix. Keep only the MCPs needed
by the Agent enabled so tool descriptions do not consume unnecessary model context. If OpenCode is
running in a container or on another host, replace `127.0.0.1` with a reachable private HTTPS
endpoint.

## 14. Availability and retry

| Condition | Caller behavior |
|---|---|
| HTTP `200` | Parse Tool result and inspect domain status |
| HTTP `401` | Stop and repair credentials; do not retry repeatedly |
| HTTP `422` / Tool validation error | Repair input or ask for clarification |
| HTTP `503` | Bounded retry with exponential backoff and jitter |
| timeout/network failure | Retry according to client SLA; do not fabricate a result |
| `refused` | Return evidence limitation to user |

Idempotent read-only Tool calls can be retried. Callers should retain `trace_id` from completed
responses for support and audit.

## 15. Versioning

- Current public schema is `1.1`.
- Current public Tool input contract is `2.0`; company scope is query-only.
- Clients must validate `schema_version`.
- Additive optional fields may use a minor update.
- Required-field, type, status-semantic, citation or source-allowlist changes are breaking.
- Breaking changes require a major version or a new Tool name and a coordinated migration period.
- Clients may ignore unknown optional fields but must not ignore `status`, `verified`, `citations`,
  `warnings` or `schema_version`.

## 16. Integration acceptance tests

The integrator must demonstrate:

- [ ] Both MCP endpoints initialize and list the documented tools.
- [ ] Invalid/missing credentials are rejected.
- [ ] A supported financial query returns only allowed financial source types.
- [ ] A supported transcript query returns transcript citations and non-null `display`.
- [ ] A future/unavailable period returns `refused` with no citations.
- [ ] An ambiguous company returns `needs_clarification` or the agreed client error flow.
- [ ] “最近一季” returns a non-null `period_resolution` based on available data.
- [ ] Citation `source_id`, `co_code`, locator and hash are retained by the client.
- [ ] HTTP `503` is retried with bounded backoff.
- [ ] Mixed questions call both services and preserve independent attribution.
- [ ] The client never adds uncited facts after refusal.

## 17. Provider-side release checklist

Before handing over a deployment, the provider supplies:

- final HTTPS URLs;
- authentication and token-rotation procedure;
- supported company/period/source coverage;
- readiness state (`ready` or `evidence_only_ready`);
- schema/tool snapshots generated from the deployed services;
- expected rate/concurrency limits and SLA;
- support owner and trace-ID incident procedure;
- change notification and deprecation period.

Current local evidence and regression coverage is documented in
[`PRODUCT_READINESS.md`](PRODUCT_READINESS.md). A production deployment must replace local URLs,
Mock LLM state and deployment-specific IAM/SLA placeholders before external handoff.

## 18. Provider-side embedding A/B policy

Embedding selection is an internal provider decision and does not change public MCP inputs or
outputs. MCP callers must not send model names, vectors, dimensions or provider credentials.

The current runtime uses Ollama `/api/embed` with `qwen3-embedding:0.6b`. An API Gateway embedding
provider requires a separate adapter before it can be evaluated. Record its endpoint contract,
authentication header, model identifier, dimensions, batch limit, rate limit and normalization
behavior without exposing credentials in evaluation artifacts.

### 18.1 Fair comparison layout

Use identical transcript text and chunk boundaries. Store both vectors on the same `Chunk` so the
experiment does not duplicate or alter source content:

```text
Chunk
├─ embedding_local
└─ embedding_gateway
```

Create independent indexes, for example:

```text
chunk_embedding_local_v1
chunk_embedding_gateway_v1
```

Document and query vectors in one evaluation arm must come from the same embedding model. Never
combine Gateway document vectors with local-model query vectors or the reverse.

### 18.2 Evaluation order and metrics

First compare retrieval without an answer LLM. Use the same Golden Set, `co_code`, period filters,
speaker filters, `top_k` and reranking settings for both arms. Measure:

| Metric | Requirement |
|---|---|
| Recall@5 / Hit@5 | Correct source passage appears in the first five results |
| MRR@10 | Correct passages rank near the top |
| Company and period isolation | Must remain 100% |
| Speaker match rate | Named-speaker queries retrieve the correct turns |
| p50 / p95 latency | Measured separately for ingestion and query embedding |
| Error rate | Timeout, rate-limit, invalid-vector and empty-response rate |
| Cost and throughput | Gateway cost plus documents/queries per second |
| Vector dimensions/storage | Neo4j index size and memory impact |

The benchmark must include Chinese and English queries, explicit speakers, long and short
questions, multiple periods, multi-part questions and unsupported/negative cases. Only after the
retrieval comparison passes should both arms use the same answer LLM for end-to-end verified-answer
evaluation.

### 18.3 Selection and rollout

- Do not accept a model that reduces company/period isolation or provenance correctness.
- Prefer retrieval quality first; use latency and cost to choose between statistically similar
  candidates.
- Save an experiment manifest containing provider, model/version, dimensions, chunk configuration,
  index name, dataset version, evaluation date and measured results.
- Roll out through a new index name. Keep the previous index available for rollback until the new
  model passes live smoke tests.
- Re-embed all transcript chunks when the selected document model changes; changing only query
  embeddings is invalid.
