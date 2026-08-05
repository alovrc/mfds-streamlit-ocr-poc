from scripts.run_pipeline import (
    apply_food_hff_confusion_guardrail,
    normalize_stage1_product_type_confidence,
    normalize_stage2_statuses,
)
from validators.core import validate_stage1_links


def stage1_output(
    *,
    product_type: str,
    food_confidence: float,
    hff_confidence: float,
    uncertainty_codes: list[str] | None = None,
) -> dict:
    return {
        "record_product_type": product_type,
        "multi_product": False,
        "products": [
            {
                "product_index": 0,
                "product_type": product_type,
                "product_subtype": "UNKNOWN_FOOD",
                "confidence": 0.60,
                "food_confidence": food_confidence,
                "hff_confidence": hff_confidence,
                "evidence_ids": [],
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


def test_routes_food_candidate_at_point_five() -> None:
    output = stage1_output(
        product_type="FOOD_FALLBACK",
        food_confidence=0.50,
        hff_confidence=0.15,
    )

    normalize_stage1_product_type_confidence(output)

    assert output["products"][0]["product_type"] == "FOOD"
    assert output["products"][0]["confidence"] == 0.50
    assert output["record_product_type"] == "FOOD"
    assert output["routes"][0]["store_alias"] == "FS11_FOOD_REVIEW"
    assert output["requires_human_review"] is True


def test_routes_hff_candidate_at_point_five() -> None:
    output = stage1_output(
        product_type="FOOD_FALLBACK",
        food_confidence=0.15,
        hff_confidence=0.50,
    )

    normalize_stage1_product_type_confidence(output)

    assert (
        output["products"][0]["product_type"]
        == "HEALTH_FUNCTIONAL_FOOD"
    )
    assert output["record_product_type"] == "HEALTH_FUNCTIONAL_FOOD"
    assert output["routes"][0]["stage2_route"] == "HFF_REVIEW"
    assert output["routes"][0]["store_alias"] == "FS21_HFF_REVIEW"


def test_both_scores_below_point_five_require_human_review() -> None:
    output = stage1_output(
        product_type="FOOD_FALLBACK",
        food_confidence=0.49,
        hff_confidence=0.20,
    )

    normalize_stage1_product_type_confidence(output)

    assert output["products"][0]["product_type"] == "UNCERTAIN"
    assert output["record_product_type"] == "UNCERTAIN"
    assert output["routes"][0]["stage2_route"] == "FOOD_REVIEW"
    assert output["routes"][0]["store_alias"] == "FS11_FOOD_REVIEW"
    assert "PRODUCT_NAME_UNCLEAR" in output["uncertainty_codes"]
    validate_stage1_links(output)


def test_uncertain_product_with_health_claim_is_routed_for_recall() -> None:
    output = stage1_output(
        product_type="FOOD_FALLBACK",
        food_confidence=0.30,
        hff_confidence=0.30,
    )

    normalize_stage1_product_type_confidence(
        output,
        {
            "title": "블루베리 건강 게시물",
            "body_text": (
                "블루베리 (천연 혈압약) 혈관 건강과 혈당 관리에 "
                "도움을 줄 수 있습니다."
            ),
        },
    )

    product = output["products"][0]
    assert product["product_type"] == "FOOD_FALLBACK"
    assert output["record_product_type"] == "FOOD_FALLBACK"
    assert output["routes"][0]["stage2_route"] == "FOOD_REVIEW"
    assert output["routes"][0]["store_alias"] == "FS11_FOOD_REVIEW"
    assert "PRODUCT_TYPE_UNCERTAIN_REVIEW" in product["uncertainty_codes"]
    assert output["requires_human_review"] is True


def test_near_tied_scores_require_human_review() -> None:
    output = stage1_output(
        product_type="FOOD_FALLBACK",
        food_confidence=0.60,
        hff_confidence=0.56,
    )

    normalize_stage1_product_type_confidence(output)

    product = output["products"][0]
    assert product["product_type"] == "UNCERTAIN"
    assert "CONFLICTING_PRODUCT_TYPE_EVIDENCE" in product["uncertainty_codes"]
    assert (
        "CONFLICTING_PRODUCT_TYPE_EVIDENCE"
        in output["uncertainty_codes"]
    )
    assert output["routes"][0]["stage2_route"] == "FOOD_REVIEW"


def test_explicit_conflicting_evidence_requires_human_review() -> None:
    output = stage1_output(
        product_type="FOOD_FALLBACK",
        food_confidence=0.90,
        hff_confidence=0.20,
        uncertainty_codes=["CONFLICTING_PRODUCT_TYPE_EVIDENCE"],
    )

    normalize_stage1_product_type_confidence(output)

    assert output["products"][0]["product_type"] == "UNCERTAIN"


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
