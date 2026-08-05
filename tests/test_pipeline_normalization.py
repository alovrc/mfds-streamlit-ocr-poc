from __future__ import annotations

from scripts.run_pipeline import (
    normalize_stage2_statuses,
    quarantine_non_advertising_candidates,
)
from validators.core import normalize_quote_text


def test_quote_normalization_ignores_naver_layout_characters() -> None:
    source = "약\u200b 시작 안 하고\n6개월  영양제"
    quote = "약 시작 안 하고 6개월 영양제"

    assert normalize_quote_text(quote) in normalize_quote_text(source)


def test_status_is_derived_from_risk_score() -> None:
    output = {
        "violation_reviews": [
            {
                "status": "REVIEW",
                "risk_score": 9,
            }
        ],
        "product_overall_status": "REVIEW",
    }

    normalize_stage2_statuses(output)

    assert output["violation_reviews"][0]["status"] == "HIGH"
    assert output["product_overall_status"] == "HIGH"


def test_non_advertising_health_information_is_not_scored_as_ad() -> None:
    stage1 = {
        "sales_ad_context": "NOT_CONFIRMED",
        "sales_signals": [],
    }
    products = [
        {
            "product_overall_status": "HIGH",
            "product_overall_risk_score": 9,
            "violation_reviews": [
                {
                    "status": "HIGH",
                    "risk_score": 9,
                    "expression_ids": ["EXP-1"],
                    "score_reason": "health information",
                }
            ],
        }
    ]

    quarantine_non_advertising_candidates(stage1, products)

    review = products[0]["violation_reviews"][0]
    assert review["status"] == "NOT_DETECTED"
    assert review["risk_score"] == 0
    assert review["expression_ids"] == []
    assert products[0]["product_overall_status"] == "NOT_DETECTED"
