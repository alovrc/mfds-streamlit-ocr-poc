#!/usr/bin/env python3
"""Execute stage 1, route per product, execute stage 2, and aggregate."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from adapters.common import offline
from product_master import (
    ProductMasterLookup,
    lookup_product,
    normalize_product_name,
)
from rule_catalog import attach_local_rules
from risk_aggregation import (
    apply_deterministic_review_scores,
    build_deterministic_aggregation,
    derive_record_evidence_status,
    quarantine_general_health_expressions,
)
from validators.core import (
    ContractValidationError,
    STATUS_BY_SCORE,
    normalize_quote_text,
    validate_aggregate,
    validate_schema,
    validate_stage1_links,
    validate_stage2,
    validate_stage2_input,
    retrieved_id_aliases,
)

PRODUCT_TYPE_CANDIDATE_THRESHOLD = 0.50
PRODUCT_TYPE_TIE_MARGIN = 0.05
FOOD_REVIEW_THRESHOLD = PRODUCT_TYPE_CANDIDATE_THRESHOLD
RECALL_REVIEW_UNCERTAINTY_CODE = "PRODUCT_TYPE_UNCERTAIN_REVIEW"
RECALL_REVIEW_KEYWORDS = (
    "혈당",
    "혈압",
    "콜레스테롤",
    "당뇨",
    "혈관",
    "심혈관",
    "콩팥",
    "신장",
    "항암",
    "종양",
    "염증",
    "아토피",
    "치료",
    "예방",
    "개선",
    "완화",
    "억제",
    "회복",
    "혈압약",
    "의약품",
)
MIN_BODY_CHARS = 20
MIN_SOURCE_CHARS = 40
SALES_CONTEXT_UNCERTAINTY_CODE = "SALES_CONTEXT_UNCERTAIN"
INPUT_INCOMPLETE_CODE = "INPUT_INCOMPLETE"
DIRECT_RULE_MARKERS = {
    "DISEASE_PREVENTION_TREATMENT": (
        "질병", "질환", "당뇨", "암", "고혈압", "고지혈증", "치료",
        "예방", "완치", "환자", "증상", "혈당을 낮", "혈당조절",
        "혈당 조절", "혈압을 낮", "염증을 없",
    ),
    "MEDICINE_CONFUSION": (
        "의약품", "혈압약", "당뇨약", "인슐린", "처방", "약 대신",
    ),
    "HFF_CONFUSION": (
        "건강기능식품", "건기식", "영양제", "기능성 제품",
    ),
    "UNAPPROVED_FUNCTION": (
        "미인정", "인정되지 않은", "인정받지 않은", "허가되지 않은",
        "인증되지 않은",
    ),
    "FALSE_EXAGGERATED": (
        "거짓", "과장", "기적", "즉효", "100%", "완벽", "부작용 없음",
        "천연 혈압약", "천연 인슐린", "무조건", "확실히",
    ),
    "CONSUMER_DECEPTION": (
        "원재료", "추출물", "성분", "연구", "논문", "체험", "후기",
        "추천", "전문가", "보증",
    ),
    "INGREDIENT_TO_PRODUCT_EFFECT": (
        "원재료", "추출물", "성분", "안토시아닌", "바나바",
    ),
    "TESTIMONIAL_EFFECT": ("체험", "후기", "사용자 경험"),
    "EXPERT_ENDORSEMENT": ("전문가", "의사", "약사", "추천", "보증"),
    "COMPARISON_DEFAMATION": ("비교", "최고", "1위", "다른 제품"),
}


def sanitize_unicode_surrogates(value: Any) -> Any:
    """Replace lone UTF-16 surrogate code points before JSON/API processing."""

    if isinstance(value, str):
        return "".join(
            "\ufffd" if 0xD800 <= ord(character) <= 0xDFFF else character
            for character in value
        )
    if isinstance(value, list):
        return [sanitize_unicode_surrogates(item) for item in value]
    if isinstance(value, dict):
        return {
            key: sanitize_unicode_surrogates(item)
            for key, item in value.items()
        }
    return value


def load_prompt(name: str) -> str:
    return (PACKAGE_ROOT / "prompts" / name).read_text(encoding="utf-8")


def provider_module(provider: str):
    if provider == "openai":
        from adapters.openai import client

        return client
    if provider == "offline":
        return offline
    raise ValueError(f"unsupported provider: {provider}")


def apply_product_master_lookup(
    output: dict[str, Any],
    lookup: ProductMasterLookup,
) -> None:
    """Apply only a unique exact product-master match deterministically."""

    if lookup.status == "NOT_REQUESTED":
        return
    if lookup.status == "UNAVAILABLE":
        for target in (output, *output.get("products", [])):
            codes = target.setdefault("uncertainty_codes", [])
            if "HFF_DB_UNAVAILABLE" not in codes:
                codes.append("HFF_DB_UNAVAILABLE")
        output["requires_human_review"] = True
        return
    if lookup.status != "EXACT_UNIQUE":
        if lookup.status == "AMBIGUOUS":
            for target in (output, *output.get("products", [])):
                codes = target.setdefault("uncertainty_codes", [])
                if "CONFLICTING_PRODUCT_TYPE_EVIDENCE" not in codes:
                    codes.append("CONFLICTING_PRODUCT_TYPE_EVIDENCE")
            output["requires_human_review"] = True
        return

    match = lookup.matches[0]
    products = output.get("products", [])
    product = next(
        (
            item
            for item in products
            if normalize_product_name(
                str(item.get("product_name") or "")
            )
            == normalize_product_name(match.product_name)
        ),
        products[0] if len(products) == 1 else None,
    )
    if product is None:
        output["requires_human_review"] = True
        return

    product_index = product["product_index"]
    product["product_name"] = match.product_name
    product["product_type"] = "HEALTH_FUNCTIONAL_FOOD"
    product["product_subtype"] = "NOT_APPLICABLE"
    product["confidence"] = 1.0
    product["hff_confidence"] = 1.0
    product["food_confidence"] = min(
        float(product.get("food_confidence", 0.0)),
        0.10,
    )
    evidence_id = f"HFF_MASTER::{match.record_id}"
    if evidence_id not in product["evidence_ids"]:
        product["evidence_ids"].append(evidence_id)
    product["uncertainty_codes"] = [
        code
        for code in product.get("uncertainty_codes", [])
        if code not in {"HFF_DB_NO_MATCH", "HFF_DB_UNAVAILABLE"}
    ]

    for route in output.get("routes", []):
        if route.get("product_index") == product_index:
            route["stage2_route"] = "HFF_REVIEW"
            route["store_alias"] = "FS21_HFF_REVIEW"
    if len(products) == 1:
        output["record_product_type"] = "HEALTH_FUNCTIONAL_FOOD"
    output["uncertainty_codes"] = [
        code
        for code in output.get("uncertainty_codes", [])
        if code not in {"HFF_DB_NO_MATCH", "HFF_DB_UNAVAILABLE"}
    ]
    reason = str(output.get("short_reason") or "").rstrip()
    master_reason = (
        "공개 승인 제품 마스터 정확일치: "
        f"{match.product_name} / {match.business_name} / "
        f"{match.product_type_name}"
    )
    output["short_reason"] = (
        f"{reason}; {master_reason}" if reason else master_reason
    )


def apply_model_product_master_lookups(output: dict[str, Any]) -> None:
    """Exact-lookup each product name extracted by the stage-1 model.

    This fallback is used only when the input did not supply a product name
    and stage 1 has identified one or more product names from the ad.
    """

    seen_names: set[str] = set()
    for product in output.get("products", []):
        product_name = str(product.get("product_name") or "").strip()
        normalized_name = normalize_product_name(product_name)
        if not normalized_name or normalized_name in seen_names:
            continue
        seen_names.add(normalized_name)
        apply_product_master_lookup(output, lookup_product(product_name))


def stage1(provider: str, source: dict[str, Any]) -> dict[str, Any]:
    validate_schema(source, "stage1_input.schema.json")
    master_lookup = lookup_product(source.get("product_name"))
    provider_payload = dict(source)
    provider_payload["product_master_lookup"] = master_lookup.prompt_payload()
    module = provider_module(provider)
    if provider == "offline":
        result = module.stage1(source)
    else:
        result = module.run(
            system_prompt=load_prompt("stage1_system_prompt.md"),
            payload=provider_payload,
            schema_name="stage1_output.schema.json",
            store_alias="FS01_PRODUCT_GATE",
        )
        result.data["file_search"] = result.tracking(
            f"{source['title']} {source['body_text']}"
        )
    if master_lookup.status == "NOT_REQUESTED":
        apply_model_product_master_lookups(result.data)
    else:
        apply_product_master_lookup(result.data, master_lookup)
    normalize_stage1_product_type_confidence(result.data, source)
    validate_schema(result.data, "stage1_output.schema.json")
    validate_stage1_links(result.data)
    return result.data


def normalize_stage1_product_type_confidence(
    output: dict[str, Any],
    source: dict[str, Any] | None = None,
) -> None:
    """Route non-master candidates with a review-preserving fallback.

    A unique exact product-master match remains authoritative. Other products
    are routed only when one score is at least 0.50 and leads the other by
    more than the 0.05 tie margin. Low or near-tied scores continue through
    the food review route and are marked PRODUCT_TYPE_UNCERTAIN_REVIEW.
    """

    route_by_index = {
        route.get("product_index"): route
        for route in output.get("routes", [])
    }
    normalized_types: list[str] = []
    decisions: list[str] = []
    needs_human_review = False
    record_uncertainty = output.setdefault("uncertainty_codes", [])
    source_text = " ".join(
        str(source.get(field) or "")
        for field in ("title", "body_text")
    ) if source else ""
    recall_review_signal = any(
        keyword in source_text for keyword in RECALL_REVIEW_KEYWORDS
    )

    for product in output.get("products", []):
        product_index = product.get("product_index")
        route = route_by_index.get(product_index)
        if route is None or product.get("product_type") == "OUT_OF_SCOPE":
            continue

        evidence_ids = product.get("evidence_ids", [])
        if any(
            str(evidence_id).startswith("HFF_MASTER::")
            for evidence_id in evidence_ids
        ):
            normalized_types.append("HEALTH_FUNCTIONAL_FOOD")
            continue

        food_confidence = product.get("food_confidence")
        hff_confidence = product.get("hff_confidence")
        uncertainty_codes = product.setdefault("uncertainty_codes", [])
        scores_valid = (
            isinstance(food_confidence, (int, float))
            and not isinstance(food_confidence, bool)
            and isinstance(hff_confidence, (int, float))
            and not isinstance(hff_confidence, bool)
        )

        if not scores_valid:
            decision = "UNCERTAIN"
        else:
            score_gap = abs(food_confidence - hff_confidence)
            both_below = (
                food_confidence < PRODUCT_TYPE_CANDIDATE_THRESHOLD
                and hff_confidence < PRODUCT_TYPE_CANDIDATE_THRESHOLD
            )
            conflicting = (
                "CONFLICTING_PRODUCT_TYPE_EVIDENCE" in uncertainty_codes
                or score_gap <= PRODUCT_TYPE_TIE_MARGIN
            )
            if both_below or conflicting:
                decision = (
                    "FOOD_FALLBACK"
                    if recall_review_signal
                    and "MULTI_PRODUCT" not in uncertainty_codes
                    else "UNCERTAIN"
                )
            elif (
                food_confidence >= PRODUCT_TYPE_CANDIDATE_THRESHOLD
                and food_confidence > hff_confidence
            ):
                decision = "FOOD"
            elif (
                hff_confidence >= PRODUCT_TYPE_CANDIDATE_THRESHOLD
                and hff_confidence > food_confidence
            ):
                decision = "HEALTH_FUNCTIONAL_FOOD"
            else:
                decision = "UNCERTAIN"

        product["product_type"] = decision
        product["confidence"] = (
            max(food_confidence, hff_confidence)
            if scores_valid
            else 0.0
        )

        if decision == "FOOD":
            needs_human_review = True
            if product.get("product_subtype") == "NOT_APPLICABLE":
                product["product_subtype"] = "UNKNOWN_FOOD"
            route["stage2_route"] = "FOOD_REVIEW"
            route["store_alias"] = "FS11_FOOD_REVIEW"
            decisions.append(
                f"제품 {product_index}: food_confidence 0.50 이상 식품 후보"
            )
        elif decision == "HEALTH_FUNCTIONAL_FOOD":
            needs_human_review = True
            product["product_subtype"] = "NOT_APPLICABLE"
            route["stage2_route"] = "HFF_REVIEW"
            route["store_alias"] = "FS21_HFF_REVIEW"
            decisions.append(
                f"제품 {product_index}: hff_confidence 0.50 이상 건기식 후보"
            )
        elif decision == "FOOD_FALLBACK":
            needs_human_review = True
            product["product_subtype"] = "UNKNOWN_FOOD"
            route["stage2_route"] = "FOOD_REVIEW"
            route["store_alias"] = "FS11_FOOD_REVIEW"
            if RECALL_REVIEW_UNCERTAINTY_CODE not in uncertainty_codes:
                uncertainty_codes.append(RECALL_REVIEW_UNCERTAINTY_CODE)
            if RECALL_REVIEW_UNCERTAINTY_CODE not in record_uncertainty:
                record_uncertainty.append(RECALL_REVIEW_UNCERTAINTY_CODE)
            decisions.append(
                f"제품 {product_index}: 유형 불확실하지만 건강효능 표현으로 2단계 우선 검토"
            )
        else:
            needs_human_review = True
            product["product_subtype"] = "UNKNOWN_FOOD"
            route["stage2_route"] = "FOOD_REVIEW"
            route["store_alias"] = "FS11_FOOD_REVIEW"
            if RECALL_REVIEW_UNCERTAINTY_CODE not in uncertainty_codes:
                uncertainty_codes.append(RECALL_REVIEW_UNCERTAINTY_CODE)
            if RECALL_REVIEW_UNCERTAINTY_CODE not in record_uncertainty:
                record_uncertainty.append(RECALL_REVIEW_UNCERTAINTY_CODE)
            if (
                scores_valid
                and food_confidence < PRODUCT_TYPE_CANDIDATE_THRESHOLD
                and hff_confidence < PRODUCT_TYPE_CANDIDATE_THRESHOLD
                and "PRODUCT_NAME_UNCLEAR" not in uncertainty_codes
            ):
                uncertainty_codes.append("PRODUCT_NAME_UNCLEAR")
            if (
                scores_valid
                and abs(food_confidence - hff_confidence)
                <= PRODUCT_TYPE_TIE_MARGIN
                and "CONFLICTING_PRODUCT_TYPE_EVIDENCE"
                not in uncertainty_codes
            ):
                uncertainty_codes.append(
                    "CONFLICTING_PRODUCT_TYPE_EVIDENCE"
                )
            for code in (
                "PRODUCT_NAME_UNCLEAR",
                "CONFLICTING_PRODUCT_TYPE_EVIDENCE",
            ):
                if (
                    code in uncertainty_codes
                    and code not in record_uncertainty
                ):
                    record_uncertainty.append(code)
            decisions.append(
                f"제품 {product_index}: 품목 불명확하지만 2단계 식품 기준 검토"
            )

        normalized_types.append(decision)

    if not normalized_types:
        return

    if len(normalized_types) == 1:
        output["record_product_type"] = normalized_types[0]
    elif len(set(normalized_types)) == 1:
        output["record_product_type"] = normalized_types[0]
    else:
        output["record_product_type"] = "UNCERTAIN"

    if needs_human_review:
        output["requires_human_review"] = True
    if decisions:
        reason = str(output.get("short_reason") or "").rstrip()
        suffix = "; ".join(decisions)
        output["short_reason"] = f"{reason}; {suffix}" if reason else suffix


def normalize_stage1_food_confidence(
    output: dict[str, Any],
    source: dict[str, Any] | None = None,
) -> None:
    """Backward-compatible alias for the symmetric routing normalizer."""

    normalize_stage1_product_type_confidence(output, source)


def assess_stage2_input_quality(
    source: dict[str, Any],
    product: dict[str, Any],
) -> list[str]:
    """Assess source completeness before the stage-2 call."""

    title = str(source.get("title") or "").strip()
    body = str(source.get("body_text") or "").strip()
    combined = " ".join(part for part in (title, body) if part)
    codes: list[str] = []
    if not body:
        codes.append("INPUT_MISSING_BODY")
    if len(body) < MIN_BODY_CHARS or len(combined) < MIN_SOURCE_CHARS:
        codes.append("INPUT_TOO_SHORT")

    supplied_name = str(source.get("product_name") or "").strip()
    detected_name = str(product.get("product_name") or "").strip()
    product_name = supplied_name or detected_name
    if product_name:
        compact_source = re.sub(r"\s+", "", combined).casefold()
        compact_name = re.sub(r"\s+", "", product_name).casefold()
        if compact_name and compact_name not in compact_source:
            codes.append("PRODUCT_NOT_FOUND")
    else:
        codes.append("PRODUCT_NAME_UNCLEAR")

    if codes and INPUT_INCOMPLETE_CODE not in codes:
        codes.append(INPUT_INCOMPLETE_CODE)
    return list(dict.fromkeys(codes))


def mark_stage1_input_incomplete(
    output: dict[str, Any],
    codes: list[str],
) -> None:
    """Propagate pre-stage-2 input quality findings to the audit envelope."""

    record_codes = output.setdefault("uncertainty_codes", [])
    for code in codes:
        if code not in record_codes:
            record_codes.append(code)
    for product in output.get("products", []):
        product_codes = product.setdefault("uncertainty_codes", [])
        for code in codes:
            if code not in product_codes:
                product_codes.append(code)
    output["requires_human_review"] = True
    reason = str(output.get("short_reason") or "").rstrip()
    suffix = "입력 원문 사전점검: " + ", ".join(codes)
    output["short_reason"] = f"{reason}; {suffix}" if reason else suffix


def apply_input_incomplete_guardrail(
    payload: dict[str, Any],
    output: dict[str, Any],
) -> None:
    """Keep stage-2 execution but prevent an incomplete source from passing."""

    codes = [
        code
        for code in payload.get("stage1_uncertainty_codes", [])
        if code in {
            "INPUT_MISSING_BODY",
            "INPUT_TOO_SHORT",
            "PRODUCT_NOT_FOUND",
            "PRODUCT_NAME_UNCLEAR",
            INPUT_INCOMPLETE_CODE,
        }
    ]
    if not codes:
        return
    output_codes = output.setdefault("uncertainty_codes", [])
    for code in codes:
        if code not in output_codes:
            output_codes.append(code)
    quarantined = False
    for review in output.get("violation_reviews", []):
        score = review.get("risk_score")
        active = (
            review.get("status") in {"HIGH", "REVIEW", "LOW"}
            or (type(score) is int and score > 0)
        )
        if not active:
            continue
        review["risk_score"] = 0
        review["status"] = "INSUFFICIENT_EVIDENCE"
        quarantined = True
        uncertainty = review.setdefault("uncertainty_codes", [])
        for code in codes:
            if code not in uncertainty:
                uncertainty.append(code)
        review["score_reason"] = (
            "2단계 분석은 실행했지만 입력 원문이 불충분하여 "
            "자동 판정을 보류했습니다."
        )
    if not quarantined and output.get("violation_reviews"):
        review = output["violation_reviews"][0]
        review["risk_score"] = 0
        review["status"] = "INSUFFICIENT_EVIDENCE"
        uncertainty = review.setdefault("uncertainty_codes", [])
        for code in codes:
            if code not in uncertainty:
                uncertainty.append(code)
        review["score_reason"] = (
            "2단계 분석은 실행했지만 입력 원문이 불충분하여 "
            "자동 판정을 보류했습니다."
        )
    output["product_overall_risk_score"] = 0
    output["product_overall_status"] = "INSUFFICIENT_EVIDENCE"
    output["requires_human_review"] = True


def make_stage2_input(
    source: dict[str, Any],
    stage1_output: dict[str, Any],
    product_index: int,
) -> dict[str, Any]:
    product = next(
        item
        for item in stage1_output["products"]
        if item["product_index"] == product_index
    )
    route = next(
        item
        for item in stage1_output["routes"]
        if item["product_index"] == product_index
    )
    payload = {
        "record_id": source["record_id"],
        "title": source["title"],
        "body_text": source["body_text"],
        "stage1_product": product,
        "product_index": product_index,
        "product_type": product["product_type"],
        "product_subtype": product["product_subtype"],
        "route": route["stage2_route"],
        "file_search_store_alias": route["store_alias"],
        "stage1_uncertainty_codes": list(
            dict.fromkeys(
                stage1_output["uncertainty_codes"]
                + product["uncertainty_codes"]
            )
        ),
    }
    validate_stage2_input(stage1_output, payload)
    validate_schema(payload, "stage2_input.schema.json")
    return payload


def stage2(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    module = provider_module(provider)
    if provider == "offline":
        result = module.stage2(payload)
    else:
        result = module.run(
            system_prompt=load_prompt("stage2_system_prompt.md"),
            payload=payload,
            schema_name="stage2_output.schema.json",
            store_alias=payload["file_search_store_alias"],
        )
        result.data["file_search"] = result.tracking(
            f"{payload['title']} {payload['body_text']}"
        )
    quarantine_invalid_problem_expressions(payload, result.data)
    quarantine_general_health_expressions(result.data)
    quarantine_unlinked_problem_candidates(result.data)
    quarantine_indirect_rule_candidates(result.data)
    apply_food_hff_confusion_guardrail(payload, result.data)
    if provider == "openai":
        attach_rules_to_tracking(
            result.data,
            payload["file_search_store_alias"],
        )
    quarantine_unretrieved_evidence_ids(result.data)
    quarantine_mismatched_evidence_ids(result.data)
    apply_deterministic_review_scores(result.data)
    normalize_stage2_statuses(result.data)
    apply_input_incomplete_guardrail(payload, result.data)
    validate_stage2(payload, result.data)
    return result.data


def attach_rules_to_tracking(
    output: dict[str, Any],
    store_alias: str,
) -> None:
    """Attach local Rule IDs and expose their official citations in tracking."""

    rule_ids, rule_citations = attach_local_rules(
        data=output,
        store_alias=store_alias,
    )
    tracking = output.get("file_search")
    if not isinstance(tracking, dict):
        return
    tracking["retrieved_ids"] = list(
        dict.fromkeys([*tracking.get("retrieved_ids", []), *rule_ids])
    )
    citation_rows = list(tracking.get("citations", []))
    citation_rows.extend(
        {
            "record_id": item.record_id,
            "file_name": item.file_name,
            "source": item.source,
            "page": item.page,
            "excerpt": item.excerpt,
        }
        for item in rule_citations
    )
    tracking["citations"] = list(
        {
            (item.get("record_id"), item.get("file_name")): item
            for item in citation_rows
            if isinstance(item, dict)
        }.values()
    )


def quarantine_unretrieved_evidence_ids(output: dict[str, Any]) -> None:
    """Remove model-cited evidence IDs absent from the verified search result."""

    tracking = output.get("file_search")
    if not isinstance(tracking, dict):
        return
    retrieved = retrieved_id_aliases(tracking)
    invalid_any = False
    invalid_official = False
    output_uncertainty = output.setdefault("uncertainty_codes", [])
    for review in output.get("violation_reviews", []):
        if not isinstance(review, dict):
            continue
        review_uncertainty = review.setdefault("uncertainty_codes", [])
        for field in ("official_evidence_ids", "case_ids"):
            ids = list(review.get(field, []))
            valid = [item for item in ids if item in retrieved]
            invalid = [item for item in ids if item not in retrieved]
            if not invalid:
                continue
            review[field] = valid
            invalid_any = True
            invalid_official = invalid_official or field == "official_evidence_ids"
            if "RETRIEVED_ID_NOT_FOUND" not in review_uncertainty:
                review_uncertainty.append("RETRIEVED_ID_NOT_FOUND")
            if "RETRIEVED_ID_NOT_FOUND" not in output_uncertainty:
                output_uncertainty.append("RETRIEVED_ID_NOT_FOUND")
    if invalid_official:
        if "SEARCH_NO_OFFICIAL_EVIDENCE" not in output_uncertainty:
            output_uncertainty.append("SEARCH_NO_OFFICIAL_EVIDENCE")
        for review in output.get("violation_reviews", []):
            if (
                isinstance(review, dict)
                and not review.get("official_evidence_ids")
                and "SEARCH_NO_OFFICIAL_EVIDENCE"
                not in review.setdefault("uncertainty_codes", [])
            ):
                review["uncertainty_codes"].append(
                    "SEARCH_NO_OFFICIAL_EVIDENCE"
                )
    if invalid_any:
        output["requires_human_review"] = True


def quarantine_mismatched_evidence_ids(output: dict[str, Any]) -> None:
    """Isolate official/case ID overlap to this product review only."""

    output_uncertainty = output.setdefault("uncertainty_codes", [])
    for review in output.get("violation_reviews", []):
        if not isinstance(review, dict):
            continue
        official = set(review.get("official_evidence_ids", []))
        cases = set(review.get("case_ids", []))
        overlap = official & cases
        if not overlap:
            continue
        review["official_evidence_ids"] = [
            item for item in review.get("official_evidence_ids", [])
            if item not in overlap
        ]
        review["case_ids"] = [
            item for item in review.get("case_ids", [])
            if item not in overlap
        ]
        uncertainty = review.setdefault("uncertainty_codes", [])
        if "EVIDENCE_CASE_TYPE_MISMATCH" not in uncertainty:
            uncertainty.append("EVIDENCE_CASE_TYPE_MISMATCH")
        if "EVIDENCE_CASE_TYPE_MISMATCH" not in output_uncertainty:
            output_uncertainty.append("EVIDENCE_CASE_TYPE_MISMATCH")
        review["risk_score"] = 0
        review["status"] = "INSUFFICIENT_EVIDENCE"
        review["score_reason"] = (
            "공식근거와 적발사례 ID가 중복되어 해당 제품 후보만 "
            "근거 불충분으로 격리했습니다. 전체 배치는 계속합니다."
        )
        output["requires_human_review"] = True


def quarantine_invalid_problem_expressions(
    payload: dict[str, Any],
    output: dict[str, Any],
) -> None:
    """Remove non-verbatim model quotes and neutralize findings they supported."""

    sources = {
        "title": str(payload.get("title") or ""),
        "body_text": str(payload.get("body_text") or ""),
    }
    valid_expressions: list[dict[str, Any]] = []
    invalid_ids: set[str] = set()
    for expression in output.get("problem_expressions", []):
        source_field = expression.get("source_field")
        quote = normalize_quote_text(str(expression.get("quote") or ""))
        source = normalize_quote_text(sources.get(source_field, ""))
        if quote and quote in source:
            valid_expressions.append(expression)
        else:
            invalid_ids.add(str(expression.get("expression_id") or ""))

    if not invalid_ids:
        for expression in valid_expressions:
            if (
                expression.get("product_linked") is True
                and not expression.get("classification")
            ):
                expression["classification"] = "PROHIBITED_CANDIDATE"
        return

    output["problem_expressions"] = valid_expressions
    for expression in valid_expressions:
        if (
            expression.get("product_linked") is True
            and not expression.get("classification")
        ):
            expression["classification"] = "PROHIBITED_CANDIDATE"
    product_uncertainty = output.setdefault("uncertainty_codes", [])
    if "QUOTE_NOT_IN_SOURCE" not in product_uncertainty:
        product_uncertainty.append("QUOTE_NOT_IN_SOURCE")

    for review in output.get("violation_reviews", []):
        original_ids = review.get("expression_ids", [])
        remaining_ids = [
            expression_id
            for expression_id in original_ids
            if expression_id not in invalid_ids
        ]
        if len(remaining_ids) == len(original_ids):
            continue
        review["expression_ids"] = remaining_ids
        uncertainty = review.setdefault("uncertainty_codes", [])
        if "QUOTE_NOT_IN_SOURCE" not in uncertainty:
            uncertainty.append("QUOTE_NOT_IN_SOURCE")
        if not remaining_ids:
            review["risk_score"] = 0
            review["status"] = "INSUFFICIENT_EVIDENCE"
            review["score_reason"] = (
                "모델이 제시한 문제표현을 입력 원문에서 확인할 수 없어 "
                "해당 후보를 근거 불충분으로 격리했습니다."
            )
    output["requires_human_review"] = True


def quarantine_unlinked_problem_candidates(output: dict[str, Any]) -> None:
    """Require each active candidate to point to a linked expression."""

    linked_ids = _linked_expression_ids(output)
    for review in output.get("violation_reviews", []):
        if not isinstance(review, dict):
            continue
        original_ids = [str(item) for item in review.get("expression_ids", [])]
        remaining_ids = [item for item in original_ids if item in linked_ids]
        if remaining_ids == original_ids:
            continue
        review["expression_ids"] = list(dict.fromkeys(remaining_ids))
        if remaining_ids:
            continue
        score = review.get("risk_score")
        candidate = (
            review.get("status") in {"HIGH", "REVIEW", "LOW"}
            or (type(score) is int and score > 0)
        )
        if candidate:
            review["risk_score"] = 0
            review["status"] = "INSUFFICIENT_EVIDENCE"
            review["score_reason"] = (
                "제품과 직접 연결된 원문 인용이 없어 후보를 "
                "근거 불충분으로 격리했습니다."
            )
            output["requires_human_review"] = True


def _linked_expression_ids(output: dict[str, Any]) -> set[str]:
    return {
        str(expression.get("expression_id"))
        for expression in output.get("problem_expressions", [])
        if expression.get("expression_id")
        and expression.get("product_linked") is True
    }


def quarantine_indirect_rule_candidates(output: dict[str, Any]) -> None:
    """Keep only violation types supported by markers in their own quote."""

    expressions = {
        str(item.get("expression_id")): item
        for item in output.get("problem_expressions", [])
        if item.get("expression_id")
    }
    direct_ids_by_expression: dict[str, set[str]] = {}
    for review in output.get("violation_reviews", []):
        violation_type = str(review.get("violation_type") or "")
        markers = DIRECT_RULE_MARKERS.get(violation_type, ())
        expression_ids = []
        for expression_id in review.get("expression_ids", []):
            expression = expressions.get(str(expression_id), {})
            quote = str(expression.get("quote") or "")
            if expression.get("product_linked") is True and any(
                marker in quote for marker in markers
            ):
                expression_ids.append(str(expression_id))
                direct_ids_by_expression.setdefault(
                    str(expression_id), set()
                ).add(violation_type)
        original_ids = [str(item) for item in review.get("expression_ids", [])]
        if expression_ids == original_ids:
            continue
        review["expression_ids"] = list(dict.fromkeys(expression_ids))
        if expression_ids:
            continue
        score = review.get("risk_score")
        candidate = (
            review.get("status") in {"HIGH", "REVIEW", "LOW"}
            or (type(score) is int and score > 0)
        )
        if candidate:
            review["risk_score"] = 0
            review["status"] = "NOT_DETECTED"
            review["score_reason"] = (
                "인용문에 해당 위반유형의 구체적 금지 표지가 없어 "
                "일반 건강 표현으로 분류했습니다."
            )
    for expression_id, expression in expressions.items():
        if not direct_ids_by_expression.get(expression_id):
            expression["classification"] = "GENERAL_HEALTH"


def apply_food_hff_confusion_guardrail(
    payload: dict[str, Any],
    output: dict[str, Any],
) -> None:
    """Ensure an eligible food candidate advertised as 영양제 is reviewable."""

    product = payload.get("stage1_product", {})
    product_type = payload.get("product_type")
    food_confidence = product.get("food_confidence", 0)
    hff_confidence = product.get("hff_confidence", 0)
    uncertainty_codes = payload.get("stage1_uncertainty_codes", [])
    confirmed_food = product_type == "FOOD"
    provisional_food = (
        product_type == "FOOD_FALLBACK"
        and food_confidence >= FOOD_REVIEW_THRESHOLD
        and food_confidence > hff_confidence
        and "CONFLICTING_PRODUCT_TYPE_EVIDENCE" not in uncertainty_codes
    )
    if (
        not (confirmed_food or provisional_food)
        or "MULTI_PRODUCT" in uncertainty_codes
    ):
        return

    title = str(payload.get("title") or "")
    body = str(payload.get("body_text") or "")
    if "영양제" in title:
        source_field = "title"
    elif "영양제" in body:
        source_field = "body_text"
    else:
        return

    expressions = output.setdefault("problem_expressions", [])
    expression = next(
        (
            item
            for item in expressions
            if item.get("quote") == "영양제"
            and item.get("source_field") == source_field
            and item.get("product_linked") is True
        ),
        None,
    )
    if expression is None:
        expression_id = (
            f"AUTO-HFF-CONFUSION-{payload.get('product_index', 0)}"
        )
        expression = {
            "expression_id": expression_id,
            "quote": "영양제",
            "source_field": source_field,
            "product_linked": True,
            "classification": "PROHIBITED_CANDIDATE",
        }
        expressions.append(expression)
    expression_id = expression["expression_id"]

    reviews = output.setdefault("violation_reviews", [])
    review = next(
        (
            item
            for item in reviews
            if item.get("violation_type") == "HFF_CONFUSION"
        ),
        None,
    )
    if review is None:
        review = {
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
        reviews.append(review)

    if expression_id not in review["expression_ids"]:
        review["expression_ids"].append(expression_id)
    factor = (
        "식품 판정 또는 food_confidence 0.50 이상 식품 후보와 "
        "'영양제' 제품 지칭 표현 연결"
    )
    if factor not in review["score_factors"]:
        review["score_factors"].append(factor)

    official_evidence = review.get("official_evidence_ids", [])
    current_score = review.get("risk_score", 0)
    if not isinstance(current_score, int):
        current_score = 0
    if official_evidence:
        review["risk_score"] = max(current_score, 8)
    else:
        review["risk_score"] = min(max(current_score, 6), 7)
        if "SEARCH_NO_OFFICIAL_EVIDENCE" not in review["uncertainty_codes"]:
            review["uncertainty_codes"].append(
                "SEARCH_NO_OFFICIAL_EVIDENCE"
            )
        product_uncertainty = output.setdefault("uncertainty_codes", [])
        if "SEARCH_NO_OFFICIAL_EVIDENCE" not in product_uncertainty:
            product_uncertainty.append("SEARCH_NO_OFFICIAL_EVIDENCE")
    review["score_reason"] = (
        "식품 판정 또는 food_confidence 0.50 이상 식품 후보를 광고에서 "
        "'영양제'로 지칭하여 "
        "건강기능식품 오인 가능성을 담당자가 확인해야 함"
    )
    output["requires_human_review"] = True


def normalize_stage2_statuses(output: dict[str, Any]) -> None:
    """Derive redundant status fields deterministically from validated scores."""

    reviews = output.get("violation_reviews", [])
    for review in reviews:
        score = review.get("risk_score")
        uncertainty_codes = set(review.get("uncertainty_codes", []))
        if (
            review.get("status") != "INSUFFICIENT_EVIDENCE"
            and type(score) is int
            and score in STATUS_BY_SCORE
        ):
            review["status"] = (
                "REVIEW"
                if uncertainty_codes & {
                    "SEARCH_NO_OFFICIAL_EVIDENCE",
                    SALES_CONTEXT_UNCERTAINTY_CODE,
                }
                else STATUS_BY_SCORE[score]
            )

    scores = [
        review.get("risk_score")
        for review in reviews
        if type(review.get("risk_score")) is int
        and review["risk_score"] in STATUS_BY_SCORE
    ]
    expected = max(scores, default=0)
    output["product_overall_risk_score"] = expected
    if any(
        review.get("status") == "INSUFFICIENT_EVIDENCE"
        for review in reviews
    ):
        output["product_overall_status"] = "INSUFFICIENT_EVIDENCE"
    elif any(
        review.get("status") == "REVIEW"
        and set(review.get("uncertainty_codes", []))
        & {
            "SEARCH_NO_OFFICIAL_EVIDENCE",
            SALES_CONTEXT_UNCERTAINTY_CODE,
        }
        for review in reviews
    ):
        output["product_overall_status"] = "REVIEW"
    else:
        output["product_overall_status"] = STATUS_BY_SCORE[expected]


def quarantine_possible_sales_candidates(
    stage1_output: dict[str, Any],
    product_results: list[dict[str, Any]],
) -> None:
    """Keep POSSIBLE sales context as a human-review candidate only."""

    if stage1_output.get("sales_ad_context") != "POSSIBLE":
        return
    for product in product_results:
        changed = False
        product_codes = product.setdefault("uncertainty_codes", [])
        if SALES_CONTEXT_UNCERTAINTY_CODE not in product_codes:
            product_codes.append(SALES_CONTEXT_UNCERTAINTY_CODE)
        for review in product.get("violation_reviews", []):
            score = review.get("risk_score")
            active = (
                review.get("status") in {"HIGH", "REVIEW", "LOW"}
                or (type(score) is int and score > 0)
            )
            if not active:
                continue
            uncertainty = review.setdefault("uncertainty_codes", [])
            if SALES_CONTEXT_UNCERTAINTY_CODE not in uncertainty:
                uncertainty.append(SALES_CONTEXT_UNCERTAINTY_CODE)
            review["status"] = "REVIEW"
            review["score_reason"] = (
                f"{str(review.get('score_reason') or '').strip()} "
                "[판매성향 POSSIBLE: 자동 부적합이 아닌 담당자 검토]"
            ).strip()
            changed = True
        if changed:
            normalize_stage2_statuses(product)


def quarantine_non_advertising_candidates(
    stage1_output: dict[str, Any],
    product_results: list[dict[str, Any]],
) -> None:
    """Do not expand non-commercial health information into ad violations."""

    if stage1_output.get("sales_ad_context") != "NOT_CONFIRMED":
        return
    if stage1_output.get("sales_signals"):
        return

    for product in product_results:
        changed = False
        for review in product.get("violation_reviews", []):
            if (
                review.get("status") in {"HIGH", "REVIEW", "LOW"}
                or (type(review.get("risk_score")) is int and review["risk_score"] > 0)
                or review.get("expression_ids")
            ):
                review["risk_score"] = 0
                review["status"] = "NOT_DETECTED"
                review["expression_ids"] = []
                review["score_reason"] = (
                    "판매·알선·광고성이 확인되지 않아 건강정보 표현을 "
                    "부당광고 후보로 확장하지 않았습니다."
                )
                changed = True
        if changed:
            normalize_stage2_statuses(product)


def aggregate(
    source: dict[str, Any],
    stage1_output: dict[str, Any],
    product_results: list[dict[str, Any]],
) -> dict[str, Any]:
    quarantine_non_advertising_candidates(stage1_output, product_results)
    quarantine_possible_sales_candidates(stage1_output, product_results)
    deterministic_aggregation = build_deterministic_aggregation(
        product_results
    )
    risk = deterministic_aggregation["overall_risk_score"]
    record_overall_status = derive_record_evidence_status(product_results)
    output = {
        "record_id": source["record_id"],
        "stage1": stage1_output,
        "product_results": product_results,
        "record_overall_status": record_overall_status,
        "record_overall_risk_score": risk,
        "deterministic_aggregation": deterministic_aggregation,
        "requires_human_review": (
            stage1_output["requires_human_review"]
            or any(item["requires_human_review"] for item in product_results)
        ),
        "error_codes": list(
            dict.fromkeys(
                stage1_output["uncertainty_codes"]
                + [
                    code
                    for item in product_results
                    for code in item["uncertainty_codes"]
                ]
            )
        ),
    }
    validate_aggregate(output)
    return output


def run(provider: str, source: dict[str, Any]) -> dict[str, Any]:
    source = sanitize_unicode_surrogates(source)
    first = stage1(provider, source)
    results: list[dict[str, Any]] = []
    for route in first["routes"]:
        if route["stage2_route"] == "NO_STAGE2":
            continue
        product = next(
            item
            for item in first["products"]
            if item["product_index"] == route["product_index"]
        )
        input_codes = assess_stage2_input_quality(source, product)
        if input_codes:
            mark_stage1_input_incomplete(first, input_codes)
        payload = make_stage2_input(source, first, route["product_index"])
        results.append(stage2(provider, payload))
    return aggregate(source, first, results)


def failure_record(
    source: dict[str, Any],
    provider: str,
    stage: str,
    error: Exception,
) -> dict[str, Any]:
    text = str(error)
    code_patterns = [
        ("PROVIDER_QUOTA_EXCEEDED", "PROVIDER_QUOTA_EXCEEDED"),
        ("PROVIDER_RATE_LIMITED", "PROVIDER_RATE_LIMITED"),
        ("PROVIDER_AUTH_FAILED", "PROVIDER_AUTH_FAILED"),
        (
            "PROVIDER_PROJECT_ACCESS_DENIED",
            "PROVIDER_PROJECT_ACCESS_DENIED",
        ),
        ("PROVIDER_MODEL_UNSUPPORTED", "PROVIDER_MODEL_UNSUPPORTED"),
        ("PROVIDER_TIMEOUT", "PROVIDER_TIMEOUT"),
        ("FILE_SEARCH_NOT_RUN", "FILE_SEARCH_NOT_RUN"),
        ("FILE_SEARCH_STORE_DISCOVERY_FAILED", "FILE_SEARCH_STORE_UNAVAILABLE"),
        (
            "FILE_SEARCH_STORE_NOT_UNIQUELY_CONFIGURED",
            "FILE_SEARCH_STORE_UNAVAILABLE",
        ),
        ("FILE_SEARCH_STORE_ID_MISSING", "FILE_SEARCH_STORE_UNAVAILABLE"),
        ("UNKNOWN_FILE_SEARCH_STORE_ALIAS", "FILE_SEARCH_STORE_UNAVAILABLE"),
        ("FILE_SEARCH_STORE_UNAVAILABLE", "FILE_SEARCH_STORE_UNAVAILABLE"),
        ("RETRIEVED_ID_NOT_FOUND", "RETRIEVED_ID_NOT_FOUND"),
        (
            "EVIDENCE_CASE_TYPE_MISMATCH",
            "EVIDENCE_CASE_TYPE_MISMATCH",
        ),
        ("JSON_SCHEMA_INVALID", "JSON_SCHEMA_INVALID"),
        ("RISK_SCORE_INVALID", "RISK_SCORE_INVALID"),
        ("RISK_AGGREGATION_MISMATCH", "RISK_AGGREGATION_MISMATCH"),
        ("MODEL_REFUSAL_OR_EMPTY", "MODEL_REFUSAL_OR_EMPTY"),
    ]
    if re.match(r"^JSON_SCHEMA_INVALID(?:[:\s]|$)", text):
        code = "JSON_SCHEMA_INVALID"
    else:
        code = next(
            (
                public_code
                for pattern, public_code in code_patterns
                if pattern in text
            ),
            "PROVIDER_RESPONSE_INVALID",
        )
    safe_message = text[:1000]
    for pattern, replacement in (
        (r"sk-[A-Za-z0-9_-]+", "[REDACTED_KEY]"),
        (r"vs_[A-Za-z0-9_-]+", "[REDACTED_STORE]"),
        (r"(?:proj|org)_[A-Za-z0-9_-]+", "[REDACTED_SCOPE]"),
    ):
        safe_message = re.sub(pattern, replacement, safe_message)
    return {
        "record_id": str(source.get("record_id") or "UNKNOWN"),
        "stage": stage,
        "provider": provider,
        "error_code": code,
        "message": safe_message,
        "retry_count": 1 if provider != "offline" else 0,
        "requires_human_review": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["offline", "openai"], required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        output = run(args.provider, source)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"validated output: {args.output}")
        return 0
    except Exception as error:
        failure = failure_record(source, args.provider, "aggregate", error)
        failure_path = args.output.with_suffix(".failure.json")
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"pipeline failed: {error}", file=sys.stderr)
        print(f"failure record: {failure_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
