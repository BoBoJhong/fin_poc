from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app.models import Evidence, SourceType


class EvidenceValidationError(ValueError):
    """Raised when evidence violates tenant scope or provenance requirements."""


CITATION_PATTERN = re.compile(r"\[(\d+)]")
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:,\d{3})*(?:\.\d+)?")
NON_FACTUAL_MARKERS = (
    "以下回答僅依據",
    "這些資料只能支持來源回查",
    "不構成投資建議",
    "目前找不到足以回答",
    "來源或答案驗證未通過",
)


def _canonical_number(value: str | int | float) -> str:
    try:
        number = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return str(value)
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _numbers_in_text(value: str) -> set[str]:
    return {_canonical_number(match) for match in NUMBER_PATTERN.findall(value)}


def _semantic_tokens(value: str) -> set[str]:
    """Produce conservative lexical signals for deterministic claim support checks."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = CITATION_PATTERN.sub("", normalized)
    latin = set(re.findall(r"[a-z][a-z0-9_-]{2,}", normalized))
    han_runs = re.findall(r"[\u3400-\u9fff]+", normalized)
    han_bigrams = {
        run[index : index + 2]
        for run in han_runs
        for index in range(max(len(run) - 1, 0))
    }
    return latin | han_bigrams


def _claim_lexically_supported(claim: str, cited: Iterable[Evidence]) -> bool:
    claim_tokens = _semantic_tokens(claim)
    if not claim_tokens:
        return True
    evidence_tokens: set[str] = set()
    for item in cited:
        evidence_tokens.update(_semantic_tokens(item.content))
    overlap = claim_tokens & evidence_tokens
    required = 1 if len(claim_tokens) <= 3 else max(2, round(len(claim_tokens) * 0.2))
    return len(overlap) >= required


def _claim_segments(answer: str) -> list[str]:
    """Split an answer at citation groups while keeping each citation with its claim."""
    segments: list[str] = []
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cursor = 0
        for match in re.finditer(r"(?:\[\d+]\s*)+", line):
            segment = line[cursor : match.end()].strip()
            if segment:
                segments.append(segment)
            cursor = match.end()
        trailing = line[cursor:].strip()
        if trailing:
            segments.append(trailing)
    return segments


def _requires_citation(claim: str) -> bool:
    stripped = claim.strip().lstrip("-•* ")
    if claim.startswith("**") and claim.endswith("**"):
        return False
    if claim.startswith("#"):
        return False
    if any(marker in stripped for marker in NON_FACTUAL_MARKERS):
        return False
    return bool(re.search(r"[A-Za-z\u3400-\u9fff]", stripped))


@dataclass(slots=True)
class EvidenceValidator:
    allowed_co_codes: set[str]
    document_min_relevance_score: float = 0.60
    graph_min_relevance_score: float = 0.70
    require_document_provenance: bool = True

    @classmethod
    def from_settings(cls, settings: object) -> "EvidenceValidator":
        return cls(
            allowed_co_codes=getattr(settings, "allowed_co_code_set"),
            document_min_relevance_score=float(
                getattr(settings, "document_min_relevance_score", 0.60)
            ),
            graph_min_relevance_score=float(
                getattr(settings, "graph_min_relevance_score", 0.70)
            ),
            require_document_provenance=bool(
                getattr(settings, "require_document_provenance", True)
            ),
        )

    def validate_scope(self, co_code: str) -> str:
        normalized = co_code.strip().upper()
        if not normalized:
            raise EvidenceValidationError("co_code 不可為空")
        if self.allowed_co_codes and normalized not in self.allowed_co_codes:
            raise EvidenceValidationError(f"co_code {normalized!r} 不在允許範圍")
        return normalized

    def validate_evidence(
        self,
        co_code: str,
        items: Iterable[Evidence],
        expected_period: str | None = None,
    ) -> list[Evidence]:
        scoped_code = self.validate_scope(co_code)
        valid: list[Evidence] = []
        seen: set[str] = set()
        metric_values: dict[tuple[str, str, str, str], float] = {}

        for item in items:
            if item.co_code.upper() != scoped_code:
                raise EvidenceValidationError(
                    f"跨公司 Evidence：預期 {scoped_code}，收到 {item.co_code}"
                )
            if not item.source_id or not item.evidence_id:
                raise EvidenceValidationError("Evidence 缺少 source_id 或 evidence_id")
            if not item.content.strip():
                raise EvidenceValidationError("Evidence 內容不可為空")
            if expected_period and item.period != expected_period:
                continue
            if item.source_type == SourceType.GRAPH:
                if item.score < self.graph_min_relevance_score:
                    continue
            elif item.source_type != SourceType.DATABASE:
                if item.score < self.document_min_relevance_score:
                    continue
                if self.require_document_provenance:
                    has_locator = bool(
                        item.locator.page is not None
                        or item.locator.paragraph_id
                        or item.locator.timestamp
                    )
                    if not has_locator or not item.content_hash:
                        raise EvidenceValidationError(
                            "文件 Evidence 缺少段落定位或 content_hash"
                        )
            if item.source_type == SourceType.DATABASE:
                if not item.locator.table or not item.locator.primary_key:
                    raise EvidenceValidationError("DB Evidence 缺少 table 或 primary key")
                required = {"metric_code", "value", "unit", "scope"}
                missing = required - item.metadata.keys()
                if missing:
                    raise EvidenceValidationError(
                        f"DB Evidence 缺少財務語意欄位：{sorted(missing)}"
                    )
                key = (
                    item.period or "",
                    str(item.metadata["metric_code"]),
                    str(item.metadata["unit"]),
                    str(item.metadata["scope"]),
                )
                value = float(item.metadata["value"])
                if key in metric_values and metric_values[key] != value:
                    raise EvidenceValidationError(f"相同財務指標出現衝突值：{key}")
                metric_values[key] = value
            if item.source_type == SourceType.GRAPH:
                if not item.locator.graph_path:
                    raise EvidenceValidationError("Graph Evidence 缺少可稽核路徑")
                provenance = item.metadata.get("relationship_provenance")
                if not isinstance(provenance, list) or not provenance:
                    raise EvidenceValidationError("Graph Evidence 缺少關係 Provenance")
                required = {"type", "co_code", "source_id", "period", "data_version"}
                for relation in provenance:
                    if not isinstance(relation, dict) or not required.issubset(relation):
                        raise EvidenceValidationError("Graph 關係 Provenance 欄位不完整")
                    if str(relation["co_code"]).upper() != scoped_code:
                        raise EvidenceValidationError("Graph 關係跨越公司範圍")
                    if any(not relation[field] for field in required):
                        raise EvidenceValidationError("Graph 關係 Provenance 含空值")
                    if relation["data_version"] != item.data_version:
                        raise EvidenceValidationError("Graph 關係資料版本不一致")
            if item.evidence_id not in seen:
                seen.add(item.evidence_id)
                valid.append(item)

        return valid

    @staticmethod
    def verify_answer(answer: str, evidence: list[Evidence]) -> dict[str, object]:
        cited = {int(value) for value in CITATION_PATTERN.findall(answer)}
        allowed = set(range(1, len(evidence) + 1))
        invalid = sorted(cited - allowed)
        evidence_numbers: dict[int, set[str]] = {}
        for index, item in enumerate(evidence, start=1):
            numbers = _numbers_in_text(item.content)
            for value in item.metadata.values():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numbers.add(_canonical_number(value))
            evidence_numbers[index] = numbers

        uncited_claims: list[str] = []
        unsupported_numbers: set[str] = set()
        unsupported_claims: list[str] = []
        claim_checks: list[dict[str, object]] = []
        for claim in _claim_segments(answer):
            claim_citations = {
                int(value) for value in CITATION_PATTERN.findall(claim)
            }
            factual = _requires_citation(claim)
            if factual and not claim_citations:
                uncited_claims.append(claim)
            valid_citations = claim_citations & allowed
            supported_numbers: set[str] = set()
            for index in valid_citations:
                supported_numbers.update(evidence_numbers[index])
            claim_without_citations = CITATION_PATTERN.sub("", claim)
            claim_numbers = _numbers_in_text(claim_without_citations)
            missing_numbers = claim_numbers - supported_numbers
            if factual:
                unsupported_numbers.update(missing_numbers)
            cited_items = [
                evidence[index - 1]
                for index in sorted(valid_citations)
                if 1 <= index <= len(evidence)
            ]
            lexically_supported = not factual or _claim_lexically_supported(
                claim_without_citations, cited_items
            )
            if factual and valid_citations and not lexically_supported:
                unsupported_claims.append(claim)
            claim_checks.append(
                {
                    "claim": claim,
                    "citations": sorted(claim_citations),
                    "unsupported_numbers": sorted(missing_numbers),
                    "lexically_supported": lexically_supported,
                    "passed": not factual
                    or (
                        bool(claim_citations & allowed)
                        and not (claim_citations - allowed)
                        and not missing_numbers
                        and lexically_supported
                    ),
                }
            )

        is_grounded = (
            bool(evidence)
            and bool(cited)
            and not invalid
            and not uncited_claims
            and not unsupported_numbers
            and not unsupported_claims
        )
        return {
            "passed": is_grounded,
            "cited_indices": sorted(cited),
            "invalid_indices": invalid,
            "uncited_claims": uncited_claims,
            "unsupported_numbers": sorted(unsupported_numbers),
            "unsupported_claims": unsupported_claims,
            "claim_checks": claim_checks,
            "evidence_count": len(evidence),
            "reason": (
                "citations_valid"
                if is_grounded
                else "answer_has_invalid_citations_or_unsupported_claims"
            ),
        }
