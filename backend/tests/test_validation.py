import pytest

from app.models import Evidence, SourceLocator, SourceType
from app.validation import EvidenceValidationError, EvidenceValidator


def evidence(co_code: str = "DEMO01") -> Evidence:
    return Evidence(
        evidence_id="ev-1",
        co_code=co_code,
        source_id="source-1",
        source_type=SourceType.TRANSCRIPT,
        title="source",
        content="supported claim",
        score=0.9,
        locator=SourceLocator(paragraph_id="p-1"),
    )


def test_rejects_cross_company_evidence() -> None:
    validator = EvidenceValidator({"DEMO01", "DEMO02"})
    with pytest.raises(EvidenceValidationError, match="跨公司"):
        validator.validate_evidence("DEMO01", [evidence("DEMO02")])


def test_rejects_unknown_scope() -> None:
    validator = EvidenceValidator({"DEMO01"})
    with pytest.raises(EvidenceValidationError, match="不在允許範圍"):
        validator.validate_scope("OTHER")


def test_answer_citations_must_reference_existing_evidence() -> None:
    result = EvidenceValidator.verify_answer("claim [2]", [evidence()])
    assert result["passed"] is False
    assert result["invalid_indices"] == [2]


def test_valid_answer_is_grounded() -> None:
    result = EvidenceValidator.verify_answer("claim [1]", [evidence()])
    assert result["passed"] is True


def test_answer_rejects_number_not_found_in_evidence() -> None:
    result = EvidenceValidator.verify_answer("營收為 999 億元 [1]", [evidence()])
    assert result["passed"] is False
    assert result["unsupported_numbers"] == ["999"]
