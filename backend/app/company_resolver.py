from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from app.models import CompanySummary


class CompanyResolutionError(ValueError):
    """Raised when a question cannot be mapped to one unambiguous company scope."""


LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限責任公司",
    "有限公司",
    "控股公司",
    "公司",
)


def normalize_company_term(value: str) -> str:
    """Normalize user/company text without relying on an LLM decision."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", normalized)


def derived_company_aliases(company: CompanySummary) -> set[str]:
    aliases = {company.co_code, company.company_name, *company.aliases}
    for suffix in LEGAL_SUFFIXES:
        if company.company_name.endswith(suffix):
            aliases.add(company.company_name[: -len(suffix)])
    return {
        normalized
        for alias in aliases
        if len(normalized := normalize_company_term(alias)) >= 2
    }


def find_company_mentions(
    query: str, companies: Iterable[CompanySummary]
) -> list[CompanySummary]:
    """Return every company deterministically mentioned by name, alias, or co_code."""
    normalized_query = normalize_company_term(query)
    matches = [
        company
        for company in companies
        if any(alias in normalized_query for alias in derived_company_aliases(company))
    ]
    return sorted(matches, key=lambda company: company.co_code)


def enforce_company_scope(
    requested_co_code: str,
    mentioned_companies: Iterable[CompanySummary],
) -> str:
    """Require query mentions, when present, to agree with the selected company scope."""
    requested = requested_co_code.strip().upper()
    matches = list(mentioned_companies)
    mentioned_codes = {company.co_code.upper() for company in matches}

    if len(mentioned_codes) > 1:
        labels = [f"{company.company_name} ({company.co_code})" for company in matches]
        raise CompanyResolutionError(
            f"問題同時提到多家公司：{labels}；目前一次只能查詢一家公司"
        )
    if mentioned_codes and mentioned_codes != {requested}:
        company = matches[0]
        raise CompanyResolutionError(
            f"問題提到 {company.company_name} ({company.co_code})，"
            f"但目前選擇的公司是 {requested}"
        )
    return requested
