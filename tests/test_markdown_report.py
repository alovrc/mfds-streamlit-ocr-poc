from markdown_report import build_markdown_report
from risk_aggregation import build_deterministic_aggregation


def sample_output() -> dict:
    return {
        "record_id": "T-001",
        "stage1": {
            "record_product_type": "FOOD_FALLBACK",
            "product_presence": "CONFIRMED",
            "sales_ad_context": "CONFIRMED",
            "short_reason": "식품 후보 경로",
            "uncertainty_codes": ["HFF_DB_NO_MATCH"],
            "products": [
                {
                    "product_index": 0,
                    "product_type": "FOOD_FALLBACK",
                    "food_confidence": 0.72,
                    "hff_confidence": 0.24,
                    "confidence": 0.72,
                    "uncertainty_codes": ["HFF_DB_NO_MATCH"],
                }
            ],
            "routes": [
                {
                    "product_index": 0,
                    "stage2_route": "FOOD_REVIEW",
                    "store_alias": "FS11_FOOD_REVIEW",
                }
            ],
            "file_search": {
                "store_alias": "FS01_PRODUCT_GATE",
                "file_search_run": True,
                "retrieved_ids": ["PRODUCT::1"],
                "citations": [
                    {
                        "record_id": "PRODUCT::1",
                        "file_name": "product.md",
                        "source": "file-1",
                        "page": None,
                        "excerpt": "식품 분류 검색 발췌문",
                    }
                ],
                "latency_ms": 1200,
            },
        },
        "product_results": [
            {
                "product_index": 0,
                "product_name": "테스트 제품",
                "product_type": "FOOD_FALLBACK",
                "problem_expressions": [
                    {
                        "expression_id": "E01",
                        "quote": "질병이 치료됩니다",
                        "source_field": "body_text",
                        "product_linked": True,
                    }
                ],
                "violation_reviews": [
                    {
                        "violation_type": "DISEASE_PREVENTION_TREATMENT",
                        "status": "HIGH",
                        "risk_score": 10,
                        "expression_ids": ["E01"],
                        "rule_ids": ["RULE-1"],
                        "official_evidence_ids": ["LAW-1"],
                        "case_ids": [],
                        "score_reason": "질병 치료 표현",
                        "uncertainty_codes": [],
                    }
                ],
                "uncertainty_codes": [],
                "file_search": {
                    "store_alias": "FS11_FOOD_REVIEW",
                    "file_search_run": True,
                    "retrieved_ids": ["LAW-1"],
                    "citations": [
                        {
                            "record_id": "LAW-1",
                            "file_name": "law.md",
                            "source": "file-2",
                            "page": 3,
                            "excerpt": "질병 치료 광고 공식 근거",
                        }
                    ],
                    "latency_ms": 2300,
                },
            }
        ],
        "record_overall_status": "HIGH",
        "record_overall_risk_score": 10,
        "requires_human_review": True,
        "error_codes": [],
    }


def test_markdown_report_contains_decision_and_traceability() -> None:
    output = sample_output()
    output["deterministic_aggregation"] = build_deterministic_aggregation(
        output["product_results"]
    )
    report = build_markdown_report(
        output,
        "openai",
        {
            "platform": "네이버 블로그",
            "title": "테스트 광고",
            "source_url": "https://example.test/post",
        },
        generated_at="2026-07-27T12:00:00+09:00",
    )

    assert "# MFDS Cloud File Search 결과보고서" in report
    assert "식품 confidence | 건기식 confidence" in report
    assert "질병의 예방·치료 효능" in report
    assert "“질병이 치료됩니다”" in report
    assert "RULE-1" in report
    assert "LAW-1" in report
    assert "FS01_PRODUCT_GATE" in report
    assert "FS11_FOOD_REVIEW" in report
    assert "file-2" in report
    assert "질병 치료 광고 공식 근거" in report
    assert "공식 검색근거·인용문" in report
    assert "최종 판단 전 담당자" in report
    assert "결정론적 위험도·대표유형 집계" in report
    assert "2026-07-28-poc-1" in report
    assert "최다빈도, 최고위험" in report


def test_markdown_report_deduplicates_citations_per_store() -> None:
    output = sample_output()
    output["product_results"][0]["file_search"]["citations"].append(
        dict(output["product_results"][0]["file_search"]["citations"][0])
    )

    report = build_markdown_report(
        output,
        "openai",
        generated_at="2026-07-27T12:00:00+09:00",
    )

    assert report.count("| file-2 |") == 1


def test_markdown_report_hides_optional_official_evidence_miss() -> None:
    output = sample_output()
    review = output["product_results"][0]["violation_reviews"][0]
    review["official_evidence_ids"] = []
    review["uncertainty_codes"] = ["SEARCH_NO_OFFICIAL_EVIDENCE"]
    output["product_results"][0]["uncertainty_codes"] = [
        "SEARCH_NO_OFFICIAL_EVIDENCE"
    ]
    output["error_codes"] = ["SEARCH_NO_OFFICIAL_EVIDENCE"]

    report = build_markdown_report(
        output,
        "openai",
        generated_at="2026-07-27T12:00:00+09:00",
    )

    assert "SEARCH_NO_OFFICIAL_EVIDENCE" not in report
    assert "보조 공식근거 미검색" not in report
