# Verified Financial RAG MCP — Provider Handoff Specification

> 文件角色：三份正式規格之一。這是唯一應直接提供給 MCP 使用者／外部 Agent 團隊的人工規格。
> 專案內部架構與部署說明請見 [PROJECT_SPEC.md](PROJECT_SPEC.md)。

- Provider product: Verified Financial RAG MCP
- Product version: `1.0.0`
- Public response schema: `1.1`
- Transport: MCP Streamable HTTP
- Document version: `1.0`
- Last updated: `2026-07-21`

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

#### `retrieve_earnings_call_evidence`

Returns validated transcript evidence without answer generation.

## 5. Tool selection

| User intent | Tool call |
|---|---|
| Revenue, EPS, profit, balance sheet, cash flow, financial ratios | `ask_financial_rag` |
| SEC filing, financial-report risk or accounting disclosure | `ask_financial_rag` |
| What management said, outlook, prepared remarks, earnings-call Q&A | `ask_earnings_call` |
| External Agent performs generation | corresponding `retrieve_*_evidence` |
| Actual number plus management explanation | call both public MCP services separately |

For mixed questions, preserve each service's citations and verification independently. Do not merge
the two retrieval result sets before verification.

## 6. Input contract

All four tools use:

```json
{
  "query": "Microsoft 最近一季 revenue?",
  "co_code": "MSFT"
}
```

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `query` | string | yes | Original natural-language user question containing company and intent |
| `co_code` | string/null | no | Backward-compatible canonical company-code hint |

Input rules:

- Prefer identifying the company naturally in `query`.
- The service resolves formal names, aliases and stock codes against its Company Master.
- The service must not create an unknown `co_code`.
- A body hint must not override an obvious conflicting company in the query.
- Explicit periods and relative expressions such as “最近一季”, “上一季” and “去年同期” are
  resolved against verified available periods and the company fiscal calendar.
- One Tool request covers one company. Multi-company questions require clarification or separate
  calls according to the agreed product scope.

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
