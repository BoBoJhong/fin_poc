from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


FINANCIAL_SCHEMA_VERSION = 2

FINANCIAL_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS financial_raw_payloads (
    payload_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    co_code TEXT NOT NULL,
    period TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    data_version TEXT NOT NULL,
    source_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 2,
    FOREIGN KEY (co_code) REFERENCES companies(co_code),
    FOREIGN KEY (source_id) REFERENCES data_sources(source_id)
);

CREATE INDEX IF NOT EXISTS idx_raw_payload_scope
    ON financial_raw_payloads (provider_id, co_code, period, data_version);

CREATE TABLE IF NOT EXISTS financial_metric_definitions (
    metric_code TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    statement_type TEXT NOT NULL,
    data_type TEXT NOT NULL,
    default_unit TEXT,
    duration_type TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    approved INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_metric_mappings (
    provider_id TEXT NOT NULL,
    provider_metric_key TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    unit_override TEXT,
    scale TEXT NOT NULL DEFAULT '1',
    statement_type_override TEXT,
    duration_type_override TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider_id, provider_metric_key),
    FOREIGN KEY (metric_code) REFERENCES financial_metric_definitions(metric_code)
);

CREATE TABLE IF NOT EXISTS financial_facts (
    fact_id TEXT PRIMARY KEY,
    co_code TEXT NOT NULL,
    fiscal_year INTEGER,
    fiscal_quarter INTEGER,
    period TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    metric_code TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    provider_metric_key TEXT NOT NULL,
    value_exact TEXT NOT NULL,
    unit TEXT NOT NULL,
    scale TEXT NOT NULL DEFAULT '1',
    statement_type TEXT NOT NULL,
    duration_type TEXT NOT NULL,
    consolidation_scope TEXT NOT NULL,
    dimensions_json TEXT NOT NULL DEFAULT '{}',
    source_id TEXT NOT NULL,
    raw_payload_id TEXT,
    data_version TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (co_code) REFERENCES companies(co_code),
    FOREIGN KEY (metric_code) REFERENCES financial_metric_definitions(metric_code),
    FOREIGN KEY (source_id) REFERENCES data_sources(source_id),
    FOREIGN KEY (raw_payload_id) REFERENCES financial_raw_payloads(payload_id)
);

CREATE INDEX IF NOT EXISTS idx_financial_facts_scope
    ON financial_facts (co_code, period, metric_code, is_current);

CREATE INDEX IF NOT EXISTS idx_financial_facts_semantics
    ON financial_facts (
        co_code, period, statement_type, duration_type, consolidation_scope, is_current
    );
"""


class MetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_code: str
    display_name: str
    statement_type: str
    data_type: Literal["monetary", "percentage", "per_share", "ratio", "count", "other"]
    default_unit: str | None = None
    duration_type: Literal["instant", "quarter", "year_to_date", "annual", "unknown"]
    aliases: list[str] = Field(default_factory=list)
    approved: bool = False


class ProviderMetricMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    provider_metric_key: str
    metric_code: str
    unit_override: str | None = None
    scale: Decimal = Decimal("1")
    statement_type_override: str | None = None
    duration_type_override: str | None = None
    approved: bool = False


class FinancialFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    co_code: str
    fiscal_year: int | None = None
    fiscal_quarter: int | None = Field(default=None, ge=1, le=4)
    period: str
    period_start: str | None = None
    period_end: str | None = None
    metric_code: str
    provider_id: str
    provider_metric_key: str
    value_exact: Decimal
    unit: str
    scale: Decimal = Decimal("1")
    statement_type: str
    duration_type: str
    consolidation_scope: str
    dimensions: dict[str, Any] = Field(default_factory=dict)
    source_id: str
    raw_payload_id: str | None = None
    data_version: str
    captured_at: str
    content_hash: str
    is_current: bool = True


class NormalizationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    co_code: str
    period: str
    source_id: str
    data_version: str
    fiscal_year: int | None = None
    fiscal_quarter: int | None = Field(default=None, ge=1, le=4)
    period_start: str | None = None
    period_end: str | None = None
    consolidation_scope: str = "consolidated"
    captured_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    dimensions: dict[str, Any] = Field(default_factory=dict)


class NormalizationResult(BaseModel):
    raw_payload_id: str
    raw_content_hash: str
    facts: list[FinancialFact]
    unmapped_metric_keys: list[str]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()}"


def flatten_metric_values(payload: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten numeric leaves while treating {value, unit} objects as one metric."""

    if not isinstance(payload, dict):
        return {}
    flattened: dict[str, Any] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and "value" in value:
            flattened[path] = value
        elif isinstance(value, dict):
            flattened.update(flatten_metric_values(value, path))
        elif isinstance(value, (int, float, Decimal, str)) and not isinstance(value, bool):
            try:
                Decimal(str(value).replace(",", ""))
            except InvalidOperation:
                continue
            flattened[path] = value
    return flattened


def normalize_financial_payload(
    payload: dict[str, Any],
    context: NormalizationContext,
    definitions: list[MetricDefinition],
    mappings: list[ProviderMetricMapping],
    *,
    metrics_path: str = "data",
) -> NormalizationResult:
    """Normalize approved dynamic provider keys without inferring unknown metric semantics."""

    selected: Any = payload
    if metrics_path:
        for part in metrics_path.split("."):
            selected = selected.get(part) if isinstance(selected, dict) else None
            if selected is None:
                break
    flattened = flatten_metric_values(selected)
    definition_index = {item.metric_code: item for item in definitions if item.approved}
    mapping_index = {
        item.provider_metric_key: item
        for item in mappings
        if item.provider_id == context.provider_id and item.approved
    }
    raw_hash = content_hash(payload)
    raw_payload_id = (
        f"raw:{context.provider_id}:{context.co_code}:{context.period}:"
        f"{raw_hash.removeprefix('sha256:')[:24]}"
    )
    facts: list[FinancialFact] = []
    unmapped: list[str] = []
    for provider_key, raw_node in flattened.items():
        mapping = mapping_index.get(provider_key)
        definition = definition_index.get(mapping.metric_code) if mapping else None
        if mapping is None or definition is None:
            unmapped.append(provider_key)
            continue
        raw_value = raw_node.get("value") if isinstance(raw_node, dict) else raw_node
        value = Decimal(str(raw_value).replace(",", "")) * mapping.scale
        raw_unit = raw_node.get("unit") if isinstance(raw_node, dict) else None
        unit = mapping.unit_override or raw_unit or definition.default_unit
        if not unit:
            unmapped.append(provider_key)
            continue
        statement_type = mapping.statement_type_override or definition.statement_type
        duration_type = mapping.duration_type_override or definition.duration_type
        semantic_identity = {
            "co_code": context.co_code,
            "period": context.period,
            "metric_code": definition.metric_code,
            "provider_id": context.provider_id,
            "provider_metric_key": provider_key,
            "unit": unit,
            "statement_type": statement_type,
            "duration_type": duration_type,
            "consolidation_scope": context.consolidation_scope,
            "dimensions": context.dimensions,
            "data_version": context.data_version,
        }
        fact_hash = content_hash(semantic_identity | {"value_exact": str(value)})
        facts.append(
            FinancialFact(
                fact_id=f"fact:{fact_hash.removeprefix('sha256:')}",
                co_code=context.co_code,
                fiscal_year=context.fiscal_year,
                fiscal_quarter=context.fiscal_quarter,
                period=context.period,
                period_start=context.period_start,
                period_end=context.period_end,
                metric_code=definition.metric_code,
                provider_id=context.provider_id,
                provider_metric_key=provider_key,
                value_exact=value,
                unit=str(unit),
                scale=mapping.scale,
                statement_type=statement_type,
                duration_type=duration_type,
                consolidation_scope=context.consolidation_scope,
                dimensions=context.dimensions,
                source_id=context.source_id,
                raw_payload_id=raw_payload_id,
                data_version=context.data_version,
                captured_at=context.captured_at,
                content_hash=fact_hash,
            )
        )
    return NormalizationResult(
        raw_payload_id=raw_payload_id,
        raw_content_hash=raw_hash,
        facts=facts,
        unmapped_metric_keys=sorted(unmapped),
    )


def persist_normalized_financial_payload(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    context: NormalizationContext,
    definitions: list[MetricDefinition],
    mappings: list[ProviderMetricMapping],
    *,
    metrics_path: str = "data",
) -> NormalizationResult:
    """Persist raw input, dictionary/mapping versions and normalized current facts."""

    result = normalize_financial_payload(
        payload,
        context,
        definitions,
        mappings,
        metrics_path=metrics_path,
    )
    now = datetime.now(UTC).isoformat()
    connection.executescript(FINANCIAL_SCHEMA_V2)
    for definition in definitions:
        connection.execute(
            """
            INSERT INTO financial_metric_definitions
                (metric_code, display_name, statement_type, data_type, default_unit,
                 duration_type, aliases_json, approved, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(metric_code) DO UPDATE SET
                display_name = excluded.display_name,
                statement_type = excluded.statement_type,
                data_type = excluded.data_type,
                default_unit = excluded.default_unit,
                duration_type = excluded.duration_type,
                aliases_json = excluded.aliases_json,
                approved = excluded.approved,
                updated_at = excluded.updated_at
            """,
            (
                definition.metric_code,
                definition.display_name,
                definition.statement_type,
                definition.data_type,
                definition.default_unit,
                definition.duration_type,
                canonical_json(definition.aliases),
                int(definition.approved),
                now,
            ),
        )
    for mapping in mappings:
        connection.execute(
            """
            INSERT INTO provider_metric_mappings
                (provider_id, provider_metric_key, metric_code, unit_override, scale,
                 statement_type_override, duration_type_override, approved, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, provider_metric_key) DO UPDATE SET
                metric_code = excluded.metric_code,
                unit_override = excluded.unit_override,
                scale = excluded.scale,
                statement_type_override = excluded.statement_type_override,
                duration_type_override = excluded.duration_type_override,
                approved = excluded.approved,
                updated_at = excluded.updated_at
            """,
            (
                mapping.provider_id,
                mapping.provider_metric_key,
                mapping.metric_code,
                mapping.unit_override,
                str(mapping.scale),
                mapping.statement_type_override,
                mapping.duration_type_override,
                int(mapping.approved),
                now,
            ),
        )
    connection.execute(
        """
        INSERT INTO financial_raw_payloads
            (payload_id, provider_id, co_code, period, payload_json, captured_at,
             content_hash, data_version, source_id, schema_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(payload_id) DO UPDATE SET
            payload_json = excluded.payload_json,
            captured_at = excluded.captured_at,
            content_hash = excluded.content_hash,
            data_version = excluded.data_version,
            source_id = excluded.source_id,
            schema_version = excluded.schema_version
        """,
        (
            result.raw_payload_id,
            context.provider_id,
            context.co_code,
            context.period,
            canonical_json(payload),
            context.captured_at,
            result.raw_content_hash,
            context.data_version,
            context.source_id,
            FINANCIAL_SCHEMA_VERSION,
        ),
    )
    for fact in result.facts:
        connection.execute(
            """
            UPDATE financial_facts
            SET is_current = 0
            WHERE co_code = ? AND period = ? AND metric_code = ?
              AND provider_id = ? AND provider_metric_key = ? AND unit = ?
              AND statement_type = ? AND duration_type = ?
              AND consolidation_scope = ? AND dimensions_json = ?
            """,
            (
                fact.co_code,
                fact.period,
                fact.metric_code,
                fact.provider_id,
                fact.provider_metric_key,
                fact.unit,
                fact.statement_type,
                fact.duration_type,
                fact.consolidation_scope,
                canonical_json(fact.dimensions),
            ),
        )
        connection.execute(
            """
            INSERT INTO financial_facts
                (fact_id, co_code, fiscal_year, fiscal_quarter, period, period_start,
                 period_end, metric_code, provider_id, provider_metric_key, value_exact,
                 unit, scale, statement_type, duration_type, consolidation_scope,
                 dimensions_json, source_id, raw_payload_id, data_version, captured_at,
                 content_hash, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fact_id) DO UPDATE SET
                value_exact = excluded.value_exact,
                captured_at = excluded.captured_at,
                content_hash = excluded.content_hash,
                is_current = excluded.is_current
            """,
            (
                fact.fact_id,
                fact.co_code,
                fact.fiscal_year,
                fact.fiscal_quarter,
                fact.period,
                fact.period_start,
                fact.period_end,
                fact.metric_code,
                fact.provider_id,
                fact.provider_metric_key,
                str(fact.value_exact),
                fact.unit,
                str(fact.scale),
                fact.statement_type,
                fact.duration_type,
                fact.consolidation_scope,
                canonical_json(fact.dimensions),
                fact.source_id,
                fact.raw_payload_id,
                fact.data_version,
                fact.captured_at,
                fact.content_hash,
                int(fact.is_current),
            ),
        )
    return result
