from scripts.run_pipeline import (
    quarantine_invalid_problem_expressions,
    sanitize_unicode_surrogates,
)
from risk_aggregation import apply_deterministic_review_scores


def test_sanitize_unicode_surrogates_recursively() -> None:
    source = {
        "title": "정상 제목",
        "body_text": "앞\udced뒤",
        "nested": ["정상", "\ud800"],
    }

    sanitized = sanitize_unicode_surrogates(source)

    assert sanitized == {
        "title": "정상 제목",
        "body_text": "앞�뒤",
        "nested": ["정상", "�"],
    }
    assert source["body_text"] == "앞\udced뒤"


def test_quarantine_invalid_problem_expression() -> None:
    payload = {"title": "제목", "body_text": "원문에 있는 정확한 표현"}
    output = {
        "problem_expressions": [
            {
                "expression_id": "E1",
                "quote": "정확한 표현",
                "source_field": "body_text",
            },
            {
                "expression_id": "E2",
                "quote": "원문에 없는 요약",
                "source_field": "body_text",
            },
        ],
        "violation_reviews": [
            {
                "expression_ids": ["E2"],
                "risk_score": 8,
                "status": "HIGH",
                "score_reason": "invalid quote",
                "uncertainty_codes": [],
            }
        ],
        "uncertainty_codes": [],
        "requires_human_review": False,
    }

    quarantine_invalid_problem_expressions(payload, output)

    assert [item["expression_id"] for item in output["problem_expressions"]] == [
        "E1"
    ]
    review = output["violation_reviews"][0]
    assert review["expression_ids"] == []
    assert review["risk_score"] == 0
    assert review["status"] == "INSUFFICIENT_EVIDENCE"
    assert "QUOTE_NOT_IN_SOURCE" in review["uncertainty_codes"]
    assert "QUOTE_NOT_IN_SOURCE" in output["uncertainty_codes"]
    assert output["requires_human_review"] is True


def test_high_risk_without_official_evidence_keeps_rule_based_score() -> None:
    output = {
        "problem_expressions": [
            {
                "expression_id": "E1",
                "quote": "질병 치료",
                "source_field": "body_text",
                "product_linked": True,
            },
            {
                "expression_id": "E2",
                "quote": "기능성 과장",
                "source_field": "body_text",
                "product_linked": True,
            },
        ],
        "violation_reviews": [
            {
                "violation_type": "DISEASE_PREVENTION_TREATMENT",
                "status": "HIGH",
                "risk_score": 9,
                "expression_ids": ["E1"],
                "rule_ids": ["RULE-1"],
                "official_evidence_ids": [],
                "score_factors": [],
                "score_reason": "candidate",
                "uncertainty_codes": [],
            },
            {
                "violation_type": "FALSE_EXAGGERATED",
                "status": "HIGH",
                "risk_score": 8,
                "expression_ids": ["E2"],
                "rule_ids": ["RULE-2"],
                "official_evidence_ids": ["OFFICIAL-1"],
                "score_factors": [],
                "score_reason": "candidate",
                "uncertainty_codes": [],
            },
        ],
        "uncertainty_codes": [],
        "requires_human_review": False,
    }

    apply_deterministic_review_scores(output)

    assert output["violation_reviews"][0]["risk_score"] == 10
    assert output["violation_reviews"][0]["status"] == "HIGH"
    assert (
        "SEARCH_NO_OFFICIAL_EVIDENCE"
        in output["violation_reviews"][0]["uncertainty_codes"]
    )
    assert output["violation_reviews"][1]["risk_score"] == 8
    assert output["violation_reviews"][1]["status"] == "HIGH"
    assert "SEARCH_NO_OFFICIAL_EVIDENCE" in output["uncertainty_codes"]
    assert output["requires_human_review"] is True
