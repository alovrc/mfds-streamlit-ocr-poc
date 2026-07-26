"""Deterministic offline adapter for contract and routing verification only."""

from __future__ import annotations

from typing import Any

from adapters.common.contracts import ProviderResult

DISEASE_WORDS = ("당뇨", "암", "고혈압", "관절염", "치매", "탈모")
TREATMENT_WORDS = ("치료", "완치", "예방", "낫", "약 대신")
SALES_WORDS = ("구매", "주문", "가격", "배송", "공동구매", "DM")


def stage1(payload: dict[str, Any]) -> ProviderResult:
    text = f"{payload.get('title', '')} {payload.get('body_text', '')}"
    product_present = bool(text.strip())
    hff_signal = any(word in text for word in ("건강기능식품", "섭취량", "기능성"))
    product_type = "HEALTH_FUNCTIONAL_FOOD" if hff_signal else "FOOD_FALLBACK"
    confidence = 0.72 if hff_signal else 0.49
    route = "HFF_REVIEW" if hff_signal else "FOOD_REVIEW"
    store = "FS21_HFF_REVIEW" if hff_signal else "FS11_FOOD_REVIEW"
    uncertainty = [] if hff_signal else ["PRODUCT_NAME_UNCLEAR"]
    data = {
        "record_id": payload["record_id"],
        "record_product_type": product_type if product_present else "OUT_OF_SCOPE",
        "product_presence": product_present,
        "multi_product": False,
        "products": (
            [
                {
                    "product_index": 0,
                    "product_name": None,
                    "product_type": product_type,
                    "product_subtype": (
                        "NOT_APPLICABLE"
                        if hff_signal
                        else "UNKNOWN_FOOD"
                    ),
                    "confidence": confidence,
                    "food_confidence": 0.15 if hff_signal else 0.49,
                    "hff_confidence": 0.72 if hff_signal else 0.20,
                    "analysis_target": True,
                    "evidence_ids": [],
                    "uncertainty_codes": uncertainty,
                }
            ]
            if product_present
            else []
        ),
        "sales_ad_context": (
            "CONFIRMED" if any(word in text for word in SALES_WORDS) else "NOT_CONFIRMED"
        ),
        "sales_signals": [word for word in SALES_WORDS if word in text],
        "analysis_target": product_present,
        "routes": (
            [{"product_index": 0, "stage2_route": route, "store_alias": store}]
            if product_present
            else []
        ),
        "uncertainty_codes": uncertainty,
        "requires_human_review": bool(uncertainty),
        "short_reason": "오프라인 계약 검증용 결정론적 분류 결과",
        "file_search": {
            "provider": "offline",
            "store_alias": "FS01_PRODUCT_GATE",
            "file_search_run": False,
            "search_query": text,
            "retrieved_ids": [],
            "citations": [],
            "latency_ms": 0,
        },
    }
    return ProviderResult(
        data=data,
        provider="offline",
        store_alias="FS01_PRODUCT_GATE",
        file_search_run=False,
    )


def stage2(payload: dict[str, Any]) -> ProviderResult:
    text = f"{payload.get('title', '')} {payload.get('body_text', '')}"
    quote = next(
        (word for word in DISEASE_WORDS + TREATMENT_WORDS if word in text),
        "",
    )
    has_risk = (
        any(word in text for word in DISEASE_WORDS)
        and any(word in text for word in TREATMENT_WORDS)
    )
    score = 6 if has_risk else 0
    status = "REVIEW" if score else "NOT_DETECTED"
    expressions = (
        [
            {
                "expression_id": "EXP-001",
                "quote": quote,
                "source_field": (
                    "title" if quote in payload.get("title", "") else "body_text"
                ),
                "product_linked": True,
            }
        ]
        if quote
        else []
    )
    reviews = []
    for violation_type in (
        "DISEASE_PREVENTION_TREATMENT",
        "MEDICINE_CONFUSION",
        "HFF_CONFUSION",
        "UNAPPROVED_FUNCTION",
        "FALSE_EXAGGERATED",
        "CONSUMER_DECEPTION",
        "INGREDIENT_TO_PRODUCT_EFFECT",
        "TESTIMONIAL_EFFECT",
        "EXPERT_ENDORSEMENT",
        "COMPARISON_DEFAMATION",
    ):
        active = violation_type == "DISEASE_PREVENTION_TREATMENT" and has_risk
        reviews.append(
            {
                "violation_type": violation_type,
                "status": status if active else "NOT_DETECTED",
                "risk_score": score if active else 0,
                "expression_ids": ["EXP-001"] if active and quote else [],
                "rule_ids": [],
                "official_evidence_ids": [],
                "case_ids": [],
                "score_factors": ["offline keyword signal"] if active else [],
                "score_reason": (
                    "오프라인 검증기는 법적 판단을 확정하지 않음"
                    if active
                    else "해당 신호 없음"
                ),
                "uncertainty_codes": (
                    ["SEARCH_NO_OFFICIAL_EVIDENCE"] if active else []
                ),
            }
        )
    data = {
        "record_id": payload["record_id"],
        "product_index": payload["product_index"],
        "product_name": payload["stage1_product"]["product_name"],
        "product_type": payload["product_type"],
        "product_subtype": payload["product_subtype"],
        "problem_expressions": expressions,
        "violation_reviews": reviews,
        "product_overall_status": status,
        "product_overall_risk_score": score,
        "uncertainty_codes": (
            ["SEARCH_NO_OFFICIAL_EVIDENCE"] if has_risk else []
        ),
        "requires_human_review": has_risk,
        "file_search": {
            "provider": "offline",
            "store_alias": payload["file_search_store_alias"],
            "file_search_run": False,
            "search_query": text,
            "retrieved_ids": [],
            "citations": [],
            "latency_ms": 0,
        },
    }
    return ProviderResult(
        data=data,
        provider="offline",
        store_alias=payload["file_search_store_alias"],
        file_search_run=False,
    )
