from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.models import Evidence, SourceType


class EvidenceValidationError(ValueError):
    """Raised when evidence violates tenant scope or provenance requirements."""


@dataclass(slots=True)
class EvidenceValidator:
    allowed_co_codes: set[str]

    def validate_scope(self, co_code: str) -> str:
        normalized = co_code.strip().upper()
        if normalized not in self.allowed_co_codes:
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
            if item.source_type == SourceType.DATABASE:
                if not item.locator.table or not item.locator.primary_key:
                    raise EvidenceValidationError("DB Evidence 缺少 table 或 primary key")
                required = {"metric_code", "value", "unit", "scope"}
                missing = required - item.metadata.keys()
                if missing:
                    raise EvidenceValidationError(
                        f"DB Evidence 缺少財務語意欄位：{sorted(missing)}"
                    )
                if expected_period and item.period != expected_period:
                    raise EvidenceValidationError(
                        f"DB Evidence 期間不一致：預期 {expected_period}，收到 {item.period}"
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
            if item.source_type == SourceType.GRAPH and not item.locator.graph_path:
                raise EvidenceValidationError("Graph Evidence 缺少可稽核路徑")
            if item.evidence_id not in seen:
                seen.add(item.evidence_id)
                valid.append(item)

        return valid

    @staticmethod
    def verify_answer(answer: str, evidence: list[Evidence]) -> dict[str, object]:
        cited = {int(value) for value in re.findall(r"\[(\d+)]", answer)}
        allowed = set(range(1, len(evidence) + 1))
        invalid = sorted(cited - allowed)
        answer_without_citations = re.sub(r"\[\d+]", "", answer)
        numeric_claims = set(
            re.findall(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?", answer_without_citations)
        )
        evidence_numbers: set[str] = set()
        for item in evidence:
            evidence_numbers.update(
                re.findall(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?", item.content)
            )
            for value in item.metadata.values():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    evidence_numbers.add(str(value))
        unsupported_numbers = sorted(numeric_claims - evidence_numbers)
        is_grounded = (
            bool(evidence)
            and bool(cited)
            and not invalid
            and not unsupported_numbers
        )
        return {
            "passed": is_grounded,
            "cited_indices": sorted(cited),
            "invalid_indices": invalid,
            "unsupported_numbers": unsupported_numbers,
            "evidence_count": len(evidence),
            "reason": (
                "citations_valid"
                if is_grounded
                else "answer_has_invalid_citations_or_unsupported_numbers"
            ),
        }
