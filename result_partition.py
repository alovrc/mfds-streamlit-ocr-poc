"""Prepare independent review findings from validated pipeline output."""

from __future__ import annotations

from typing import Any


VIOLATION_LABELS = {
    "DISEASE_PREVENTION_TREATMENT": "질병의 예방·치료 효능",
    "MEDICINE_CONFUSION": "의약품 오인·혼동",
    "HFF_CONFUSION": "건강기능식품 오인·혼동",
    "UNAPPROVED_FUNCTION": "미인정 기능성",
    "FALSE_EXAGGERATED": "거짓·과장",
    "CONSUMER_DECEPTION": "소비자 기만",
    "INGREDIENT_TO_PRODUCT_EFFECT": "원재료 효능의 완제품 전환",
    "TESTIMONIAL_EFFECT": "후기·체험담 효능",
    "EXPERT_ENDORSEMENT": "전문가 보증·추천",
    "COMPARISON_DEFAMATION": "비교·비방",
}

ACTIVE_STATUSES = {"HIGH", "REVIEW", "LOW"}


def _citation_index(
    citations: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Index the most informative citation for each retrieved record."""

    index: dict[str, dict[str, Any]] = {}
    for citation in citations:
        record_id = str(citation.get("record_id") or "")
        if not record_id:
            continue
        current = index.get(record_id)
        if current is None or (
            not current.get("excerpt") and citation.get("excerpt")
        ):
            index[record_id] = citation
    return index


def _evidence_details(
    record_ids: list[str],
    citations: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join model-selected evidence IDs to actual File Search metadata."""

    details: list[dict[str, Any]] = []
    for record_id in record_ids:
        citation = citations.get(record_id, {})
        details.append(
            {
                "record_id": record_id,
                "file_name": citation.get("file_name"),
                "source": citation.get("source"),
                "page": citation.get("page"),
                "excerpt": citation.get("excerpt"),
                "citation_matched": bool(citation),
            }
        )
    return details


def _all_reviews(output: dict[str, Any]) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    stage1_products = {
        item.get("product_index"): item
        for item in output.get("stage1", {}).get("products", [])
    }
    for product in output.get("product_results", []):
        stage1_product = stage1_products.get(product.get("product_index"), {})
        expressions = {
            item.get("expression_id"): item
            for item in product.get("problem_expressions", [])
        }
        citations = _citation_index(
            product.get("file_search", {}).get("citations", [])
        )
        for review in product.get("violation_reviews", []):
            reviews.append(
                {
                    "product_index": product.get("product_index"),
                    "product_name": product.get("product_name"),
                    "food_confidence": stage1_product.get(
                        "food_confidence"
                    ),
                    "hff_confidence": stage1_product.get("hff_confidence"),
                    "_expressions": expressions,
                    "_citations": citations,
                    **review,
                }
            )
    return reviews


def _candidate(review: dict[str, Any]) -> dict[str, Any]:
    expressions = review.get("_expressions", {})
    citations = review.get("_citations", {})
    expression_ids = list(review.get("expression_ids", []))
    rule_ids = list(review.get("rule_ids", []))
    official_evidence_ids = list(review.get("official_evidence_ids", []))
    case_ids = list(review.get("case_ids", []))
    return {
        "product_index": review.get("product_index"),
        "product_name": review.get("product_name"),
        "food_confidence": review.get("food_confidence"),
        "hff_confidence": review.get("hff_confidence"),
        "violation_type": review.get("violation_type"),
        "violation_label": VIOLATION_LABELS.get(
            str(review.get("violation_type")),
            str(review.get("violation_type")),
        ),
        "status": review.get("status"),
        "risk_score": review.get("risk_score"),
        "expression_ids": expression_ids,
        "problem_expressions": [
            {
                "expression_id": expression_id,
                "quote": expressions.get(expression_id, {}).get("quote"),
                "source_field": expressions.get(expression_id, {}).get(
                    "source_field"
                ),
            }
            for expression_id in expression_ids
        ],
        "rule_ids": rule_ids,
        "official_evidence_ids": official_evidence_ids,
        "case_ids": case_ids,
        "evidence_details": {
            "rules": _evidence_details(rule_ids, citations),
            "official_evidence": _evidence_details(
                official_evidence_ids,
                citations,
            ),
            "cases": _evidence_details(case_ids, citations),
        },
        "score_factors": list(review.get("score_factors", [])),
        "score_reason": review.get("score_reason", ""),
        "uncertainty_codes": list(review.get("uncertainty_codes", [])),
    }


def independent_review_output(output: dict[str, Any]) -> dict[str, Any]:
    """Return only findings derived independently from the current ad text."""

    findings = [
        _candidate(review)
        for review in _all_reviews(output)
        if review.get("status") in ACTIVE_STATUSES
    ]
    findings.sort(
        key=lambda item: (
            -(item["risk_score"] or 0),
            item["product_index"] if item["product_index"] is not None else -1,
            item["violation_type"],
        )
    )
    return {
        "record_id": output.get("record_id"),
        "independent_findings": findings,
        "independent_findings_scope": (
            "현재 광고 원문을 제품정보·Rule·공식근거와 대조해 탐지한 위반 가능 "
            "항목입니다. 최종 판단 전 담당자가 원문과 검색 근거를 확인해야 "
            "합니다."
        ),
        "deterministic_aggregation": output.get(
            "deterministic_aggregation", {}
        ),
        "raw_model_summary": {
            "record_overall_status": output.get("record_overall_status"),
            "record_overall_risk_score": output.get(
                "record_overall_risk_score"
            ),
            "requires_human_review": output.get("requires_human_review"),
            "error_codes": list(output.get("error_codes", [])),
        },
    }
