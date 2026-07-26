from scripts.run_pipeline import (
    apply_food_hff_confusion_guardrail,
    normalize_stage1_food_confidence,
    normalize_stage2_statuses,
)


def stage1_output(
    *,
    product_type: str,
    food_confidence: float,
    hff_confidence: float,
    uncertainty_codes: list[str] | None = None,
) -> dict:
    return {
        "record_product_type": product_type,
        "products": [
            {
                "product_index": 0,
                "product_type": product_type,
                "product_subtype": "UNKNOWN_FOOD",
                "confidence": 0.60,
                "food_confidence": food_confidence,
                "hff_confidence": hff_confidence,
                "uncertainty_codes": uncertainty_codes or [],
            }
        ],
        "routes": [
            {
                "product_index": 0,
                "stage2_route": "FOOD_REVIEW",
                "store_alias": "FS11_FOOD_REVIEW",
            }
        ],
        "requires_human_review": False,
        "short_reason": "test",
    }


def stage2_payload(
    *,
    product_type: str,
    food_confidence: float,
    hff_confidence: float = 0.20,
    title: str = "라메모아 영양제",
    uncertainty_codes: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "body_text": "",
        "product_index": 0,
        "product_type": product_type,
        "stage1_product": {
            "food_confidence": food_confidence,
            "hff_confidence": hff_confidence,
        },
        "stage1_uncertainty_codes": uncertainty_codes or [],
    }


def stage2_output() -> dict:
    return {
        "problem_expressions": [],
        "violation_reviews": [
            {
                "violation_type": "HFF_CONFUSION",
                "status": "NOT_DETECTED",
                "risk_score": 0,
                "expression_ids": [],
                "rule_ids": [],
                "official_evidence_ids": [],
                "case_ids": [],
                "score_factors": [],
                "score_reason": "",
                "uncertainty_codes": [],
            }
        ],
        "product_overall_status": "NOT_DETECTED",
        "product_overall_risk_score": 0,
        "uncertainty_codes": [],
        "requires_human_review": False,
    }


def test_promotes_food_fallback_at_point_eight() -> None:
    output = stage1_output(
        product_type="FOOD_FALLBACK",
        food_confidence=0.80,
        hff_confidence=0.15,
    )

    normalize_stage1_food_confidence(output)

    assert output["products"][0]["product_type"] == "FOOD"
    assert output["products"][0]["confidence"] == 0.80
    assert output["record_product_type"] == "FOOD"
    assert output["requires_human_review"] is True


def test_does_not_promote_food_fallback_below_point_eight() -> None:
    output = stage1_output(
        product_type="FOOD_FALLBACK",
        food_confidence=0.79,
        hff_confidence=0.15,
    )

    normalize_stage1_food_confidence(output)

    assert output["products"][0]["product_type"] == "FOOD_FALLBACK"


def test_does_not_promote_conflicting_product_type_evidence() -> None:
    output = stage1_output(
        product_type="FOOD_FALLBACK",
        food_confidence=0.90,
        hff_confidence=0.20,
        uncertainty_codes=["CONFLICTING_PRODUCT_TYPE_EVIDENCE"],
    )

    normalize_stage1_food_confidence(output)

    assert output["products"][0]["product_type"] == "FOOD_FALLBACK"


def test_point_five_food_candidate_with_supplement_term_is_reviewed() -> None:
    payload = stage2_payload(
        product_type="FOOD_FALLBACK",
        food_confidence=0.50,
    )
    output = stage2_output()

    apply_food_hff_confusion_guardrail(payload, output)
    normalize_stage2_statuses(output)

    review = output["violation_reviews"][0]
    assert review["status"] == "REVIEW"
    assert review["risk_score"] == 6
    assert review["expression_ids"] == ["AUTO-HFF-CONFUSION-0"]
    assert output["product_overall_risk_score"] == 6
    assert output["requires_human_review"] is True


def test_below_point_five_food_candidate_is_not_forced() -> None:
    payload = stage2_payload(
        product_type="FOOD_FALLBACK",
        food_confidence=0.49,
    )
    output = stage2_output()

    apply_food_hff_confusion_guardrail(payload, output)
    normalize_stage2_statuses(output)

    assert output["violation_reviews"][0]["status"] == "NOT_DETECTED"
    assert output["product_overall_risk_score"] == 0


def test_multi_product_context_is_not_auto_linked() -> None:
    payload = stage2_payload(
        product_type="FOOD_FALLBACK",
        food_confidence=0.70,
        uncertainty_codes=["MULTI_PRODUCT"],
    )
    output = stage2_output()

    apply_food_hff_confusion_guardrail(payload, output)
    normalize_stage2_statuses(output)

    assert output["violation_reviews"][0]["status"] == "NOT_DETECTED"
