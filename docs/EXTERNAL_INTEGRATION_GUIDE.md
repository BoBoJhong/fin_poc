# External Data and Model Integration Guide

> 文件層級：內部整合附錄。產品整合原則請見 [PROJECT_SPEC.md](PROJECT_SPEC.md)；新增其他 MCP
> 請見 [ADDING_EXTERNAL_MCP.md](ADDING_EXTERNAL_MCP.md)。

This project accepts three external integration types without changing its public MCP contract:

1. OpenAI-compatible answer/model API.
2. Approved read-only SQL database.
3. Approved read-only JSON REST financial API.

An external MCP Server is not accepted through a URL-only configuration. It requires an explicit
Tool allowlist, typed adapter and Evidence validation, or it remains a separate MCP registered by
the calling Agent. See [ADDING_EXTERNAL_MCP.md](ADDING_EXTERNAL_MCP.md) for the complete decision
and implementation guide.

All provider-specific records must be normalized into the stable `CompanySummary`,
`FiscalCalendar`, `Evidence` and `SourcePreview` contracts before retrieval.

## 1. OpenAI-compatible LLM API

Configure:

```dotenv
COMPANY_LLM_MODE=openai_compatible
COMPANY_LLM_BASE_URL=https://llm.example.com/v1
COMPANY_LLM_API_KEY=replace-me
COMPANY_LLM_MODEL=deployed-model-name
```

The provider must expose an OpenAI-compatible Chat Completions API. After configuration:

```bash
curl http://127.0.0.1:8000/health/readiness
cd backend
../.venv/bin/python -m scripts.evaluate_llm_behavior --live-llm
```

Do not approve `ask_financial_rag` or `ask_earnings_call` for production until the live-model
behavior set, timeout and rate-limit tests pass. Evidence-only tools do not require this API.

## 2. External SQL database

Use a database-side read-only account. Credentials belong only in an environment variable.

```bash
export INTERNAL_FINANCE_DATABASE_URL='mariadb+pymysql://readonly:secret@db/finance?charset=utf8mb4'
cd backend
../.venv/bin/python -m scripts.discover_database \
  --url-env INTERNAL_FINANCE_DATABASE_URL \
  --database-id internal_finance_db \
  --output ../data/local/finance-db-schema.json \
  --config-output ../config/external_databases.local.json
```

Review the generated mapping, then set `approved: true` only after checking company, period,
metric, value, unit, source and version semantics. Configure:

```dotenv
EXTERNAL_DATABASE_CONFIG_PATH=config/external_databases.local.json
EXTERNAL_DATABASE_STRICT=true
FINANCE_REPOSITORY_MODE=external
INTERNAL_FINANCE_DATABASE_URL=...
```

The adapter executes reflected, parameter-bound `SELECT` statements only. It never accepts SQL
from users or models.

For the current internal deployment, keep `narrative_datasets` empty: MariaDB rows are not
embedded. Only earnings-call transcripts are chunked and embedded in Neo4j. See
[INTERNAL_DATABASE_QUICKSTART.md](INTERNAL_DATABASE_QUICKSTART.md).

The built-in SQL registry expects long-form facts (`company`, `period`, `metric`, `value`). For a
wide table where every column is a financial indicator, create a reviewed read-only database view
that unpivots columns to long form, or implement a dedicated Financial Schema v2 adapter. Do not let
a model infer column semantics at query time.

## 3. External JSON REST financial API

Copy the registry template:

```bash
cp config/external_apis.example.json config/external_apis.local.json
```

Configure the registry path and secret:

```dotenv
EXTERNAL_API_CONFIG_PATH=config/external_apis.local.json
EXTERNAL_API_STRICT=false
VENDOR_FINANCE_API_KEY=replace-me
```

The local file is gitignored. Keep `approved: false` while adapting and testing. The adapter:

- permits configured `GET` endpoints only;
- rejects endpoint URLs that can override the approved host;
- sends credentials from `api_key_env`, never from the JSON mapping;
- does not follow redirects;
- enforces timeout, connection and response-size limits;
- filters returned rows again by `co_code` and period;
- hashes each raw record and preserves provider, version and source URL;
- emits `source_type=database`, so it can only enter the Financial MCP.

### Supported financial response shapes

Row mode is used when every API item already contains `metric` and `value` fields. Configure
`metric_mapping` as before.

Dynamic mode is used when one company/period item contains many indicator keys:

```json
{
  "ticker": "2330",
  "fiscal_period": "2026Q1",
  "statements": {
    "income_statement": {
      "營業收入合計": {"value": "839253000000", "unit": "TWD"},
      "基本每股盈餘": {"value": "13.94", "unit": "TWD_PER_SHARE"}
    }
  }
}
```

For dynamic mode set `metric_mapping: null` and configure:

```json
{
  "dynamic_metric_mapping": {
    "company_code": "ticker",
    "period": "fiscal_period",
    "metrics_path": "statements",
    "fiscal_year": "fiscal_year",
    "fiscal_quarter": "fiscal_quarter",
    "consolidation_scope": "scope",
    "source_id": "source.id",
    "source_url": "source.url",
    "data_version": "revision"
  },
  "metric_definitions": [
    {
      "metric_code": "revenue",
      "display_name": "營業收入",
      "statement_type": "income_statement",
      "data_type": "monetary",
      "default_unit": "TWD",
      "duration_type": "quarter",
      "aliases": ["營收", "營業收入", "revenue"],
      "approved": true
    }
  ],
  "provider_metric_mappings": [
    {
      "provider_id": "vendor_finance_api",
      "provider_metric_key": "income_statement.營業收入合計",
      "metric_code": "revenue",
      "scale": "1",
      "approved": true
    }
  ]
}
```

`items_path` and mapping fields support dot-separated object paths. The selected items value must
be a JSON array. `query_params` maps remote parameter names to the fixed local values `co_code`
or `period`; it cannot inject arbitrary user parameters. Dynamic metric keys are flattened relative
to `metrics_path`. Unmapped keys are returned in provenance metadata and preserved in the raw API
record, but they cannot become verified Evidence.

The complete executable registry is
[`config/external_apis.example.json`](../config/external_apis.example.json). Financial semantics,
precision and revision rules are defined in [FINANCIAL_DATA_SPEC.md](FINANCIAL_DATA_SPEC.md).

### Approval checklist

- Company codes use the same canonical stock code as existing data.
- Fiscal period is normalized, for example `2026Q1`.
- Metric names and units are semantically stable.
- Every answerable dynamic key has an approved definition and provider mapping.
- Unknown keys are visible in `unmapped_metric_keys` and excluded from answers.
- The provider returns one attributable source ID or source URL per fact.
- Revision/version changes are observable.
- Wrong-company rows are included in a negative isolation test.
- Future/unavailable periods refuse rather than falling back to another period.
- Source preview can recover the normalized raw record.

Then change `approved` to `true`, restart the stack and run the full verification commands from
[DEPLOYMENT.md](DEPLOYMENT.md).

## 4. Unsupported external API shapes

Create a dedicated adapter when the provider requires POST queries, signed requests, pagination,
GraphQL, nested arrays, currency conversion or non-trivial fiscal-period conversion. Implement the
`FinanceRepository` protocol in `backend/app/repositories.py`, normalize to `Evidence`, add it to
`build_finance_repository`, and add positive, conflict, isolation, refusal and provenance tests.

Do not weaken the public MCP contract to mirror a vendor's schema.
