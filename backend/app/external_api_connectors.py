from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.financial_data import (
    FinancialFact,
    MetricDefinition,
    NormalizationContext,
    ProviderMetricMapping,
    normalize_financial_payload,
)
from app.models import CompanySummary, Evidence, FiscalCalendar, SourceLocator, SourcePreview


logger = logging.getLogger(__name__)


class APICompanyMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_code: str
    company_name: str
    industry: str | None = None
    aliases: str | None = None
    fiscal_year_end_month: str | None = None
    timezone: str | None = None


class APIMetricMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_code: str
    period: str
    metric: str
    value: str
    unit: str | None = None
    scope: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    data_version: str | None = None
    updated_at: str | None = None


class APIDynamicMetricMapping(BaseModel):
    """Maps one period record containing an arbitrary nested metric-key object."""

    model_config = ConfigDict(extra="forbid")

    company_code: str
    period: str
    metrics_path: str
    fiscal_year: str | None = None
    fiscal_quarter: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    consolidation_scope: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    data_version: str | None = None
    updated_at: str | None = None


class APIEndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    items_path: str = ""
    query_params: dict[str, Literal["co_code", "period"]] = Field(default_factory=dict)
    row_limit: int = Field(default=1000, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_path(self) -> "APIEndpointConfig":
        parsed = urlparse(self.path)
        if parsed.scheme or parsed.netloc or not self.path.startswith("/"):
            raise ValueError("External API endpoint path must be an absolute URL path")
        return self


class ExternalAPIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    enabled: bool = True
    approved: bool = False
    base_url: str
    api_key_env: str | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    max_connections: int = Field(default=20, ge=1, le=500)
    verify_tls: bool = True
    max_response_bytes: int = Field(default=5_000_000, ge=1024, le=50_000_000)
    companies: APIEndpointConfig
    company_mapping: APICompanyMapping
    metrics: APIEndpointConfig
    metric_mapping: APIMetricMapping | None = None
    dynamic_metric_mapping: APIDynamicMetricMapping | None = None
    metric_definitions: list[MetricDefinition] = Field(default_factory=list)
    provider_metric_mappings: list[ProviderMetricMapping] = Field(default_factory=list)
    default_unit: str = "UNKNOWN"
    default_scope: str = "external_api"

    @model_validator(mode="after")
    def validate_source(self) -> "ExternalAPIConfig":
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("External API base_url must be HTTP(S)")
        if parsed.username or parsed.password:
            raise ValueError("External API credentials must not be embedded in base_url")
        if self.api_key_env and not self.api_key_env.replace("_", "").isalnum():
            raise ValueError("api_key_env must be an environment-variable name")
        if (self.metric_mapping is None) == (self.dynamic_metric_mapping is None):
            raise ValueError(
                "Configure exactly one of metric_mapping or dynamic_metric_mapping"
            )
        if self.dynamic_metric_mapping and (
            not self.metric_definitions or not self.provider_metric_mappings
        ):
            raise ValueError(
                "Dynamic metrics require metric_definitions and provider_metric_mappings"
            )
        if any(
            item.provider_id != self.id for item in self.provider_metric_mappings
        ):
            raise ValueError("Dynamic provider_metric_mappings provider_id must equal API id")
        return self


class ExternalAPIRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    apis: list[ExternalAPIConfig] = Field(default_factory=list)


def load_external_api_registry(path: Path) -> ExternalAPIRegistry:
    if not path.is_file():
        return ExternalAPIRegistry()
    return ExternalAPIRegistry.model_validate_json(path.read_text(encoding="utf-8"))


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(
        {key: _json_value(item) for key, item in value.items()},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _path_value(value: Any, path: str) -> Any:
    current = value
    if not path:
        return current
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _items(payload: Any, path: str, limit: int) -> list[dict[str, Any]]:
    selected = _path_value(payload, path)
    if not isinstance(selected, list):
        raise RuntimeError(f"External API items_path {path!r} did not resolve to a JSON array")
    rows = [item for item in selected[:limit] if isinstance(item, dict)]
    return rows


class ExternalAPIFinanceRepository:
    """Approved, read-only JSON REST adapter that emits the stable Evidence contract."""

    def __init__(self, config: ExternalAPIConfig, client: httpx.AsyncClient | None = None):
        if not config.approved:
            raise RuntimeError(f"External API {config.id!r} has not been approved")
        self.config = config
        headers = {"Accept": "application/json"}
        if config.api_key_env:
            token = os.getenv(config.api_key_env, "").strip()
            if not token:
                raise RuntimeError(
                    f"External API {config.id!r} requires environment variable "
                    f"{config.api_key_env}."
                )
            value = f"{config.auth_scheme} {token}".strip()
            headers[config.auth_header] = value
        self.client = client or httpx.AsyncClient(
            headers=headers,
            timeout=config.timeout_seconds,
            verify=config.verify_tls,
            limits=httpx.Limits(
                max_connections=config.max_connections,
                max_keepalive_connections=config.max_connections,
            ),
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._previews: dict[tuple[str, str], SourcePreview] = {}
        self._calendars: dict[str, FiscalCalendar] = {}

    async def _get(
        self,
        endpoint: APIEndpointConfig,
        *,
        co_code: str | None = None,
        period: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        values = {"co_code": co_code, "period": period}
        params = {
            remote: values[local]
            for remote, local in endpoint.query_params.items()
            if values[local] is not None
        }
        url = urljoin(self.config.base_url.rstrip("/") + "/", endpoint.path.lstrip("/"))
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        if len(response.content) > self.config.max_response_bytes:
            raise RuntimeError(f"External API {self.config.id!r} response exceeded size limit")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"External API {self.config.id!r} returned invalid JSON") from exc
        return _items(payload, endpoint.items_path, endpoint.row_limit), str(response.url)

    async def list_companies(self) -> list[CompanySummary]:
        rows, _ = await self._get(self.config.companies)
        mapping = self.config.company_mapping
        companies: list[CompanySummary] = []
        for row in rows:
            code = str(_path_value(row, mapping.company_code) or "").strip().upper()
            name = str(_path_value(row, mapping.company_name) or "").strip()
            if not code or not name:
                continue
            raw_aliases = _path_value(row, mapping.aliases) if mapping.aliases else []
            if isinstance(raw_aliases, str):
                aliases = [item.strip() for item in raw_aliases.split(",") if item.strip()]
            elif isinstance(raw_aliases, list):
                aliases = [str(item).strip() for item in raw_aliases if str(item).strip()]
            else:
                aliases = []
            companies.append(
                CompanySummary(
                    co_code=code,
                    company_name=name,
                    industry=(
                        str(_path_value(row, mapping.industry))
                        if mapping.industry and _path_value(row, mapping.industry) is not None
                        else None
                    ),
                    aliases=aliases,
                )
            )
            if mapping.fiscal_year_end_month:
                month = _path_value(row, mapping.fiscal_year_end_month)
                if month is not None:
                    self._calendars[code] = FiscalCalendar(
                        co_code=code,
                        fiscal_year_end_month=int(month),
                        timezone=(
                            str(_path_value(row, mapping.timezone) or "UTC")
                            if mapping.timezone
                            else "UTC"
                        ),
                        source=f"external_api:{self.config.id}",
                    )
        return companies

    async def get_metrics(self, co_code: str, period: str | None = None) -> list[Evidence]:
        rows, request_url = await self._get(
            self.config.metrics, co_code=co_code, period=period
        )
        if self.config.dynamic_metric_mapping is not None:
            return self._dynamic_rows_to_evidence(rows, request_url, co_code, period)
        if self.config.metric_mapping is None:  # guarded by configuration validation
            return []
        mapping = self.config.metric_mapping
        evidence: list[Evidence] = []
        for index, row in enumerate(rows):
            row_code = str(_path_value(row, mapping.company_code) or "").strip().upper()
            row_period = str(_path_value(row, mapping.period) or "").strip()
            if row_code != co_code or (period and row_period != period):
                continue
            metric = str(_path_value(row, mapping.metric) or "").strip()
            raw_value = _path_value(row, mapping.value)
            if not metric or raw_value is None:
                continue
            unit = (
                str(_path_value(row, mapping.unit) or self.config.default_unit)
                if mapping.unit
                else self.config.default_unit
            )
            scope = (
                str(_path_value(row, mapping.scope) or self.config.default_scope)
                if mapping.scope
                else self.config.default_scope
            )
            row_hash = _canonical_hash(row)
            source_id = (
                str(_path_value(row, mapping.source_id))
                if mapping.source_id and _path_value(row, mapping.source_id)
                else f"api:{self.config.id}:{row_hash.removeprefix('sha256:')[:20]}"
            )
            source_url = (
                str(_path_value(row, mapping.source_url))
                if mapping.source_url and _path_value(row, mapping.source_url)
                else request_url
            )
            data_version = (
                str(_path_value(row, mapping.data_version))
                if mapping.data_version and _path_value(row, mapping.data_version) is not None
                else row_hash
            )
            captured_at = (
                str(_path_value(row, mapping.updated_at))
                if mapping.updated_at and _path_value(row, mapping.updated_at) is not None
                else None
            )
            primary_key = f"{row_code}|{row_period}|{metric}|{index}"
            item = Evidence(
                evidence_id=f"ev-api-{self.config.id}-{row_hash.removeprefix('sha256:')[:24]}",
                co_code=row_code,
                source_id=source_id,
                source_type="database",
                title=f"{row_code} {row_period} external API financial metric",
                content=f"{row_period} {metric} = {raw_value} {unit} ({scope})",
                score=1.0,
                period=row_period,
                locator=SourceLocator(
                    table=f"external_api:{self.config.id}",
                    primary_key=primary_key,
                    columns=["co_code", "period", "metric", "value", "unit", "scope"],
                ),
                captured_at=captured_at,
                content_hash=row_hash,
                data_version=data_version,
                metadata={
                    "provider_type": "external_api",
                    "provider_id": self.config.id,
                    "metric_code": metric,
                    "value": raw_value,
                    "unit": unit,
                    "scope": scope,
                    "request_url": request_url,
                },
            )
            evidence.append(item)
            self._previews[(source_id, row_code)] = SourcePreview(
                source_id=source_id,
                co_code=row_code,
                source_type="database",
                title=item.title,
                live_url=source_url,
                text=item.content,
                locator=item.locator,
                captured_at=captured_at,
                content_hash=row_hash,
                database_record={
                    "provider_type": "external_api",
                    "provider_id": self.config.id,
                    "record": row,
                    "data_version": data_version,
                },
            )
        return evidence

    def _dynamic_rows_to_evidence(
        self,
        rows: list[dict[str, Any]],
        request_url: str,
        co_code: str,
        period: str | None,
    ) -> list[Evidence]:
        mapping = self.config.dynamic_metric_mapping
        if mapping is None:
            return []
        evidence: list[Evidence] = []
        for row in rows:
            row_code = str(_path_value(row, mapping.company_code) or "").strip().upper()
            row_period = str(_path_value(row, mapping.period) or "").strip()
            if row_code != co_code or (period and row_period != period):
                continue
            row_hash = _canonical_hash(row)
            source_id = (
                str(_path_value(row, mapping.source_id))
                if mapping.source_id and _path_value(row, mapping.source_id)
                else f"api:{self.config.id}:{row_hash.removeprefix('sha256:')[:20]}"
            )
            source_url = (
                str(_path_value(row, mapping.source_url))
                if mapping.source_url and _path_value(row, mapping.source_url)
                else request_url
            )
            data_version = (
                str(_path_value(row, mapping.data_version))
                if mapping.data_version and _path_value(row, mapping.data_version) is not None
                else row_hash
            )
            captured_at = (
                str(_path_value(row, mapping.updated_at))
                if mapping.updated_at and _path_value(row, mapping.updated_at) is not None
                else datetime.now().astimezone().isoformat()
            )
            scope = (
                str(_path_value(row, mapping.consolidation_scope))
                if mapping.consolidation_scope
                and _path_value(row, mapping.consolidation_scope) is not None
                else "consolidated"
            )
            result = normalize_financial_payload(
                row,
                NormalizationContext(
                    provider_id=self.config.id,
                    co_code=row_code,
                    period=row_period,
                    fiscal_year=(
                        int(_path_value(row, mapping.fiscal_year))
                        if mapping.fiscal_year
                        and _path_value(row, mapping.fiscal_year) is not None
                        else None
                    ),
                    fiscal_quarter=(
                        int(_path_value(row, mapping.fiscal_quarter))
                        if mapping.fiscal_quarter
                        and _path_value(row, mapping.fiscal_quarter) is not None
                        else None
                    ),
                    period_start=(
                        str(_path_value(row, mapping.period_start))
                        if mapping.period_start
                        and _path_value(row, mapping.period_start) is not None
                        else None
                    ),
                    period_end=(
                        str(_path_value(row, mapping.period_end))
                        if mapping.period_end
                        and _path_value(row, mapping.period_end) is not None
                        else None
                    ),
                    consolidation_scope=scope,
                    source_id=source_id,
                    data_version=data_version,
                    captured_at=captured_at,
                ),
                self.config.metric_definitions,
                self.config.provider_metric_mappings,
                metrics_path=mapping.metrics_path,
            )
            definitions = {
                item.metric_code: item for item in self.config.metric_definitions
            }
            evidence.extend(
                self._dynamic_fact_to_evidence(
                    fact,
                    result.unmapped_metric_keys,
                    definitions.get(fact.metric_code),
                )
                for fact in result.facts
            )
            self._previews[(source_id, row_code)] = SourcePreview(
                source_id=source_id,
                co_code=row_code,
                source_type="database",
                title=f"{row_code} {row_period} external API financial facts",
                live_url=source_url,
                text="\n".join(item.content for item in evidence if item.source_id == source_id),
                captured_at=captured_at,
                content_hash=row_hash,
                database_record={
                    "provider_type": "external_api_dynamic",
                    "provider_id": self.config.id,
                    "raw_payload_id": result.raw_payload_id,
                    "record": row,
                    "unmapped_metric_keys": result.unmapped_metric_keys,
                    "data_version": data_version,
                },
            )
        return evidence

    @staticmethod
    def _dynamic_fact_to_evidence(
        fact: FinancialFact,
        unmapped_metric_keys: list[str],
        definition: MetricDefinition | None,
    ) -> Evidence:
        scope = f"{fact.consolidation_scope}_{fact.duration_type}"
        return Evidence(
            evidence_id=f"ev-api-{fact.fact_id}",
            co_code=fact.co_code,
            source_id=fact.source_id,
            source_type="database",
            title=f"{fact.co_code} {fact.period} external API normalized fact",
            content=(
                f"{fact.period} {fact.metric_code} = {fact.value_exact} "
                f"{fact.unit} ({scope})"
            ),
            score=1.0,
            period=fact.period,
            locator=SourceLocator(
                table=f"external_api:{fact.provider_id}:financial_facts",
                primary_key=fact.fact_id,
                columns=[
                    "co_code",
                    "period",
                    "metric_code",
                    "value_exact",
                    "unit",
                    "statement_type",
                    "duration_type",
                    "consolidation_scope",
                ],
            ),
            captured_at=fact.captured_at,
            content_hash=fact.content_hash,
            data_version=fact.data_version,
            metadata={
                "provider_type": "external_api_dynamic",
                "provider_id": fact.provider_id,
                "provider_metric_key": fact.provider_metric_key,
                "metric_code": fact.metric_code,
                "metric_display_name": definition.display_name if definition else fact.metric_code,
                "metric_aliases": definition.aliases if definition else [],
                "value": float(fact.value_exact),
                "value_exact": str(fact.value_exact),
                "unit": fact.unit,
                "scale": str(fact.scale),
                "scope": scope,
                "statement_type": fact.statement_type,
                "duration_type": fact.duration_type,
                "consolidation_scope": fact.consolidation_scope,
                "unmapped_metric_keys": unmapped_metric_keys,
            },
        )

    async def get_source_preview(self, source_id: str, co_code: str) -> SourcePreview | None:
        return self._previews.get((source_id, co_code))

    async def list_periods(self, co_code: str) -> list[str]:
        return sorted({item.period for item in await self.get_metrics(co_code) if item.period})

    async def get_fiscal_calendar(self, co_code: str) -> FiscalCalendar | None:
        if co_code not in self._calendars:
            await self.list_companies()
        return self._calendars.get(co_code)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def build_external_api_repositories(
    path: Path, strict: bool = False
) -> list[ExternalAPIFinanceRepository]:
    registry = load_external_api_registry(path)
    repositories: list[ExternalAPIFinanceRepository] = []
    for api in registry.apis:
        if not api.enabled or not api.approved:
            continue
        try:
            repositories.append(ExternalAPIFinanceRepository(api))
        except Exception as exc:
            if strict:
                raise
            logger.warning("External API %s was not mounted: %s", api.id, exc)
    return repositories
