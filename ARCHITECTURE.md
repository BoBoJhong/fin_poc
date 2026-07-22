# Verified Financial RAG Architecture

> 文件層級：內部技術附錄。產品與工程主規格以
> [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md) 為入口。

- Product version: `1.0.0`
- Public MCP schema: `1.1`
- Architecture principle: source-isolated retrieval, deterministic scope, verifiable evidence

## System context

```text
User / External Agent
        |
        +--> React UI --> FastAPI (8000)
        |                    |
        |                    +--> PublicMCPChatService
        |
        +--------------------+--> Financial RAG MCP (8003)
        |                           |
        |                           +--> Knowledge MCP (8001)
        |                           +--> Finance MCP (8002)
        |
        +-----------------------> Earnings Call MCP (8004)
                                    |
                                    +--> Knowledge MCP (8001, transcript scope)

Finance MCP --> selectable SQLite / approved external SQL DB / hybrid repositories
Knowledge MCP --> Neo4j scoped vector/full-text retrieval + Qwen embedding
Answer pipeline --> OpenAI-compatible LLM or deterministic Mock mode
```

Ports 8001 and 8002 are implementation details and must remain private. External Agents integrate
with 8003 and 8004 only.

## Request lifecycle

1. Normalize the user query.
2. Resolve company against the merged Company Master; constrained LLM selection may only choose
   from ranked existing candidates.
3. Resolve explicit or relative period from verified periods and company fiscal calendar.
4. Select Financial or Transcript retrieval profile.
5. Retrieve with mandatory `co_code`, period and source-type filters.
6. Normalize all provider records to `Evidence`.
7. Validate company, period, source allowlist, provenance, score and conflicting metrics.
8. Generate an answer only from validated Evidence.
9. Validate citation indices, numbers and claim/evidence support.
10. Return `answered`, `refused` or `needs_clarification` with traceable citations.

Relative periods never use the current date as a substitute for data availability. “最近一季” is
the latest verified period for that company and retrieval profile.

## Public boundaries

### Financial RAG MCP

Tools:

- `ask_financial_rag`
- `retrieve_financial_evidence`

Allowed sources: `database`, `financial_report`, `url`. In production DB-only mode, `database`
means the approved internal MariaDB mappings; SQLite and external API fallback are disabled.

### Earnings Call MCP

Tools:

- `ask_earnings_call`
- `list_earnings_calls`
- `retrieve_multi_period_earnings_call_evidence`
- `get_earnings_call_transcript`
- `retrieve_earnings_call_evidence`
- `retrieve_earnings_call_blocks`

Allowed source: `transcript` only. Speaker, section and source content come directly from citation
metadata and quoted text. When a natural-language query uniquely names a known speaker, retrieval
hard-filters chunks by `speakers` before cosine ranking. The block tool returns nested attribution
metadata without changing the existing string-based Evidence and citation contracts.

`ask_earnings_call` answers a question through verified RAG.
`list_earnings_calls` deterministically discovers available calls. The multi-period evidence tool
selects up to four calls and retrieves each period independently, preventing cross-quarter evidence
mixing. Broad highlight requests run four coverage facets (operations, strategy, outlook/risk and
Q&A) per call; the result remains cited retrieval evidence, not a claim of exhaustive summarization.
`get_earnings_call_transcript` handles “latest/specific call content” deterministically: it resolves
one official call and returns cursor-paginated, ordered, single-speaker turns. It never uses vector
Top-K for transcript browsing. `retrieve_earnings_call_evidence` remains the audit/external-RAG
contract; the block tool is retained as the detailed compatibility contract.

Mixed questions are two isolated tool calls combined by the calling Agent. Retrieval results are
never mixed into a shared Top-K.

## Stable domain contracts

| Contract | Purpose |
|---|---|
| `CompanySummary` | Canonical `co_code`, name, industry and aliases |
| `FiscalCalendar` | Company fiscal year end and timezone |
| `PeriodResolution` | User expression, resolved period, method and available periods |
| `Evidence` | Normalized attributable fact or source passage |
| `SourceLocator` | Table/key/columns, paragraph/page/timestamp or graph path |
| `SourcePreview` | Recoverable source snapshot or normalized provider record |
| `VerifiedRAGResponse` | Public answer, citation, verification and status envelope |
| `EvidenceToolResponse` | Evidence-only public response without answer generation |
| `MetricDefinition` | Stable approved metric semantics and aliases |
| `ProviderMetricMapping` | Exact provider-key to internal metric mapping |
| `FinancialFact` | Exact, dimensioned and versioned normalized numeric fact |

Vendor field names stop at adapters. They never modify public MCP fields.

## Data providers

### SQLite

Read-only runtime snapshot with company/source tables and Financial Data Schema v2:
`financial_raw_payloads`, `financial_metric_definitions`, `provider_metric_mappings` and
`financial_facts`. Legacy `financial_metrics` remains readable during migration. Parameterized SQL
only.

### External SQL

SQLAlchemy reflection over explicitly approved tables and column mappings. Credentials are stored in
environment variables; arbitrary SQL is forbidden. `FINANCE_REPOSITORY_MODE=external` makes this
the only financial repository. Company-master mappings supply names, aliases and fiscal calendars.
The schema catalog (schema/table/column/PK/index/FK) may be synchronized to Neo4j for operations,
but internal database rows are not embedded. Only earnings-call transcript chunks are embedded. See
[docs/INTERNAL_DATABASE_QUICKSTART.md](docs/INTERNAL_DATABASE_QUICKSTART.md).

### External REST API

Explicitly approved base URL and GET endpoints. The adapter accepts both row-based metric/value data
and nested dynamic metric-key objects. Dynamic keys require exact approved dictionary mappings;
unknown keys remain observable but cannot become verified facts. The adapter disables redirects,
bounds response size/connections/timeouts, refilters company/period and hashes raw records.

### Financial normalization

```text
raw provider payload
  -> immutable payload/hash
  -> approved Metric Dictionary
  -> approved Provider Metric Mapping
  -> exact Decimal Financial Fact + dimensions + revision
  -> database Evidence
```

Numeric facts use deterministic structured queries. Embedding is used for transcript chunks; it
does not select financial values by vector similarity. Full rules are defined in
[docs/FINANCIAL_DATA_SPEC.md](docs/FINANCIAL_DATA_SPEC.md).

### Neo4j GraphRAG

Qwen embedding produces query vectors. Vector candidates are server-side filtered by `co_code`,
period and source type, then optionally reranked with scoped full-text signals. Graph expansion uses
fixed relationship allowlists and a maximum hop count; unrestricted Text2Cypher is not used.
Explicit multi-part English questions retain the full-query vector and add up to two facet vectors;
their cosine scores are fused before the lexical rerank.

Narrative ingestion uses bounded, structure-aware blocks. SEC blocks preserve every non-whitespace
line in the selected sections, merge short content, hard-split long paragraphs and never silently
stop at a document-level chunk count. Transcript blocks preserve speaker/section labels, hard-split
long turns and merge short turns only within the same section while retaining all contributing
speakers. Re-ingestion removes stale chunks for the same source before publishing the new set.

Transcript ingestion has separate source adapters. `microsoft_ir_html` handles the verified
Microsoft IR layout. `plain_text` accepts UTF-8 text using `姓名：內容`, `姓名: 內容`,
`[姓名] 內容`, `姓名（職稱）：內容`, or the three-line `Speaker` / `Title` / `Content`
layout. These layouts normalize to the same ordered speaker turns before chunking. Section is
internal optional metadata: plain text defaults to `unknown` and changes only when an explicit
prepared-remarks or Q&A heading is present.

The implemented first-stage boundaries, remaining tokenizer limitation, relevant retrieval research
and proposed parent-child evaluation path are documented in
[docs/EMBEDDING_CHUNKING_RESEARCH.md](docs/EMBEDDING_CHUNKING_RESEARCH.md).

The fixed earnings-call graph, identity keys, title semantics and relationship cardinalities are
defined in [docs/NEO4J_EARNINGS_CALL_GRAPH.md](docs/NEO4J_EARNINGS_CALL_GRAPH.md).

## Company Entity Index

```text
exact canonical name / alias / ticker with token boundary
  -> unique high-confidence fuzzy candidate
  -> bounded Top-N candidates
  -> optional constrained LLM choice
  -> clarification on unknown or ambiguity
```

The model cannot create a new company code. `ALLOWED_CO_CODES` is enforced again at repository and
validation boundaries.

## Reliability and refusal

An answer can be `verified=true` only when citations are non-empty and all required gates pass.
Future or absent periods, ambiguous companies, source-type leakage, missing provenance, conflicting
metrics and unsupported claims result in refusal or clarification. A caller must not supplement a
refusal with model memory.

No RAG architecture guarantees zero error. Quality is maintained with real-source Golden Sets,
negative tests, content hashes, data versions, source preview and live-model behavior evaluation.

## Concurrency

- FastAPI semaphore and queue timeout return controlled HTTP 503 under overload.
- LLM and embedding calls have independent bounded concurrency.
- LLM HTTP connections and external SQL/API providers use connection pools.
- External provider failure is isolated when strict mode is false.
- Production worker count and limits must be capacity-tested against every downstream service.

## Security and deployment

- Public MCP supports optional static Bearer authentication for private deployments.
- Internet-facing deployments should terminate OAuth/OIDC at an authenticated gateway.
- Internal MCP ports, Neo4j, Ollama and databases remain private.
- API tenant headers are compatibility placeholders, not production IAM.
- Local mapping and secret files are gitignored.
- TLS, secret rotation, audit retention and tenant authorization belong to the deployment platform.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the operational topology and
[docs/VERIFIED_RAG_MCP_OUTPUT_SPEC.md](docs/VERIFIED_RAG_MCP_OUTPUT_SPEC.md) for the public contract.
External MCP Servers use an explicit allowlist and typed Evidence adapter; they are not dynamically
trusted from a URL. See [docs/ADDING_EXTERNAL_MCP.md](docs/ADDING_EXTERNAL_MCP.md).

## Change policy

Additive optional fields may use a minor schema version. Removing/renaming fields, making an
optional field required, changing status semantics or changing source allowlists is breaking and
requires a major version or a new tool name. Every change requires regenerated OpenAPI, full tests,
Golden Sets and deployment smoke tests.
