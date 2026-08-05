from result_partition import independent_review_output, legal_basis_details


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
        "stage1": {
            "products": [
                {
                    "product_index": 0,
                    "food_confidence": 0.82,
                    "hff_confidence": 0.18,
                }
            ]
        },
        "product_results": [
            {
                "product_index": 0,
                "product_name": "테스트 제품",
                "problem_expressions": [
                    {
                        "expression_id": "EXP-1",
                        "quote": "질병이 치료됩니다",
                        "source_field": "body_text",
                        "product_linked": True,
                    }
                ],
                "violation_reviews": list(reviews),
                "file_search": {
                    "citations": [
                        {
                            "record_id": "RULE-1",
                            "file_name": "rules.md",
                            "source": "file-rule",
                            "page": None,
                            "excerpt": "질병 치료 표현 적용 기준",
                        },
                        {
                            "record_id": "LAW-1",
                            "file_name": "official.md",
                            "source": "file-law",
                            "page": 3,
                            "excerpt": "공식 근거 인용문",
                        },
                    ]
                },
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
    assert report["independent_findings"][0]["food_confidence"] == 0.82
    assert report["independent_findings"][0]["hff_confidence"] == 0.18
    assert [
        item["violation_type"] for item in report["unresolved_findings"]
    ] == ["CONSUMER_DECEPTION"]
    finding = report["independent_findings"][0]
    assert finding["problem_expressions"][0]["quote"] == "질병이 치료됩니다"
    assert (
        finding["evidence_details"]["rules"][0]["file_name"]
        == "rules.md"
    )
    assert (
        finding["evidence_details"]["official_evidence"][0]["excerpt"]
        == "공식 근거 인용문"
    )


def test_report_contains_only_independent_review_fields() -> None:
    report = independent_review_output(
        output_with(review("FALSE_EXAGGERATED", "REVIEW", 6))
    )

    assert set(report) == {
        "record_id",
        "independent_findings",
        "unresolved_findings",
        "independent_findings_scope",
        "deterministic_aggregation",
        "raw_model_summary",
    }


def test_legal_basis_resolves_article_and_official_search_status() -> None:
    verified = legal_basis_details(
        ["RULE::FOOD_REVIEW::MFDS-05-DEC-001"],
        ["OFFICIAL-1"],
    )
    assert verified[0]["article"] == "시행령 별표 1 제1호"
    assert verified[0]["legal_basis_status"] == "RULE_MAPPED"
    assert verified[0]["official_evidence_status"] == "OFFICIAL_SEARCH_VERIFIED"

    review_required = legal_basis_details(
        ["RULE::FOOD_REVIEW::MFDS-05-DEC-001"],
        [],
    )
    assert (
        review_required[0]["official_evidence_status"]
        == "OFFICIAL_SEARCH_REVIEW_REQUIRED"
    )
