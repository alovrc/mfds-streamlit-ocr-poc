"""Deterministic MFDS PoC risk scoring and representative-type aggregation."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent
RULES_PATH = PACKAGE_ROOT / "risk_rules.json"
ACTIVE_STATUSES = {"HIGH", "REVIEW", "LOW"}
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


def load_risk_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    """Load and minimally validate the versioned deterministic rule table."""

    rules = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "rules_version",
        "scope",
        "score_policy",
        "occurrence_unit",
        "source_document",
        "source_sha256",
        "article_names",
        "article_penalty_priority",
        "violation_rules",
        "representative_selection",
    }
    missing = required - rules.keys()
    if missing:
        raise ValueError(f"RISK_RULES_INVALID: missing {sorted(missing)}")
    for violation_type, rule in rules["violation_rules"].items():
        if not rule.get("aggregate"):
            continue
        article = rule.get("article_item")
        score = rule.get("risk_score")
        if type(article) is not int or article not in range(1, 6):
            raise ValueError(
                f"RISK_RULES_INVALID: {violation_type}.article_item"
            )
        if type(score) is not int or score not in range(1, 11):
            raise ValueError(
                f"RISK_RULES_INVALID: {violation_type}.risk_score"
            )
    return rules


def _valid_expression_ids(output: dict[str, Any]) -> set[str]:
    return {
        str(item.get("expression_id"))
        for item in output.get("problem_expressions", [])
        if item.get("expression_id") and item.get("product_linked") is True
    }


def apply_deterministic_review_scores(
    output: dict[str, Any],
    rules: dict[str, Any] | None = None,
) -> None:
    """Replace provisional model scores with rule-table scores.

    Only a detected candidate with a linked, verbatim expression is scoreable.
    Scores of 8 or more additionally require linked official evidence under the
    PoC evidence contract. Lower-risk candidates without official evidence stay
    active but require human review. Unsupported candidates remain visible as
    INSUFFICIENT_EVIDENCE with score 0.
    """

    rules = rules or load_risk_rules()
    rule_map = rules["violation_rules"]
    valid_ids = _valid_expression_ids(output)
    human_review = bool(output.get("requires_human_review"))

    for review in output.get("violation_reviews", []):
        violation_type = str(review.get("violation_type") or "")
        rule = rule_map.get(violation_type)
        if not rule or not rule.get("aggregate"):
            continue

        previous_score = review.get("risk_score")
        previous_status = review.get("status")
        candidate = (
            previous_status in ACTIVE_STATUSES
            or (type(previous_score) is int and previous_score > 0)
        )
        linked_ids = [
            str(expression_id)
            for expression_id in review.get("expression_ids", [])
            if str(expression_id) in valid_ids
        ]
        review["expression_ids"] = list(dict.fromkeys(linked_ids))

        if not candidate:
            review["risk_score"] = 0
            if previous_status != "INSUFFICIENT_EVIDENCE":
                review["status"] = "NOT_DETECTED"
            continue

        fixed_score = int(rule["risk_score"])
        unsupported = (
            not linked_ids
            or not review.get("rule_ids")
            or (
                fixed_score >= 8
                and not review.get("official_evidence_ids")
            )
        )
        factors = review.setdefault("score_factors", [])
        audit_factor = (
            f"결정론적 규칙 {rules['rules_version']}: "
            f"{rule['risk_code']}={fixed_score}점"
        )
        if audit_factor not in factors:
            factors.append(audit_factor)

        if unsupported:
            review["risk_score"] = 0
            review["status"] = "INSUFFICIENT_EVIDENCE"
            review["score_reason"] = (
                f"모델 후보는 {rule['risk_name']}에 해당하나, "
                "유효한 원문 표현 또는 고위험 공식근거 연결이 부족하여 "
                "결정론적 위험도 집계에서 제외했습니다."
            )
            uncertainty = review.setdefault("uncertainty_codes", [])
            if fixed_score >= 8 and not review.get("official_evidence_ids"):
                if "SEARCH_NO_OFFICIAL_EVIDENCE" not in uncertainty:
                    uncertainty.append("SEARCH_NO_OFFICIAL_EVIDENCE")
                product_uncertainty = output.setdefault(
                    "uncertainty_codes", []
                )
                if (
                    "SEARCH_NO_OFFICIAL_EVIDENCE"
                    not in product_uncertainty
                ):
                    product_uncertainty.append(
                        "SEARCH_NO_OFFICIAL_EVIDENCE"
                    )
            if not review.get("rule_ids"):
                if "SEARCH_NO_RULE" not in uncertainty:
                    uncertainty.append("SEARCH_NO_RULE")
                product_uncertainty = output.setdefault(
                    "uncertainty_codes", []
                )
                if "SEARCH_NO_RULE" not in product_uncertainty:
                    product_uncertainty.append("SEARCH_NO_RULE")
            human_review = True
            continue

        if not review.get("official_evidence_ids"):
            uncertainty = review.setdefault("uncertainty_codes", [])
            if "SEARCH_NO_OFFICIAL_EVIDENCE" not in uncertainty:
                uncertainty.append("SEARCH_NO_OFFICIAL_EVIDENCE")
            product_uncertainty = output.setdefault(
                "uncertainty_codes", []
            )
            if "SEARCH_NO_OFFICIAL_EVIDENCE" not in product_uncertainty:
                product_uncertainty.append(
                    "SEARCH_NO_OFFICIAL_EVIDENCE"
                )
            human_review = True

        review["risk_score"] = fixed_score
        review["status"] = STATUS_BY_SCORE[fixed_score]
        review["score_reason"] = (
            f"{review.get('score_reason', '').strip()} "
            f"[결정론적 적용: {rule['risk_name']} "
            f"{fixed_score}점, {rules['rules_version']}]"
        ).strip()

    output["requires_human_review"] = human_review


def _expression_orders(
    product_results: list[dict[str, Any]],
) -> dict[tuple[int, str], int]:
    orders: dict[tuple[int, str], int] = {}
    order = 0
    for fallback_index, product in enumerate(product_results):
        product_index = int(product.get("product_index", fallback_index))
        for expression in product.get("problem_expressions", []):
            expression_id = str(expression.get("expression_id") or "")
            if not expression_id:
                continue
            order += 1
            orders.setdefault((product_index, expression_id), order)
    return orders


def build_deterministic_aggregation(
    product_results: list[dict[str, Any]],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Count valid occurrences and select both deterministic representatives."""

    rules = rules or load_risk_rules()
    rule_map = rules["violation_rules"]
    orders = _expression_orders(product_results)
    occurrences: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str, str]] = set()

    for fallback_index, product in enumerate(product_results):
        product_index = int(product.get("product_index", fallback_index))
        active_reviews = [
            review
            for review in product.get("violation_reviews", [])
            if review.get("status") in ACTIVE_STATUSES
        ]
        specific_article5_ids = {
            str(expression_id)
            for review in active_reviews
            if review.get("violation_type")
            in {
                "INGREDIENT_TO_PRODUCT_EFFECT",
                "TESTIMONIAL_EFFECT",
                "EXPERT_ENDORSEMENT",
            }
            for expression_id in review.get("expression_ids", [])
        }
        for review in active_reviews:
            violation_type = str(review.get("violation_type") or "")
            rule = rule_map.get(violation_type)
            if not rule or not rule.get("aggregate"):
                continue
            article = int(rule["article_item"])
            score = int(rule["risk_score"])
            for expression_id_value in review.get("expression_ids", []):
                expression_id = str(expression_id_value)
                if (
                    violation_type == "CONSUMER_DECEPTION"
                    and expression_id in specific_article5_ids
                ):
                    continue
                key = (
                    product_index,
                    article,
                    violation_type,
                    expression_id,
                )
                if key in seen:
                    continue
                seen.add(key)
                occurrences.append(
                    {
                        "product_index": product_index,
                        "article_item": article,
                        "violation_type": violation_type,
                        "expression_id": expression_id,
                        "risk_score": score,
                        "order": orders.get(
                            (product_index, expression_id),
                            2_147_483_647,
                        ),
                    }
                )

    article_summaries: list[dict[str, Any]] = []
    article_names = rules["article_names"]
    penalty_priority = {
        int(article): int(priority)
        for article, priority in rules["article_penalty_priority"].items()
    }
    for article in range(1, 6):
        article_occurrences = [
            item for item in occurrences if item["article_item"] == article
        ]
        risk_score = max(
            (item["risk_score"] for item in article_occurrences),
            default=0,
        )
        max_score_items = [
            item
            for item in article_occurrences
            if item["risk_score"] == risk_score and risk_score > 0
        ]
        first_order = min(
            (item["order"] for item in article_occurrences),
            default=None,
        )
        first_max_order = min(
            (item["order"] for item in max_score_items),
            default=None,
        )
        article_summaries.append(
            {
                "article_item": article,
                "article_name": article_names[str(article)],
                "occurrence_count": len(article_occurrences),
                "risk_score": risk_score,
                "highest_risk_occurrence_count": len(max_score_items),
                "first_occurrence_order": first_order,
                "first_highest_risk_occurrence_order": first_max_order,
                "violation_types": sorted(
                    {
                        item["violation_type"]
                        for item in article_occurrences
                    }
                ),
            }
        )

    eligible = [
        item for item in article_summaries if item["occurrence_count"] > 0
    ]
    representatives: dict[int, dict[str, Any]] = {}
    if eligible:
        most_frequent = min(
            eligible,
            key=lambda item: (
                -item["occurrence_count"],
                -item["risk_score"],
                -item["highest_risk_occurrence_count"],
                -penalty_priority.get(item["article_item"], 0),
                item["first_occurrence_order"],
                item["article_item"],
            ),
        )
        highest_risk = min(
            eligible,
            key=lambda item: (
                -item["risk_score"],
                -item["highest_risk_occurrence_count"],
                -item["occurrence_count"],
                -penalty_priority.get(item["article_item"], 0),
                item["first_highest_risk_occurrence_order"],
                item["article_item"],
            ),
        )
        for selected_by, selected in (
            ("most_frequent", most_frequent),
            ("highest_risk", highest_risk),
        ):
            representative = representatives.setdefault(
                selected["article_item"],
                {
                    "article_item": selected["article_item"],
                    "article_name": selected["article_name"],
                    "risk_score": selected["risk_score"],
                    "occurrence_count": selected["occurrence_count"],
                    "selected_by": [],
                },
            )
            representative["selected_by"].append(selected_by)

    return {
        "rules_version": rules["rules_version"],
        "rules_source_sha256": rules["source_sha256"],
        "scope": rules["scope"],
        "score_policy": rules["score_policy"],
        "occurrence_unit": rules["occurrence_unit"],
        "overall_risk_score": max(
            (item["risk_score"] for item in article_summaries),
            default=0,
        ),
        "total_occurrence_count": len(occurrences),
        "article_summaries": article_summaries,
        "representative_types": [
            representatives[key] for key in sorted(representatives)
        ],
    }


def derive_record_evidence_status(
    product_results: list[dict[str, Any]],
) -> str:
    """Summarize evidence sufficiency independently from risk severity.

    A record is sufficient when at least one active violation review has passed
    the score-dependent evidence gate. Other unresolved candidates remain
    available for human review but do not downgrade the record-level evidence
    status.
    """

    reviews = [
        review
        for product in product_results
        for review in product.get("violation_reviews", [])
    ]
    if any(
        review.get("status") in ACTIVE_STATUSES
        for review in reviews
    ):
        return "SUFFICIENT_EVIDENCE"
    if any(
        review.get("status") == "INSUFFICIENT_EVIDENCE"
        for review in reviews
    ):
        return "INSUFFICIENT_EVIDENCE"
    return "NOT_DETECTED"


def risk_rules_for_report(
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an isolated copy for audit/report callers."""

    return deepcopy(rules or load_risk_rules())
