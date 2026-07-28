from __future__ import annotations

from risk_aggregation import (
    apply_deterministic_review_scores,
    build_deterministic_aggregation,
    derive_record_evidence_status,
    load_risk_rules,
)


def expression(expression_id: str) -> dict:
    return {
        "expression_id": expression_id,
        "quote": expression_id,
        "source_field": "body_text",
        "product_linked": True,
    }


def review(
    violation_type: str,
    expression_ids: list[str],
    *,
    score: int,
    official: bool = True,
    rule: bool = True,
) -> dict:
    return {
        "violation_type": violation_type,
        "status": "HIGH" if score >= 8 else "REVIEW",
        "risk_score": score,
        "expression_ids": expression_ids,
        "rule_ids": ["RULE-1"] if rule else [],
        "official_evidence_ids": ["OFFICIAL-1"] if official else [],
        "case_ids": [],
        "score_factors": [],
        "score_reason": "model candidate",
        "uncertainty_codes": [],
    }


def test_rule_table_is_complete_for_article_one_to_five() -> None:
    rules = load_risk_rules()

    articles = {
        item["article_item"]
        for item in rules["violation_rules"].values()
        if item["aggregate"]
    }

    assert articles == {1, 2, 3, 4, 5}
    assert rules["rules_version"] == "2026-07-28-poc-1"


def test_model_scores_are_replaced_by_fixed_rules() -> None:
    output = {
        "problem_expressions": [expression("E1"), expression("E2")],
        "violation_reviews": [
            review(
                "DISEASE_PREVENTION_TREATMENT",
                ["E1"],
                score=4,
            ),
            review("TESTIMONIAL_EFFECT", ["E2"], score=2),
        ],
        "uncertainty_codes": [],
        "requires_human_review": False,
    }

    apply_deterministic_review_scores(output)

    assert output["violation_reviews"][0]["risk_score"] == 10
    assert output["violation_reviews"][0]["status"] == "HIGH"
    assert output["violation_reviews"][1]["risk_score"] == 7
    assert output["violation_reviews"][1]["status"] == "REVIEW"
    assert "2026-07-28-poc-1" in output["violation_reviews"][0][
        "score_reason"
    ]


def test_representatives_follow_frequency_and_risk_tie_breaks() -> None:
    expression_ids = [f"E{index}" for index in range(1, 16)]
    product = {
        "product_index": 0,
        "problem_expressions": [
            expression(expression_id) for expression_id in expression_ids
        ],
        "violation_reviews": [
            review("DISEASE_PREVENTION_TREATMENT", ["E1"], score=10),
            review("MEDICINE_CONFUSION", ["E2", "E3"], score=10),
            review(
                "HFF_CONFUSION",
                ["E4", "E5", "E6", "E7", "E8"],
                score=9,
            ),
            review("FALSE_EXAGGERATED", ["E9", "E10", "E11"], score=8),
            review(
                "INGREDIENT_TO_PRODUCT_EFFECT",
                ["E12", "E13"],
                score=8,
            ),
            review("TESTIMONIAL_EFFECT", ["E14", "E15"], score=7),
            review("CONSUMER_DECEPTION", ["E12"], score=7),
        ],
    }

    result = build_deterministic_aggregation([product])

    summaries = {
        item["article_item"]: item for item in result["article_summaries"]
    }
    assert summaries[1]["occurrence_count"] == 1
    assert summaries[2]["occurrence_count"] == 2
    assert summaries[3]["occurrence_count"] == 5
    assert summaries[4]["occurrence_count"] == 3
    assert summaries[5]["occurrence_count"] == 4
    assert result["total_occurrence_count"] == 15
    assert result["overall_risk_score"] == 10
    assert result["representative_types"] == [
        {
            "article_item": 2,
            "article_name": "의약품 오인·혼동",
            "risk_score": 10,
            "occurrence_count": 2,
            "selected_by": ["highest_risk"],
        },
        {
            "article_item": 3,
            "article_name": "건강기능식품 오인·혼동",
            "risk_score": 9,
            "occurrence_count": 5,
            "selected_by": ["most_frequent"],
        },
    ]


def test_same_representative_is_returned_once_with_both_reasons() -> None:
    product = {
        "product_index": 0,
        "problem_expressions": [expression("E1")],
        "violation_reviews": [
            review("DISEASE_PREVENTION_TREATMENT", ["E1", "E1"], score=10)
        ],
    }

    result = build_deterministic_aggregation([product])

    assert result["total_occurrence_count"] == 1
    assert result["representative_types"] == [
        {
            "article_item": 1,
            "article_name": "질병 예방·치료 효능",
            "risk_score": 10,
            "occurrence_count": 1,
            "selected_by": ["most_frequent", "highest_risk"],
        }
    ]


def test_missing_rule_is_unresolved_and_not_scored() -> None:
    output = {
        "problem_expressions": [expression("E1")],
        "violation_reviews": [
            review(
                "DISEASE_PREVENTION_TREATMENT",
                ["E1"],
                score=10,
                rule=False,
            )
        ],
        "uncertainty_codes": [],
        "requires_human_review": False,
    }

    apply_deterministic_review_scores(output)

    finding = output["violation_reviews"][0]
    assert finding["risk_score"] == 0
    assert finding["status"] == "INSUFFICIENT_EVIDENCE"
    assert "SEARCH_NO_RULE" in finding["uncertainty_codes"]
    assert "SEARCH_NO_RULE" in output["uncertainty_codes"]
    assert output["requires_human_review"] is True


def test_review_score_also_requires_official_evidence() -> None:
    output = {
        "problem_expressions": [expression("E1")],
        "violation_reviews": [
            review(
                "CONSUMER_DECEPTION",
                ["E1"],
                score=7,
                official=False,
            )
        ],
        "uncertainty_codes": [],
        "requires_human_review": False,
    }

    apply_deterministic_review_scores(output)

    finding = output["violation_reviews"][0]
    assert finding["risk_score"] == 0
    assert finding["status"] == "INSUFFICIENT_EVIDENCE"
    assert "SEARCH_NO_OFFICIAL_EVIDENCE" in finding["uncertainty_codes"]


def test_supported_candidate_makes_record_evidence_sufficient() -> None:
    supported = review(
        "CONSUMER_DECEPTION",
        ["E1"],
        score=7,
        official=True,
    )
    unresolved = review(
        "FALSE_EXAGGERATED",
        ["E2"],
        score=8,
        official=False,
    )
    unresolved["status"] = "INSUFFICIENT_EVIDENCE"
    unresolved["risk_score"] = 0

    status = derive_record_evidence_status(
        [{"violation_reviews": [supported, unresolved]}]
    )

    assert status == "SUFFICIENT_EVIDENCE"


def test_only_unresolved_candidates_are_insufficient() -> None:
    unresolved = review(
        "CONSUMER_DECEPTION",
        ["E1"],
        score=7,
        official=False,
    )
    unresolved["status"] = "INSUFFICIENT_EVIDENCE"
    unresolved["risk_score"] = 0

    status = derive_record_evidence_status(
        [{"violation_reviews": [unresolved]}]
    )

    assert status == "INSUFFICIENT_EVIDENCE"


def test_item_one_wins_exact_item_one_item_two_penalty_tie() -> None:
    product = {
        "product_index": 0,
        "problem_expressions": [expression("E1"), expression("E2")],
        "violation_reviews": [
            review("MEDICINE_CONFUSION", ["E1"], score=10),
            review("DISEASE_PREVENTION_TREATMENT", ["E2"], score=10),
        ],
    }

    result = build_deterministic_aggregation([product])

    assert result["representative_types"] == [
        {
            "article_item": 1,
            "article_name": result["article_summaries"][0]["article_name"],
            "risk_score": 10,
            "occurrence_count": 1,
            "selected_by": ["most_frequent", "highest_risk"],
        }
    ]
