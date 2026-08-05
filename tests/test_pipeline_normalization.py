from __future__ import annotations

from scripts.run_pipeline import (
    apply_input_incomplete_guardrail,
    assess_stage2_input_quality,
    normalize_stage2_statuses,
    quarantine_non_advertising_candidates,
    quarantine_possible_sales_candidates,
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


def test_incomplete_input_still_requires_stage2_review() -> None:
    codes = assess_stage2_input_quality(
        {"title": "제품", "body_text": "판매자 주소만 있음"},
        {"product_name": "테스트 제품"},
    )

    assert "INPUT_INCOMPLETE" in codes
    payload = {"stage1_uncertainty_codes": codes}
    output = {
        "violation_reviews": [
            {
                "status": "NOT_DETECTED",
                "risk_score": 0,
                "uncertainty_codes": [],
            }
        ],
        "uncertainty_codes": [],
        "requires_human_review": False,
    }

    apply_input_incomplete_guardrail(payload, output)

    assert output["product_overall_status"] == "INSUFFICIENT_EVIDENCE"
    assert output["violation_reviews"][0]["status"] == (
        "INSUFFICIENT_EVIDENCE"
    )
    assert output["requires_human_review"] is True


def test_possible_sales_context_is_review_not_auto_noncompliant() -> None:
    stage1 = {"sales_ad_context": "POSSIBLE"}
    products = [
        {
            "product_overall_status": "HIGH",
            "product_overall_risk_score": 9,
            "uncertainty_codes": [],
            "violation_reviews": [
                {
                    "status": "HIGH",
                    "risk_score": 9,
                    "uncertainty_codes": [],
                    "score_reason": "candidate",
                }
            ],
        }
    ]

    quarantine_possible_sales_candidates(stage1, products)

    assert products[0]["violation_reviews"][0]["status"] == "REVIEW"
    assert products[0]["product_overall_status"] == "REVIEW"


def test_incomplete_source_does_not_skip_stage2(monkeypatch) -> None:
    import scripts.run_pipeline as pipeline

    first = {
        "uncertainty_codes": [],
        "products": [
            {
                "product_index": 0,
                "product_name": "제품",
                "product_type": "FOOD",
                "product_subtype": "UNKNOWN_FOOD",
                "confidence": 0.6,
                "food_confidence": 0.6,
                "hff_confidence": 0.2,
                "analysis_target": True,
                "evidence_ids": [],
                "uncertainty_codes": [],
            }
        ],
        "routes": [
            {
                "product_index": 0,
                "stage2_route": "FOOD_REVIEW",
                "store_alias": "FS11_FOOD_REVIEW",
            }
        ],
    }
    calls = []
    monkeypatch.setattr(pipeline, "stage1", lambda provider, source: first)
    monkeypatch.setattr(
        pipeline,
        "stage2",
        lambda provider, payload: calls.append(payload) or {"stage2": True},
    )
    monkeypatch.setattr(
        pipeline,
        "aggregate",
        lambda source, stage1_output, product_results: product_results,
    )

    pipeline.run(
        "offline",
        {
            "record_id": "R-INCOMPLETE",
            "title": "제품",
            "body_text": "주소",
            "product_name": "제품",
            "platform": "test",
            "source_url": "https://example.com/post",
        },
    )

    assert len(calls) == 1
    assert "INPUT_INCOMPLETE" in calls[0]["stage1_uncertainty_codes"]
