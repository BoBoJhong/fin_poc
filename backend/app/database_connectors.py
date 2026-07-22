from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from dotenv import dotenv_values
from sqlalchemy import MetaData, Table, and_, create_engine, inspect, select
from sqlalchemy.engine import Engine

from app.models import (
    CompanySummary,
    Evidence,
    FiscalCalendar,
    SourceLocator,
    SourcePreview,
    SourceType,
)


logger = logging.getLogger(__name__)
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
PROJECT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def resolve_environment_value(name: str, env_file: Path = PROJECT_ENV_FILE) -> str:
    """Resolve dynamic registry secrets without copying them into settings or logs."""
    value = os.getenv(name)
    if value is not None:
        return value.strip()
    if not env_file.is_file():
        return ""
    file_value = dotenv_values(env_file).get(name)
    return str(file_value).strip() if file_value is not None else ""


class MetricColumnMapping(BaseModel):
    """Maps one arbitrary financial fact table to the stable Evidence contract."""

    model_config = ConfigDict(extra="forbid")

    company_code: str
    period: str
    metric: str
    value: str
    company_name: str | None = None
    industry: str | None = None
    unit: str | None = None
    scope: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    data_version: str | None = None
    updated_at: str | None = None
    primary_key: list[str] = Field(default_factory=list)


class ExternalDatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    table: str
    schema_name: str | None = None
    approved: bool = False
    mapping: MetricColumnMapping
    default_unit: str = "UNKNOWN"
    default_scope: str = "external_database"
    row_limit: int = Field(default=1000, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_identifiers(self) -> "ExternalDatasetConfig":
        if not SAFE_ID.fullmatch(self.id):
            raise ValueError(f"Unsafe dataset id: {self.id!r}")
        if not self.table.strip():
            raise ValueError("Mapped table name cannot be empty")
        return self


class CompanyColumnMapping(BaseModel):
    """Maps a company master without assuming the vendor's table names."""

    model_config = ConfigDict(extra="forbid")

    company_code: str
    company_name: str
    industry: str | None = None
    aliases: str | None = None
    fiscal_year_end_month: str | None = None
    timezone: str | None = None
    primary_key: list[str] = Field(default_factory=list)


class ExternalCompanyDatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    table: str
    schema_name: str | None = None
    approved: bool = False
    mapping: CompanyColumnMapping
    row_limit: int = Field(default=10000, ge=1, le=100000)

    @model_validator(mode="after")
    def validate_identifiers(self) -> "ExternalCompanyDatasetConfig":
        if not SAFE_ID.fullmatch(self.id):
            raise ValueError(f"Unsafe company dataset id: {self.id!r}")
        if not self.table.strip():
            raise ValueError("Mapped table name cannot be empty")
        return self


class NarrativeColumnMapping(BaseModel):
    """Maps approved internal narrative text that may be copied and embedded."""

    model_config = ConfigDict(extra="forbid")

    company_code: str
    text: str
    title: str | None = None
    period: str | None = None
    source_id: str | None = None
    data_version: str | None = None
    updated_at: str | None = None
    primary_key: list[str] = Field(default_factory=list)


class ExternalNarrativeDatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    table: str
    schema_name: str | None = None
    approved: bool = False
    mapping: NarrativeColumnMapping
    default_title: str = "Internal financial narrative"
    source_type: str = "financial_report"
    row_limit: int = Field(default=1000, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_identifiers(self) -> "ExternalNarrativeDatasetConfig":
        if not SAFE_ID.fullmatch(self.id):
            raise ValueError(f"Unsafe narrative dataset id: {self.id!r}")
        if not self.table.strip():
            raise ValueError("Mapped table name cannot be empty")
        if self.source_type not in {"financial_report", "url"}:
            raise ValueError("Narrative source_type must be financial_report or url")
        return self


class ExternalDatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    enabled: bool = True
    url_env: str
    connect_args: dict[str, Any] = Field(default_factory=dict)
    pool_size: int = Field(default=5, ge=1, le=100)
    max_overflow: int = Field(default=10, ge=0, le=200)
    pool_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    datasets: list[ExternalDatasetConfig] = Field(default_factory=list)
    company_datasets: list[ExternalCompanyDatasetConfig] = Field(default_factory=list)
    narrative_datasets: list[ExternalNarrativeDatasetConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source(self) -> "ExternalDatabaseConfig":
        if not SAFE_ID.fullmatch(self.id):
            raise ValueError(f"Unsafe database id: {self.id!r}")
        if not self.url_env or not self.url_env.replace("_", "").isalnum():
            raise ValueError("url_env must be an environment-variable name")
        all_datasets = [*self.datasets, *self.company_datasets, *self.narrative_datasets]
        if len({item.id for item in all_datasets}) != len(all_datasets):
            raise ValueError(f"Duplicate dataset id in database {self.id}")
        return self


class ExternalDatabaseRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    databases: list[ExternalDatabaseConfig] = Field(default_factory=list)


def load_external_database_registry(path: Path) -> ExternalDatabaseRegistry:
    if not path.is_file():
        return ExternalDatabaseRegistry()
    return ExternalDatabaseRegistry.model_validate_json(path.read_text(encoding="utf-8"))


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def _canonical_hash(value: dict[str, Any]) -> str:
    serializable = {key: _json_value(item) for key, item in value.items()}
    payload = json.dumps(serializable, sort_keys=True, ensure_ascii=False, default=str)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _mapped_columns(mapping: BaseModel) -> set[str]:
    columns: set[str] = set()
    for key, value in mapping.model_dump(exclude_none=True).items():
        if key == "primary_key":
            columns.update(value)
        else:
            columns.add(value)
    return columns


def _parse_aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in re.split(r"[,;|、]", raw) if item.strip()]


class NarrativeRecord(BaseModel):
    database_id: str
    dataset_id: str
    schema_name: str | None = None
    table: str
    co_code: str
    source_id: str
    source_type: str
    title: str
    text: str
    period: str | None = None
    primary_key: str
    captured_at: str | None = None
    content_hash: str
    data_version: str


class ExternalSQLFinanceRepository:
    """Read-only SQLAlchemy adapter driven by an explicitly approved column mapping.

    It uses reflected SQLAlchemy tables and bound parameters only. No query text from
    the user or model is ever executed as SQL.
    """

    def __init__(self, config: ExternalDatabaseConfig):
        self.config = config
        url = resolve_environment_value(config.url_env)
        if not url:
            raise RuntimeError(
                f"External database {config.id!r} requires environment variable {config.url_env}."
            )
        approved = [item for item in config.datasets if item.approved]
        if not approved:
            raise RuntimeError(f"External database {config.id!r} has no approved dataset mapping.")
        self.datasets = approved
        self.company_datasets = [item for item in config.company_datasets if item.approved]
        self.engine: Engine = create_engine(
            url,
            connect_args=config.connect_args,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_timeout=config.pool_timeout_seconds,
            pool_pre_ping=True,
            future=True,
        )
        self._tables: dict[str, Table] = {}
        self._preview_cache: dict[tuple[str, str], SourcePreview] = {}
        self._validate_schema()

    def _validate_schema(self) -> None:
        inspector = inspect(self.engine)
        for dataset in [*self.datasets, *self.company_datasets]:
            columns = {
                item["name"]
                for item in inspector.get_columns(dataset.table, schema=dataset.schema_name)
            }
            if not columns:
                raise RuntimeError(f"Mapped table not found: {self.config.id}.{dataset.table}")
            required_columns = _mapped_columns(dataset.mapping)
            missing = sorted(required_columns - columns)
            if missing:
                raise RuntimeError(
                    f"Invalid mapping for {self.config.id}.{dataset.id}; "
                    f"missing columns: {', '.join(missing)}"
                )

    def _table(self, dataset: ExternalDatasetConfig | ExternalCompanyDatasetConfig) -> Table:
        key = dataset.id
        if key not in self._tables:
            self._tables[key] = Table(
                dataset.table,
                MetaData(),
                schema=dataset.schema_name,
                autoload_with=self.engine,
            )
        return self._tables[key]

    @staticmethod
    def _value(row: dict[str, Any], column: str | None, default: Any = None) -> Any:
        return row.get(column, default) if column else default

    def _source_id(self, dataset: ExternalDatasetConfig, row: dict[str, Any]) -> str:
        mapping = dataset.mapping
        raw = self._value(row, mapping.source_id)
        if raw is None:
            keys = mapping.primary_key or [
                mapping.company_code,
                mapping.period,
                mapping.metric,
            ]
            raw = "|".join(str(row.get(column, "")) for column in keys)
        digest = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:24]
        return f"ext-{self.config.id}-{dataset.id}-{digest}"

    def _row_to_evidence(self, dataset: ExternalDatasetConfig, raw_row: dict[str, Any]) -> Evidence:
        row = {key: _json_value(value) for key, value in raw_row.items()}
        mapping = dataset.mapping
        co_code = str(row[mapping.company_code]).strip().upper()
        period = str(row[mapping.period]).strip()
        metric = str(row[mapping.metric]).strip()
        value = _json_value(row[mapping.value])
        unit = str(self._value(row, mapping.unit, dataset.default_unit))
        scope = str(self._value(row, mapping.scope, dataset.default_scope))
        source_id = self._source_id(dataset, row)
        content_hash = _canonical_hash(row)
        version = self._value(row, mapping.data_version) or content_hash
        updated_at = self._value(row, mapping.updated_at)
        keys = mapping.primary_key or [mapping.company_code, mapping.period, mapping.metric]
        primary_key = "|".join(str(row.get(column, "")) for column in keys)
        record_digest = hashlib.sha256(primary_key.encode("utf-8")).hexdigest()[:24]
        mapped_columns: list[str] = []
        for mapped in mapping.model_dump(exclude_none=True).values():
            if isinstance(mapped, list):
                mapped_columns.extend(mapped)
            else:
                mapped_columns.append(str(mapped))
        evidence = Evidence(
            evidence_id=f"ev-{source_id}-{record_digest}",
            co_code=co_code,
            source_id=source_id,
            source_type=SourceType.DATABASE,
            title=f"{co_code} {period} external financial metric ({self.config.id})",
            content=f"{period} {metric} = {value} {unit} ({scope})",
            score=1.0,
            period=period,
            locator=SourceLocator(
                table=(
                    f"{dataset.schema_name}.{dataset.table}"
                    if dataset.schema_name
                    else dataset.table
                ),
                primary_key=primary_key,
                columns=sorted(set(mapped_columns)),
            ),
            captured_at=str(updated_at) if updated_at is not None else None,
            content_hash=content_hash,
            data_version=str(version),
            metadata={
                "database_id": self.config.id,
                "dataset_id": dataset.id,
                "metric_code": metric,
                "value": value,
                "unit": unit,
                "scope": scope,
                "source_url": self._value(row, mapping.source_url),
                "read_only_adapter": True,
            },
        )
        cache_key = (source_id, co_code)
        existing = self._preview_cache.get(cache_key)
        records = [] if existing is None else list(existing.database_record.get("records", []))
        if not any(item.get("primary_key") == primary_key for item in records):
            records.append({"primary_key": primary_key, "record": row})
        self._preview_cache[cache_key] = SourcePreview(
            source_id=source_id,
            co_code=co_code,
            source_type=SourceType.DATABASE,
            title=evidence.title,
            live_url=self._value(row, mapping.source_url)
            or (existing.live_url if existing else None),
            locator=evidence.locator,
            captured_at=evidence.captured_at or (existing.captured_at if existing else None),
            content_hash=_canonical_hash({"records": records}),
            database_record={
                "database_id": self.config.id,
                "dataset_id": dataset.id,
                "table": evidence.locator.table,
                "records": records,
                "data_version": evidence.data_version,
            },
        )
        return evidence

    async def list_companies(self) -> list[CompanySummary]:
        def run() -> list[CompanySummary]:
            companies: dict[str, CompanySummary] = {}
            with self.engine.connect() as connection:
                for dataset in self.company_datasets:
                    table = self._table(dataset)
                    mapping = dataset.mapping
                    statement = select(table).limit(dataset.row_limit)
                    for raw in connection.execute(statement).mappings():
                        row = dict(raw)
                        code = str(row[mapping.company_code]).strip().upper()
                        if not code:
                            continue
                        companies[code] = CompanySummary(
                            co_code=code,
                            company_name=str(row[mapping.company_name]).strip() or code,
                            industry=(
                                str(self._value(row, mapping.industry)).strip()
                                if self._value(row, mapping.industry) is not None
                                else None
                            ),
                            aliases=_parse_aliases(self._value(row, mapping.aliases)),
                        )
                for dataset in self.datasets:
                    table = self._table(dataset)
                    mapping = dataset.mapping
                    selected = [table.c[mapping.company_code]]
                    if mapping.company_name:
                        selected.append(table.c[mapping.company_name])
                    if mapping.industry:
                        selected.append(table.c[mapping.industry])
                    statement = select(*selected).distinct().limit(dataset.row_limit)
                    for raw in connection.execute(statement).mappings():
                        row = dict(raw)
                        code = str(row[mapping.company_code]).strip().upper()
                        if not code:
                            continue
                        name = self._value(row, mapping.company_name, code)
                        industry = self._value(row, mapping.industry)
                        companies.setdefault(
                            code,
                            CompanySummary(
                                co_code=code,
                                company_name=str(name or code),
                                industry=str(industry) if industry is not None else None,
                            ),
                        )
            return sorted(companies.values(), key=lambda item: item.co_code)

        return await asyncio.to_thread(run)

    async def get_metrics(self, co_code: str, period: str | None = None) -> list[Evidence]:
        code = co_code.strip().upper()

        def run() -> list[Evidence]:
            evidence: list[Evidence] = []
            with self.engine.connect() as connection:
                for dataset in self.datasets:
                    table = self._table(dataset)
                    mapping = dataset.mapping
                    filters = [table.c[mapping.company_code] == code]
                    if period is not None:
                        filters.append(table.c[mapping.period] == period)
                    statement = select(table).where(and_(*filters)).limit(dataset.row_limit)
                    rows = connection.execute(statement).mappings()
                    evidence.extend(self._row_to_evidence(dataset, dict(row)) for row in rows)
            return evidence

        return await asyncio.to_thread(run)

    async def get_source_preview(self, source_id: str, co_code: str) -> SourcePreview | None:
        code = co_code.strip().upper()
        cached = self._preview_cache.get((source_id, code))
        if cached:
            return cached
        await self.get_metrics(code)
        return self._preview_cache.get((source_id, code))

    async def list_periods(self, co_code: str) -> list[str]:
        evidence = await self.get_metrics(co_code)
        return sorted({item.period for item in evidence if item.period})

    async def get_fiscal_calendar(self, co_code: str) -> FiscalCalendar | None:
        code = co_code.strip().upper()

        def run() -> FiscalCalendar | None:
            with self.engine.connect() as connection:
                for dataset in self.company_datasets:
                    mapping = dataset.mapping
                    if not mapping.fiscal_year_end_month:
                        continue
                    table = self._table(dataset)
                    statement = select(table).where(table.c[mapping.company_code] == code).limit(1)
                    row = connection.execute(statement).mappings().first()
                    if row is None:
                        continue
                    month = int(row[mapping.fiscal_year_end_month])
                    timezone = self._value(dict(row), mapping.timezone, "UTC")
                    return FiscalCalendar(
                        co_code=code,
                        fiscal_year_end_month=month,
                        timezone=str(timezone or "UTC"),
                        source=f"external_database:{self.config.id}:{dataset.id}",
                    )
            return None

        return await asyncio.to_thread(run)

    async def close(self) -> None:
        await asyncio.to_thread(self.engine.dispose)


class ExternalSQLNarrativeReader:
    """Reads only explicitly approved text columns for controlled Neo4j ingestion."""

    def __init__(self, config: ExternalDatabaseConfig):
        self.config = config
        url = resolve_environment_value(config.url_env)
        if not url:
            raise RuntimeError(
                f"External database {config.id!r} requires environment variable {config.url_env}."
            )
        self.datasets = [item for item in config.narrative_datasets if item.approved]
        if not self.datasets:
            raise RuntimeError(
                f"External database {config.id!r} has no approved narrative mapping."
            )
        self.engine: Engine = create_engine(
            url,
            connect_args=config.connect_args,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_timeout=config.pool_timeout_seconds,
            pool_pre_ping=True,
            future=True,
        )
        self._tables: dict[str, Table] = {}
        inspector = inspect(self.engine)
        for dataset in self.datasets:
            columns = {
                item["name"]
                for item in inspector.get_columns(dataset.table, schema=dataset.schema_name)
            }
            missing = sorted(_mapped_columns(dataset.mapping) - columns)
            if missing:
                raise RuntimeError(
                    f"Invalid narrative mapping for {config.id}.{dataset.id}; "
                    f"missing columns: {', '.join(missing)}"
                )

    def _table(self, dataset: ExternalNarrativeDatasetConfig) -> Table:
        if dataset.id not in self._tables:
            self._tables[dataset.id] = Table(
                dataset.table,
                MetaData(),
                schema=dataset.schema_name,
                autoload_with=self.engine,
            )
        return self._tables[dataset.id]

    def _source_id(self, dataset: ExternalNarrativeDatasetConfig, row: dict[str, Any]) -> str:
        mapping = dataset.mapping
        raw = row.get(mapping.source_id) if mapping.source_id else None
        keys = mapping.primary_key or [mapping.company_code, mapping.text]
        identity = "|".join(str(row.get(column, "")) for column in keys)
        digest = hashlib.sha256(f"{raw or ''}|{identity}".encode("utf-8")).hexdigest()[:24]
        return f"dbdoc-{self.config.id}-{dataset.id}-{digest}"

    def read(self) -> list[NarrativeRecord]:
        records: list[NarrativeRecord] = []
        with self.engine.connect() as connection:
            for dataset in self.datasets:
                table = self._table(dataset)
                mapping = dataset.mapping
                rows = connection.execute(select(table).limit(dataset.row_limit)).mappings()
                for raw in rows:
                    row = {key: _json_value(value) for key, value in dict(raw).items()}
                    text = str(row.get(mapping.text) or "").strip()
                    code = str(row.get(mapping.company_code) or "").strip().upper()
                    if not text or not code:
                        continue
                    keys = mapping.primary_key or [mapping.company_code, mapping.text]
                    primary_key = "|".join(str(row.get(column, "")) for column in keys)
                    content_hash = _canonical_hash(row)
                    title = row.get(mapping.title) if mapping.title else None
                    version = row.get(mapping.data_version) if mapping.data_version else None
                    captured_at = row.get(mapping.updated_at) if mapping.updated_at else None
                    period = row.get(mapping.period) if mapping.period else None
                    records.append(
                        NarrativeRecord(
                            database_id=self.config.id,
                            dataset_id=dataset.id,
                            schema_name=dataset.schema_name,
                            table=dataset.table,
                            co_code=code,
                            source_id=self._source_id(dataset, row),
                            source_type=dataset.source_type,
                            title=str(title or dataset.default_title),
                            text=text,
                            period=str(period).strip() if period is not None else None,
                            primary_key=primary_key,
                            captured_at=str(captured_at) if captured_at is not None else None,
                            content_hash=content_hash,
                            data_version=str(version or content_hash),
                        )
                    )
        return records

    def close(self) -> None:
        self.engine.dispose()


class CompositeFinanceRepository:
    """Aggregates trusted repositories without letting an unavailable DB break others."""

    def __init__(self, repositories: list[Any], strict: bool = False):
        self.repositories = repositories
        self.strict = strict

    async def _safe(self, repository: Any, method: str, *args: Any) -> Any:
        try:
            return await getattr(repository, method)(*args)
        except Exception as exc:
            if self.strict:
                raise
            logger.warning("Skipping unavailable finance repository %s: %s", repository, exc)
            return None if method in {"get_source_preview", "get_fiscal_calendar"} else []

    async def list_companies(self) -> list[CompanySummary]:
        results = await asyncio.gather(
            *(self._safe(repo, "list_companies") for repo in self.repositories)
        )
        companies: dict[str, CompanySummary] = {}
        for items in results:
            for item in items or []:
                current = companies.get(item.co_code)
                if current is None:
                    companies[item.co_code] = item
                elif item.company_name != item.co_code and current.company_name == current.co_code:
                    companies[item.co_code] = item
        return sorted(companies.values(), key=lambda item: item.co_code)

    async def get_metrics(self, co_code: str, period: str | None = None) -> list[Evidence]:
        results = await asyncio.gather(
            *(self._safe(repo, "get_metrics", co_code, period) for repo in self.repositories)
        )
        unique: dict[str, Evidence] = {}
        for items in results:
            for item in items or []:
                unique[item.evidence_id] = item
        return list(unique.values())

    async def get_source_preview(self, source_id: str, co_code: str) -> SourcePreview | None:
        for repository in self.repositories:
            preview = await self._safe(repository, "get_source_preview", source_id, co_code)
            if preview is not None:
                return preview
        return None

    async def list_periods(self, co_code: str) -> list[str]:
        results = await asyncio.gather(
            *(self._safe(repo, "list_periods", co_code) for repo in self.repositories)
        )
        return sorted({period for items in results for period in (items or [])})

    async def get_fiscal_calendar(self, co_code: str) -> FiscalCalendar | None:
        for repository in self.repositories:
            calendar = await self._safe(repository, "get_fiscal_calendar", co_code)
            if calendar is not None:
                return calendar
        return None

    async def close(self) -> None:
        await asyncio.gather(
            *(
                repository.close()
                for repository in self.repositories
                if hasattr(repository, "close")
            )
        )


def build_external_repositories(
    path: Path, strict: bool = False
) -> list[ExternalSQLFinanceRepository]:
    registry = load_external_database_registry(path)
    repositories: list[ExternalSQLFinanceRepository] = []
    for database in registry.databases:
        if not database.enabled:
            continue
        try:
            repositories.append(ExternalSQLFinanceRepository(database))
        except Exception as exc:
            if strict:
                raise
            logger.warning("External database %s was not mounted: %s", database.id, exc)
    return repositories


def discover_database(url: str) -> dict[str, Any]:
    """Return a credential-free schema report and conservative mapping suggestions."""

    engine = create_engine(url, future=True)
    inspector = inspect(engine)
    aliases = {
        "company_code": (
            "co_code",
            "company_code",
            "ticker",
            "ticker_symbol",
            "symbol",
            "stock_code",
        ),
        "company_name": ("company_name", "issuer_name", "name"),
        "industry": ("industry", "sector"),
        "period": (
            "period",
            "fiscal_period",
            "fiscal_quarter",
            "quarter",
            "reporting_period",
        ),
        "metric": ("metric_code", "metric", "metric_name", "measure_name", "concept"),
        "value": ("value", "metric_value", "measure_value", "amount", "numeric_value"),
        "unit": ("unit", "measure_unit", "currency", "uom"),
        "scope": ("scope", "statement_scope", "consolidation_scope"),
        "source_id": ("source_id", "filing_id", "accession_id", "document_id"),
        "source_url": ("source_url", "document_url", "filing_url", "url"),
        "data_version": ("data_version", "version", "revision"),
        "updated_at": ("updated_at", "captured_at", "loaded_at", "created_at"),
        "aliases": ("aliases", "alias", "company_aliases", "other_names"),
        "fiscal_year_end_month": ("fiscal_year_end_month", "fy_end_month"),
        "timezone": ("timezone", "time_zone", "tz"),
        "text": ("content", "text", "body", "narrative", "description", "summary"),
        "title": ("title", "document_title", "subject", "heading"),
    }
    try:
        tables: list[dict[str, Any]] = []
        for schema_name in inspector.get_schema_names():
            if schema_name in {"information_schema", "pg_catalog", "mysql", "performance_schema"}:
                continue
            for table_name in inspector.get_table_names(schema=schema_name):
                columns = inspector.get_columns(table_name, schema=schema_name)
                primary_key = inspector.get_pk_constraint(table_name, schema=schema_name).get(
                    "constrained_columns", []
                )
                foreign_keys = inspector.get_foreign_keys(table_name, schema=schema_name)
                indexes = inspector.get_indexes(table_name, schema=schema_name)
                names = {str(column["name"]).lower(): str(column["name"]) for column in columns}
                suggestion = {
                    target: next((names[name] for name in candidates if name in names), None)
                    for target, candidates in aliases.items()
                }
                required = ("company_code", "period", "metric", "value")
                score = sum(bool(suggestion[name]) for name in required)
                tables.append(
                    {
                        "schema_name": schema_name,
                        "table": table_name,
                        "columns": [
                            {
                                "name": column["name"],
                                "type": str(column["type"]),
                                "nullable": bool(column.get("nullable", True)),
                            }
                            for column in columns
                        ],
                        "primary_key": primary_key,
                        "foreign_keys": [
                            {
                                "name": item.get("name"),
                                "constrained_columns": item.get("constrained_columns", []),
                                "referred_schema": item.get("referred_schema"),
                                "referred_table": item.get("referred_table"),
                                "referred_columns": item.get("referred_columns", []),
                            }
                            for item in foreign_keys
                        ],
                        "indexes": [
                            {
                                "name": item.get("name"),
                                "columns": item.get("column_names", []),
                                "unique": bool(item.get("unique", False)),
                            }
                            for item in indexes
                        ],
                        "mapping_suggestion": suggestion,
                        "required_mapping_score": f"{score}/4",
                        "ready_for_review": score == 4,
                        "company_mapping_ready_for_review": bool(
                            suggestion["company_code"] and suggestion["company_name"]
                        ),
                        "narrative_mapping_ready_for_review": bool(
                            suggestion["company_code"] and suggestion["text"]
                        ),
                    }
                )
        tables.sort(key=lambda item: item["required_mapping_score"], reverse=True)
        return {
            "dialect": engine.dialect.name,
            "driver": engine.dialect.driver,
            "tables": tables,
            "credentials_included": False,
        }
    finally:
        engine.dispose()


def build_registry_draft(report: dict[str, Any], database_id: str, url_env: str) -> dict[str, Any]:
    """Build a disabled-by-approval registry draft from high-confidence table matches."""

    datasets: list[dict[str, Any]] = []
    company_datasets: list[dict[str, Any]] = []
    narrative_datasets: list[dict[str, Any]] = []
    for table in report.get("tables", []):
        base_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", table["table"])
        if table.get("ready_for_review"):
            mapping = {
                key: value
                for key, value in table["mapping_suggestion"].items()
                if value is not None and key in MetricColumnMapping.model_fields
            }
            mapping["primary_key"] = table.get("primary_key") or [
                mapping["company_code"],
                mapping["period"],
                mapping["metric"],
            ]
            datasets.append(
                {
                    "id": f"{base_id}_metrics",
                    "table": table["table"],
                    "schema_name": table.get("schema_name"),
                    "approved": False,
                    "mapping": mapping,
                    "default_unit": "UNKNOWN",
                    "default_scope": "external_database",
                    "row_limit": 1000,
                }
            )
        if table.get("company_mapping_ready_for_review"):
            mapping = {
                key: value
                for key, value in table["mapping_suggestion"].items()
                if value is not None and key in CompanyColumnMapping.model_fields
            }
            mapping["primary_key"] = table.get("primary_key") or [mapping["company_code"]]
            company_datasets.append(
                {
                    "id": f"{base_id}_companies",
                    "table": table["table"],
                    "schema_name": table.get("schema_name"),
                    "approved": False,
                    "mapping": mapping,
                    "row_limit": 10000,
                }
            )
        if table.get("narrative_mapping_ready_for_review"):
            mapping = {
                key: value
                for key, value in table["mapping_suggestion"].items()
                if value is not None and key in NarrativeColumnMapping.model_fields
            }
            mapping["primary_key"] = table.get("primary_key") or [
                mapping["company_code"],
                mapping["text"],
            ]
            narrative_datasets.append(
                {
                    "id": f"{base_id}_narratives",
                    "table": table["table"],
                    "schema_name": table.get("schema_name"),
                    "approved": False,
                    "mapping": mapping,
                    "default_title": "Internal financial narrative",
                    "source_type": "financial_report",
                    "row_limit": 1000,
                }
            )
    draft = {
        "version": 1,
        "databases": [
            {
                "id": database_id,
                "enabled": True,
                "url_env": url_env,
                "connect_args": {},
                "pool_size": 5,
                "max_overflow": 10,
                "pool_timeout_seconds": 10.0,
                "datasets": datasets,
                "company_datasets": company_datasets,
                "narrative_datasets": narrative_datasets,
            }
        ],
    }
    return ExternalDatabaseRegistry.model_validate(draft).model_dump(mode="json")
