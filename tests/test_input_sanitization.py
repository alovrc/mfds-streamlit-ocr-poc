from scripts.run_pipeline import (
    cap_high_risk_without_official_evidence,
    quarantine_invalid_problem_expressions,
    sanitize_unicode_surrogates,
)


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


def test_cap_high_risk_without_official_evidence() -> None:
    output = {
        "violation_reviews": [
            {
                "risk_score": 9,
                "official_evidence_ids": [],
                "uncertainty_codes": [],
            },
            {
                "risk_score": 8,
                "official_evidence_ids": ["OFFICIAL-1"],
                "uncertainty_codes": [],
            },
        ],
        "uncertainty_codes": [],
        "requires_human_review": False,
    }

    cap_high_risk_without_official_evidence(output)

    assert output["violation_reviews"][0]["risk_score"] == 7
    assert (
        "SEARCH_NO_OFFICIAL_EVIDENCE"
        in output["violation_reviews"][0]["uncertainty_codes"]
    )
    assert output["violation_reviews"][1]["risk_score"] == 8
    assert "SEARCH_NO_OFFICIAL_EVIDENCE" in output["uncertainty_codes"]
    assert output["requires_human_review"] is True
