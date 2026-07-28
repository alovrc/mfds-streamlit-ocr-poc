from __future__ import annotations

import pytest

from validators.core import ContractValidationError, validate_risk


def finding(*, rule_ids: list[str]) -> dict:
    return {
        "violation_type": "DISEASE_PREVENTION_TREATMENT",
        "status": "HIGH",
        "risk_score": 10,
        "rule_ids": rule_ids,
        "official_evidence_ids": ["OFFICIAL-1"],
    }


def test_active_finding_requires_rule() -> None:
    output = {
        "violation_reviews": [finding(rule_ids=[])],
        "product_overall_risk_score": 10,
    }

    with pytest.raises(
        ContractValidationError,
        match="ACTIVE_REVIEW_REQUIRES_RULE",
    ):
        validate_risk(output)


def test_active_finding_with_rule_passes() -> None:
    output = {
        "violation_reviews": [finding(rule_ids=["RULE-1"])],
        "product_overall_risk_score": 10,
    }

    validate_risk(output)
