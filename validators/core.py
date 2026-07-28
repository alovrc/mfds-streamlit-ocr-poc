"""Application-level validators beyond JSON Schema expressiveness."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PACKAGE_ROOT / "schemas"

STATUS_BY_SCORE = {
    0: "NOT_DETECTED",
    1: "LOW",
    2: "LOW",
    3: "LOW",
    4: "REVIEW",
    5: "REVIEW",
    6: "REVIEW",
    7: "REVIEW",
    8: "HIGH",
    9: "HIGH",
    10: "HIGH",
}


class ContractValidationError(ValueError):
    """Raised when structural or semantic validation fails."""


def normalize_quote_text(value: str) -> str:
    """Normalize invisible characters and layout-only whitespace for quotes."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", normalized)
    return re.sub(r"\s+", "", normalized)


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate_schema(instance: dict[str, Any], schema_name: str) -> None:
    errors = sorted(
        Draft202012Validator(load_schema(schema_name)).iter_errors(instance),
        key=lambda item: list(item.path),
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, error.path)) or '$'}: {error.message}"
            for error in errors[:10]
        )
        raise ContractValidationError(f"JSON_SCHEMA_INVALID: {detail}")


def validate_stage1_links(stage1: dict[str, Any]) -> None:
    product_indexes = [item["product_index"] for item in stage1["products"]]
    route_indexes = [item["product_index"] for item in stage1["routes"]]
    if len(product_indexes) != len(set(product_indexes)):
        raise ContractValidationError("duplicate products[].product_index")
    if sorted(product_indexes) != sorted(route_indexes):
        raise ContractValidationError("products/routes product_index mismatch")
    if stage1["multi_product"] != (len(product_indexes) > 1):
        raise ContractValidationError("multi_product does not match products length")
    routes = {
        item["product_index"]: item
        for item in stage1["routes"]
    }
    for product in stage1["products"]:
        product_type = product["product_type"]
        food_confidence = product["food_confidence"]
        hff_confidence = product["hff_confidence"]
        route = routes[product["product_index"]]
        master_match = any(
            str(evidence_id).startswith("HFF_MASTER::")
            for evidence_id in product["evidence_ids"]
        )
        if (
            product_type == "FOOD"
            and (
                food_confidence < 0.50
                or food_confidence <= hff_confidence
                or route["stage2_route"] != "FOOD_REVIEW"
                or route["store_alias"] != "FS11_FOOD_REVIEW"
            )
        ):
            raise ContractValidationError(
                "FOOD requires dominant food_confidence >= 0.50 and FS11 route"
            )
        if (
            product_type == "HEALTH_FUNCTIONAL_FOOD"
            and not master_match
            and (
                hff_confidence < 0.50
                or hff_confidence <= food_confidence
                or route["stage2_route"] != "HFF_REVIEW"
                or route["store_alias"] != "FS21_HFF_REVIEW"
            )
        ):
            raise ContractValidationError(
                "HEALTH_FUNCTIONAL_FOOD requires dominant hff_confidence "
                ">= 0.50 and FS21 route"
            )
        if (
            product_type == "UNCERTAIN"
            and (
                route["stage2_route"] != "NO_STAGE2"
                or route["store_alias"] != "FS01_PRODUCT_GATE"
            )
        ):
            raise ContractValidationError(
                "UNCERTAIN requires NO_STAGE2 and FS01 route"
            )


def validate_stage2_input(stage1: dict[str, Any], stage2_input: dict[str, Any]) -> None:
    index = stage2_input["product_index"]
    products = {item["product_index"]: item for item in stage1["products"]}
    routes = {item["product_index"]: item for item in stage1["routes"]}
    if index not in products or index not in routes:
        raise ContractValidationError("stage2 product_index is not present in stage1")
    if stage2_input["stage1_product"] != products[index]:
        raise ContractValidationError("stage2 stage1_product does not match stage1")
    if stage2_input["product_type"] != products[index]["product_type"]:
        raise ContractValidationError("stage2 product_type does not match stage1")
    if stage2_input["product_subtype"] != products[index]["product_subtype"]:
        raise ContractValidationError("stage2 product_subtype does not match stage1")
    if stage2_input["route"] != routes[index]["stage2_route"]:
        raise ContractValidationError("stage2 route does not match stage1")
    if stage2_input["file_search_store_alias"] != routes[index]["store_alias"]:
        raise ContractValidationError("stage2 store alias does not match stage1")


def validate_quotes(stage2_input: dict[str, Any], stage2_output: dict[str, Any]) -> None:
    sources = {
        "title": stage2_input["title"],
        "body_text": stage2_input["body_text"],
    }
    for expression in stage2_output["problem_expressions"]:
        quote = normalize_quote_text(expression["quote"])
        source = normalize_quote_text(sources[expression["source_field"]])
        if not quote or quote not in source:
            raise ContractValidationError(
                f"QUOTE_NOT_IN_SOURCE: {expression['expression_id']}"
            )


def validate_retrieved_ids(stage2_output: dict[str, Any]) -> None:
    retrieved = set(stage2_output["file_search"]["retrieved_ids"])
    for review in stage2_output["violation_reviews"]:
        cited = (
            set(review["rule_ids"])
            | set(review["official_evidence_ids"])
            | set(review["case_ids"])
        )
        missing = cited - retrieved
        if missing:
            raise ContractValidationError(
                f"RETRIEVED_ID_NOT_FOUND: {sorted(missing)}"
            )
        overlap = set(review["official_evidence_ids"]) & set(review["case_ids"])
        if overlap:
            raise ContractValidationError(
                f"EVIDENCE_CASE_TYPE_MISMATCH: {sorted(overlap)}"
            )


def validate_risk(stage2_output: dict[str, Any]) -> None:
    reviews = stage2_output["violation_reviews"]
    scores = [review["risk_score"] for review in reviews]
    if any(type(score) is not int or not 0 <= score <= 10 for score in scores):
        raise ContractValidationError("RISK_SCORE_INVALID")
    expected = max(scores, default=0)
    if stage2_output["product_overall_risk_score"] != expected:
        raise ContractValidationError("RISK_AGGREGATION_MISMATCH")
    for review in reviews:
        if review["status"] == "INSUFFICIENT_EVIDENCE":
            continue
        if review["status"] != STATUS_BY_SCORE[review["risk_score"]]:
            raise ContractValidationError(
                f"status/risk mismatch for {review['violation_type']}"
            )
        if (
            review["status"] in {"HIGH", "REVIEW", "LOW"}
            and not review["rule_ids"]
        ):
            raise ContractValidationError(
                f"ACTIVE_REVIEW_REQUIRES_RULE: {review['violation_type']}"
            )


def validate_stage2(
    stage2_input: dict[str, Any],
    stage2_output: dict[str, Any],
) -> None:
    validate_schema(stage2_input, "stage2_input.schema.json")
    validate_schema(stage2_output, "stage2_output.schema.json")
    if stage2_input["record_id"] != stage2_output["record_id"]:
        raise ContractValidationError("record_id mismatch")
    if stage2_input["product_index"] != stage2_output["product_index"]:
        raise ContractValidationError("product_index mismatch")
    validate_quotes(stage2_input, stage2_output)
    validate_retrieved_ids(stage2_output)
    validate_risk(stage2_output)


def validate_aggregate(aggregate: dict[str, Any]) -> None:
    validate_schema(aggregate, "aggregate_output.schema.json")
    validate_stage1_links(aggregate["stage1"])
    indexes = {item["product_index"] for item in aggregate["stage1"]["products"]}
    result_indexes = {item["product_index"] for item in aggregate["product_results"]}
    expected_results = {
        route["product_index"]
        for route in aggregate["stage1"]["routes"]
        if route["stage2_route"] != "NO_STAGE2"
    }
    if result_indexes != expected_results:
        raise ContractValidationError("multi-product stage2 result omission")
    if not result_indexes.issubset(indexes):
        raise ContractValidationError("unknown product_index in aggregate")
    deterministic = aggregate["deterministic_aggregation"]
    expected = deterministic["overall_risk_score"]
    if aggregate["record_overall_risk_score"] != expected:
        raise ContractValidationError("record RISK_AGGREGATION_MISMATCH")
    article_items = [
        item["article_item"] for item in deterministic["article_summaries"]
    ]
    if article_items != [1, 2, 3, 4, 5]:
        raise ContractValidationError(
            "deterministic article summaries must be ordered 1..5"
        )
    if deterministic["total_occurrence_count"] != sum(
        item["occurrence_count"]
        for item in deterministic["article_summaries"]
    ):
        raise ContractValidationError(
            "deterministic occurrence count mismatch"
        )
    if aggregate["record_id"] != aggregate["stage1"]["record_id"]:
        raise ContractValidationError("aggregate/stage1 record_id mismatch")


def duplicate_ids(records: Iterable[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for record in records:
        record_id = str(record.get("record_id", ""))
        if record_id in seen:
            duplicates.add(record_id)
        seen.add(record_id)
    return sorted(duplicates)
