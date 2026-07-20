from __future__ import annotations

import re
from datetime import UTC, datetime

from app.models import FiscalCalendar, PeriodResolution


EXACT_PATTERNS = (
    re.compile(r"(?:FY\s*)?(20\d{2})\s*[-_/ ]?Q([1-4])", re.IGNORECASE),
    re.compile(r"Q([1-4])\s*[-_/ ]?(20\d{2})", re.IGNORECASE),
    re.compile(r"(20\d{2})\s*年\s*第?\s*([1-4一二三四])\s*季"),
)
CHINESE_QUARTERS = {"一": "1", "二": "2", "三": "3", "四": "4"}
LATEST_TERMS = ("最近一季", "最新一季", "最近季度", "最新季度", "latest quarter")
PREVIOUS_TERMS = ("上一季", "前一季", "previous quarter", "prior quarter")
YEAR_AGO_TERMS = ("去年同期", "上年同期", "year ago quarter", "same quarter last year")


def canonical_period(value: str) -> str | None:
    for index, pattern in enumerate(EXACT_PATTERNS):
        match = pattern.search(value)
        if not match:
            continue
        if index == 1:
            quarter, year = match.groups()
        else:
            year, quarter = match.groups()
        quarter = CHINESE_QUARTERS.get(quarter, quarter)
        return f"{year}Q{quarter}"
    return None


def sort_periods(periods: list[str]) -> list[str]:
    canonical = {period for value in periods if (period := canonical_period(value))}
    return sorted(canonical, key=lambda item: (int(item[:4]), int(item[-1])))


def has_relative_period(query: str) -> bool:
    lowered = query.casefold()
    return any(
        term in lowered for term in (*LATEST_TERMS, *PREVIOUS_TERMS, *YEAR_AGO_TERMS)
    )


def resolve_period(
    query: str,
    available_periods: list[str] | None = None,
    fiscal_calendar: FiscalCalendar | None = None,
    as_of: datetime | None = None,
) -> PeriodResolution:
    resolved_at = (as_of or datetime.now(UTC)).date().isoformat()
    exact = canonical_period(query)
    available = sort_periods(available_periods or [])
    if exact:
        return PeriodResolution(
            input=exact,
            resolved_period=exact,
            as_of=resolved_at,
            method="explicit_fiscal_quarter",
            confidence=1.0,
            available_periods=available,
            fiscal_calendar=fiscal_calendar,
        )

    lowered = query.casefold()
    if any(term in lowered for term in LATEST_TERMS):
        resolved = available[-1] if available else None
        method = "latest_verified_available" if resolved else "latest_period_unavailable"
    elif any(term in lowered for term in PREVIOUS_TERMS):
        resolved = available[-2] if len(available) >= 2 else None
        method = "previous_verified_available" if resolved else "previous_period_unavailable"
    elif any(term in lowered for term in YEAR_AGO_TERMS):
        latest = available[-1] if available else None
        candidate = f"{int(latest[:4]) - 1}Q{latest[-1]}" if latest else None
        resolved = candidate if candidate in available else None
        method = "same_quarter_previous_year" if resolved else "year_ago_period_unavailable"
    else:
        return PeriodResolution(
            input=None,
            resolved_period=None,
            as_of=resolved_at,
            method="not_specified",
            confidence=1.0,
            available_periods=available,
            fiscal_calendar=fiscal_calendar,
        )
    return PeriodResolution(
        input=next(
            (
                term
                for term in (*LATEST_TERMS, *PREVIOUS_TERMS, *YEAR_AGO_TERMS)
                if term in lowered
            ),
            None,
        ),
        resolved_period=resolved,
        as_of=resolved_at,
        method=method,
        confidence=1.0 if resolved else 0.0,
        available_periods=available,
        fiscal_calendar=fiscal_calendar,
    )
