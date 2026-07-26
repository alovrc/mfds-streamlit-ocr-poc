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
from validators.core import (
    ContractValidationError,
    STATUS_BY_SCORE,
    validate_aggregate,
    validate_schema,
    validate_stage1_links,
    validate_stage2,
    validate_stage2_input,
)

HIGH_CONFIDENCE_FOOD_THRESHOLD = 0.80
FOOD_REVIEW_THRESHOLD = 0.50


def load_prompt(name: str) -> str:
    return (PACKAGE_ROOT / "prompts" / name).read_text(encoding="utf-8")


def provider_module(provider: str):
    if provider == "openai":
        from adapters.openai import client

        return client
    if provider == "offline":
        return offline
    raise ValueError(f"unsupported provider: {provider}")


def stage1(provider: str, source: dict[str, Any]) -> dict[str, Any]:
    validate_schema(source, "stage1_input.schema.json")
    module = provider_module(provider)
    if provider == "offline":
        result = module.stage1(source)
    else:
        result = module.run(
            system_prompt=load_prompt("stage1_system_prompt.md"),
            payload=source,
            schema_name="stage1_output.schema.json",
            store_alias="FS01_PRODUCT_GATE",
        )
        result.data["file_search"] = result.tracking(
            f"{source['title']} {source['body_text']}"
        )
    normalize_stage1_food_confidence(result.data)
    validate_schema(result.data, "stage1_output.schema.json")
    validate_stage1_links(result.data)
    return result.data


def normalize_stage1_food_confidence(output: dict[str, Any]) -> None:
    """Promote a supported high-confidence food candidate deterministically."""

    promoted_indexes: set[int] = set()
    for product in output.get("products", []):
        product_type = product.get("product_type")
        food_confidence = product.get("food_confidence")
        hff_confidence = product.get("hff_confidence")
        uncertainty_codes = product.get("uncertainty_codes", [])
        if (
            product_type not in {"FOOD_FALLBACK", "UNCERTAIN"}
            or not isinstance(food_confidence, (int, float))
            or not isinstance(hff_confidence, (int, float))
            or food_confidence < HIGH_CONFIDENCE_FOOD_THRESHOLD
            or food_confidence <= hff_confidence
            or "CONFLICTING_PRODUCT_TYPE_EVIDENCE" in uncertainty_codes
        ):
            continue

        product["product_type"] = "FOOD"
        product["confidence"] = food_confidence
        if product.get("product_subtype") == "NOT_APPLICABLE":
            product["product_subtype"] = "UNKNOWN_FOOD"
        promoted_indexes.add(product["product_index"])

    if not promoted_indexes:
        return

    for route in output.get("routes", []):
        if route.get("product_index") in promoted_indexes:
            route["stage2_route"] = "FOOD_REVIEW"
            route["store_alias"] = "FS11_FOOD_REVIEW"

    products = output.get("products", [])
    if len(products) == 1 and products[0].get("product_index") in promoted_indexes:
        output["record_product_type"] = "FOOD"
    output["requires_human_review"] = True
    reason = str(output.get("short_reason") or "").rstrip()
    suffix = (
        "food_confidence 0.80 이상 기준으로 식품 검토 경로를 적용했으며 "
        "담당자 확인이 필요함"
    )
    output["short_reason"] = f"{reason}; {suffix}" if reason else suffix


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
    apply_food_hff_confusion_guardrail(payload, result.data)
    normalize_stage2_statuses(result.data)
    validate_stage2(payload, result.data)
    return result.data


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
        if (
            review.get("status") != "INSUFFICIENT_EVIDENCE"
            and type(score) is int
            and score in STATUS_BY_SCORE
        ):
            review["status"] = STATUS_BY_SCORE[score]

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
    else:
        output["product_overall_status"] = STATUS_BY_SCORE[expected]


def aggregate(
    source: dict[str, Any],
    stage1_output: dict[str, Any],
    product_results: list[dict[str, Any]],
) -> dict[str, Any]:
    risk = max(
        (item["product_overall_risk_score"] for item in product_results),
        default=0,
    )
    insufficient = any(
        item["product_overall_status"] == "INSUFFICIENT_EVIDENCE"
        for item in product_results
    )
    output = {
        "record_id": source["record_id"],
        "stage1": stage1_output,
        "product_results": product_results,
        "record_overall_status": (
            "INSUFFICIENT_EVIDENCE" if insufficient else STATUS_BY_SCORE[risk]
        ),
        "record_overall_risk_score": risk,
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
    first = stage1(provider, source)
    results: list[dict[str, Any]] = []
    for route in first["routes"]:
        if route["stage2_route"] == "NO_STAGE2":
            continue
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
        ("JSON_SCHEMA_INVALID", "JSON_SCHEMA_INVALID"),
        ("RISK_SCORE_INVALID", "RISK_SCORE_INVALID"),
        ("RISK_AGGREGATION_MISMATCH", "RISK_AGGREGATION_MISMATCH"),
        ("MODEL_REFUSAL_OR_EMPTY", "MODEL_REFUSAL_OR_EMPTY"),
    ]
    code = next(
        (public_code for pattern, public_code in code_patterns if pattern in text),
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
