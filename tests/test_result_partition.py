from result_partition import independent_review_output


def review(
    violation_type: str,
    status: str,
    risk_score: int,
) -> dict:
    return {
        "violation_type": violation_type,
        "status": status,
        "risk_score": risk_score,
        "expression_ids": ["EXP-1"] if risk_score else [],
        "rule_ids": ["RULE-1"] if risk_score else [],
        "official_evidence_ids": ["LAW-1"] if risk_score else [],
        "case_ids": [],
        "score_factors": [],
        "score_reason": "test",
        "uncertainty_codes": [],
    }


def output_with(*reviews: dict) -> dict:
    return {
        "record_id": "T-001",
        "product_results": [
            {
                "product_index": 0,
                "product_name": "테스트 제품",
                "violation_reviews": list(reviews),
            }
        ],
        "record_overall_status": "HIGH",
        "record_overall_risk_score": 10,
        "requires_human_review": True,
        "error_codes": [],
    }


def test_returns_only_detected_independent_findings() -> None:
    output = output_with(
        review("HFF_CONFUSION", "HIGH", 9),
        review("DISEASE_PREVENTION_TREATMENT", "HIGH", 10),
        review("MEDICINE_CONFUSION", "NOT_DETECTED", 0),
        review("CONSUMER_DECEPTION", "INSUFFICIENT_EVIDENCE", 0),
    )

    report = independent_review_output(output)

    assert [
        item["violation_type"] for item in report["independent_findings"]
    ] == ["DISEASE_PREVENTION_TREATMENT", "HFF_CONFUSION"]


def test_report_contains_only_independent_review_fields() -> None:
    report = independent_review_output(
        output_with(review("FALSE_EXAGGERATED", "REVIEW", 6))
    )

    assert set(report) == {
        "record_id",
        "independent_findings",
        "independent_findings_scope",
        "raw_model_summary",
    }
