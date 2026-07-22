# Financial Data Schema v2

> 文件層級：內部資料契約附錄。產品主規格請見 [PROJECT_SPEC.md](PROJECT_SPEC.md)。

- Schema version: `2`
- Status: implemented with legacy read compatibility
- Public MCP impact: none; normalized data still emits the stable Evidence contract
- Canonical company key: `co_code`

## 1. Purpose

Real financial providers commonly return hundreds or thousands of dynamic metric keys rather than a
fixed `metric/value` list. This schema accepts flat or nested key/value statements while preserving
exact values, provider semantics, reporting dimensions, revisions and raw input.

Example provider input:

```json
{
  "ticker": "2330",
  "fiscal_period": "2026Q1",
  "statements": {
    "income_statement": {
      "營業收入合計": {"value": "839253000000", "unit": "TWD"},
      "營業毛利（毛損）": {"value": "477811000000", "unit": "TWD"},
      "基本每股盈餘": {"value": "13.94", "unit": "TWD_PER_SHARE"}
    },
    "balance_sheet": {
      "資產總計": {"value": "6821000000000", "unit": "TWD"}
    }
  }
}
```

Provider keys never become trusted answer semantics automatically. They pass through an approved
Metric Dictionary and Provider Mapping first.

## 2. Four-layer model

```text
Provider JSON / DB record / XBRL
    -> financial_raw_payloads
    -> financial_metric_definitions + provider_metric_mappings
    -> financial_facts
    -> Evidence / Citation / MCP response
```

### 2.1 `financial_raw_payloads`

Preserves the normalization input so mappings can be rerun later.

| Column | Type | Required | Meaning |
|---|---|---:|---|
| `payload_id` | text | yes | Stable hash-based raw payload ID |
| `provider_id` | text | yes | Provider namespace |
| `co_code` | text | yes | Canonical company code |
| `period` | text | yes | Normalized fiscal period |
| `payload_json` | text/JSON | yes | Original normalization payload |
| `captured_at` | ISO 8601 | yes | Capture time |
| `content_hash` | SHA-256 | yes | Canonical payload hash |
| `data_version` | text | yes | Provider revision/accession |
| `source_id` | text | yes | Provenance source |
| `schema_version` | integer | yes | Currently `2` |

Large SEC/source originals may also be stored as raw files; `payload_json` then preserves the exact
selected normalization input and references the full-source hash and locator.

### 2.2 `financial_metric_definitions`

Defines stable internal metric semantics.

| Column | Meaning |
|---|---|
| `metric_code` | Stable internal code such as `revenue`, `basic_eps`, `total_assets` |
| `display_name` | User-facing name |
| `statement_type` | `income_statement`, `balance_sheet`, `cash_flow`, etc. |
| `data_type` | `monetary`, `percentage`, `per_share`, `ratio`, `count`, `other` |
| `default_unit` | Unit used only when provider semantics explicitly permit it |
| `duration_type` | `instant`, `quarter`, `year_to_date`, `annual`, `unknown` |
| `aliases_json` | Natural-language aliases used by metric resolution |
| `approved` | Only approved definitions may become answerable facts |

Example:

```json
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
```

### 2.3 `provider_metric_mappings`

Maps exact provider keys to approved internal metrics.

| Column | Meaning |
|---|---|
| `provider_id` | Provider namespace |
| `provider_metric_key` | Exact flattened key, e.g. `income_statement.營業收入合計` |
| `metric_code` | Approved internal metric |
| `unit_override` | Explicit provider-specific unit correction |
| `scale` | Decimal multiplier, stored as text |
| `statement_type_override` | Provider-specific statement override |
| `duration_type_override` | Provider-specific duration override |
| `approved` | Mapping approval gate |

No model is allowed to invent or approve this mapping at query time.

### 2.4 `financial_facts`

Stores normalized facts. Exact numeric values and scale are stored as decimal strings, not SQLite
floating point.

| Dimension | Columns |
|---|---|
| Company/period | `co_code`, `fiscal_year`, `fiscal_quarter`, `period`, `period_start`, `period_end` |
| Metric | `metric_code`, `provider_id`, `provider_metric_key` |
| Value | `value_exact`, `unit`, `scale` |
| Semantics | `statement_type`, `duration_type`, `consolidation_scope`, `dimensions_json` |
| Provenance | `source_id`, `raw_payload_id`, `data_version`, `captured_at`, `content_hash` |
| Revision | `fact_id`, `is_current` |

Logical fact identity includes at least:

```text
co_code + period + metric_code + provider_id + provider_metric_key
+ unit + statement_type + duration_type + consolidation_scope + dimensions
```

`data_version` and value participate in the immutable `fact_id`. A newer record with the same
logical identity marks the prior revision `is_current=0`; history remains queryable for audit.

## 3. Dynamic key normalization

The normalizer supports nested objects and scalar or `{value, unit}` leaves. Keys are flattened with
dots:

```text
statements.income_statement.營業收入合計
```

When `metrics_path=statements`, the mapping key is relative to that object:

```text
income_statement.營業收入合計
```

Rules:

1. Preserve the raw payload and hash first.
2. Flatten numeric leaves only.
3. Require exact approved provider mapping.
4. Require an approved metric definition.
5. Apply explicit decimal scale and unit rules.
6. Emit `FinancialFact` only when semantics are complete.
7. Return unknown keys in `unmapped_metric_keys` without guessing.

Unknown keys are therefore observable and remappable later, but cannot enter a verified answer.

## 4. Period, unit and scope rules

- `period` is the canonical fiscal identifier, e.g. `2026Q1`.
- `duration_type` distinguishes instant, single-quarter, year-to-date and annual values.
- `consolidation_scope` distinguishes consolidated, standalone or other approved scopes.
- `statement_type` prevents identically named concepts from unrelated statements being conflated.
- `value_exact` is the post-scale exact decimal string.
- `unit` is mandatory on an answerable fact.
- Additional segment, geography, product or accounting dimensions belong in `dimensions_json`.

The system must not compare or aggregate facts with incompatible unit, duration, consolidation or
dimensions without a separately tested deterministic calculation rule.

## 5. Evidence mapping

Financial Facts become `source_type=database` Evidence:

```json
{
  "evidence_id": "ev-db-fact:...",
  "co_code": "2330",
  "source_id": "filing-2330-2026q1",
  "source_type": "database",
  "period": "2026Q1",
  "locator": {
    "table": "financial_facts",
    "primary_key": "fact:...",
    "columns": [
      "metric_code",
      "value_exact",
      "unit",
      "statement_type",
      "duration_type",
      "consolidation_scope"
    ]
  },
  "metadata": {
    "metric_code": "revenue",
    "provider_metric_key": "income_statement.營業收入合計",
    "value": 839253000000,
    "value_exact": "839253000000",
    "unit": "TWD",
    "statement_type": "income_statement",
    "duration_type": "quarter",
    "consolidation_scope": "consolidated"
  }
}
```

`metadata.value` remains numeric for existing validation/calculation compatibility;
`metadata.value_exact` is authoritative for precision and display-sensitive processing.

## 6. Compatibility

The legacy `financial_metrics` table remains readable. `SQLiteFinanceRepository` behavior:

1. Use current `financial_facts` when v2 facts exist for the requested company/period.
2. Fall back to legacy `financial_metrics` when no v2 fact exists.
3. Include periods from both tables during migration.
4. Return source previews from the table actually used.

New ingestion should write v2. Legacy writers can be migrated incrementally without changing the
public Financial MCP.

## 7. External API modes

The REST adapter supports:

- row mode: each response row already contains `metric` and `value`;
- dynamic mode: each company/period row contains a nested key/value statement object.

Dynamic mode uses `dynamic_metric_mapping`, `metric_definitions` and
`provider_metric_mappings` from the approved registry. See
[EXTERNAL_INTEGRATION_GUIDE.md](EXTERNAL_INTEGRATION_GUIDE.md).

## 8. Query strategy

Structured numeric facts are queried deterministically by company, period, metric and dimensions.
Embedding is used for earnings-call transcript chunks, not to resolve or choose a financial value.
Metric aliases and structured facts use deterministic mappings and SQL queries.

Before the global Evidence limit is applied, database facts are deterministically ranked against the
question using approved `metric_code`, display name, aliases and provider key. This prevents an
arbitrary first-N subset from hiding the requested metric when a period contains hundreds of facts.

## 9. Acceptance criteria

- Nested and flat dynamic keys normalize correctly.
- Unknown keys are preserved and excluded from answerable facts.
- Decimal precision survives storage and retrieval.
- Wrong-company and wrong-period rows are rejected.
- Different unit/scope/duration facts do not overwrite each other.
- A new revision preserves history and becomes the only current version.
- Evidence locator and Source Preview recover the normalized/raw record.
- Legacy periods continue working during migration.
- Financial MCP source allowlist remains unchanged.
