from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from difflib import SequenceMatcher

from app.models import CompanyCandidate, CompanySummary


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
        normalized for alias in aliases if len(normalized := normalize_company_term(alias)) >= 2
    }


def find_company_mentions(query: str, companies: Iterable[CompanySummary]) -> list[CompanySummary]:
    """Return every company deterministically mentioned by name, alias, or co_code."""
    normalized_query = normalize_company_term(query)
    raw_query = unicodedata.normalize("NFKC", query).casefold()
    matches = []
    for company in companies:
        mentioned = False
        for alias in derived_company_aliases(company):
            if alias.isascii() and len(alias) <= 5:
                mentioned = bool(
                    re.search(
                        rf"(?<![0-9a-z]){re.escape(alias)}(?![0-9a-z])",
                        raw_query,
                    )
                )
            else:
                mentioned = alias in normalized_query
            if mentioned:
                break
        if mentioned:
            matches.append(company)
    return sorted(matches, key=lambda company: company.co_code)


def search_company_candidates(
    query: str,
    companies: Iterable[CompanySummary],
    limit: int = 10,
) -> list[CompanyCandidate]:
    """Rank a bounded candidate set for fuzzy/LLM resolution without inventing codes."""
    items = list(companies)
    exact = find_company_mentions(query, items)
    if exact:
        return [
            CompanyCandidate(
                company=company,
                score=1.0,
                match_method="exact_company_index",
                matched_term=company.co_code,
            )
            for company in exact[:limit]
        ]

    tokens = [
        normalize_company_term(token)
        for token in re.findall(r"[0-9A-Za-z.\-_]+|[\u3400-\u9fff]+", query)
    ]
    tokens = [token for token in tokens if len(token) >= 2]
    normalized_query = normalize_company_term(query)
    candidates: list[CompanyCandidate] = []
    for company in items:
        best_score = 0.0
        best_term: str | None = None
        for alias in derived_company_aliases(company):
            comparisons = [normalized_query, *tokens]
            score = max(
                (SequenceMatcher(None, alias, candidate).ratio() for candidate in comparisons),
                default=0.0,
            )
            if score > best_score:
                best_score = score
                best_term = alias
        if best_score >= 0.55:
            candidates.append(
                CompanyCandidate(
                    company=company,
                    score=round(best_score, 6),
                    match_method="fuzzy_company_index",
                    matched_term=best_term,
                )
            )
    candidates.sort(key=lambda item: (-item.score, item.company.co_code))
    return candidates[: max(1, min(limit, 50))]


def resolve_company_scope(
    default_co_code: str | None,
    mentioned_companies: Iterable[CompanySummary],
) -> str:
    """Resolve exactly one mentioned company, with an optional legacy default."""
    default = default_co_code.strip().upper() if default_co_code else ""
    matches = list(mentioned_companies)
    mentioned_codes = {company.co_code.upper() for company in matches}

    if len(mentioned_codes) > 1:
        labels = [f"{company.company_name} ({company.co_code})" for company in matches]
        raise CompanyResolutionError(
            f"公司解析出多個候選：{labels}；請檢查 Alias 或 Company Master"
        )
    if mentioned_codes:
        return next(iter(mentioned_codes))
    if default:
        return default
    raise CompanyResolutionError(
        "無法從問題判斷公司；請輸入公司正式名稱、常用簡稱或股票代碼"
    )
