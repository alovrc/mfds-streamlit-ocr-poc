from __future__ import annotations

from scripts.run_pipeline import normalize_stage2_statuses
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
