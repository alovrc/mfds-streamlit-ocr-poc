from __future__ import annotations

import json

from adapters.openai import client as openai_client
from rule_catalog import attach_local_rules, load_rule_catalog
from scripts.run_pipeline import attach_rules_to_tracking


def candidate(
    violation_type: str,
    *,
    rule_ids: list[str] | None = None,
) -> dict:
    return {
        "violation_type": violation_type,
        "status": "HIGH",
        "risk_score": 10,
        "expression_ids": ["E1"],
        "rule_ids": rule_ids or [],
        "official_evidence_ids": ["OFFICIAL::1"],
        "case_ids": [],
        "score_factors": [],
        "score_reason": "candidate",
        "uncertainty_codes": ["SEARCH_NO_RULE"],
    }


def test_catalog_is_versioned_and_contains_primary_active_rules() -> None:
    catalog = load_rule_catalog()

    assert catalog["catalog_version"] == "2026-07-28-poc-1"
    assert len(catalog["rules"]) == 7
    assert all(rule["status"] == "ACTIVE" for rule in catalog["rules"])
    assert {
        rule["violation_type"] for rule in catalog["rules"]
    } >= {
        "DISEASE_PREVENTION_TREATMENT",
        "MEDICINE_CONFUSION",
        "HFF_CONFUSION",
        "FALSE_EXAGGERATED",
        "CONSUMER_DECEPTION",
    }


def test_catalog_replaces_model_rule_id_and_adds_official_citation() -> None:
    review = candidate(
        "DISEASE_PREVENTION_TREATMENT",
        rule_ids=["RULE::MODEL::INVENTED"],
    )
    data = {
        "violation_reviews": [review],
        "uncertainty_codes": ["SEARCH_NO_RULE"],
    }

    ids, citations = attach_local_rules(
        data=data,
        store_alias="FS21_HFF_REVIEW",
    )

    assert ids == ["RULE::HFF_REVIEW::MFDS-05-DEC-001"]
    assert review["rule_ids"] == ids
    assert "SEARCH_NO_RULE" not in review["uncertainty_codes"]
    assert "SEARCH_NO_RULE" not in data["uncertainty_codes"]
    assert citations[0].record_id == ids[0]
    assert citations[0].file_name == "rule_catalog.json"
    assert citations[0].source.startswith("https://www.law.go.kr/")
    assert "시행령 별표 1 제1호" in citations[0].excerpt


def test_detailed_consumer_types_share_primary_item5_rule() -> None:
    data = {
        "violation_reviews": [
            candidate("INGREDIENT_TO_PRODUCT_EFFECT"),
            candidate("TESTIMONIAL_EFFECT"),
            candidate("EXPERT_ENDORSEMENT"),
        ],
        "uncertainty_codes": [],
    }

    ids, _ = attach_local_rules(
        data=data,
        store_alias="FS11_FOOD_REVIEW",
    )

    assert ids == ["RULE::FOOD_REVIEW::MFDS-05-DEC-005"]
    assert all(
        review["rule_ids"] == ids
        for review in data["violation_reviews"]
    )


def test_comparison_candidate_maps_to_items6_and7_without_risk_rule_search() -> None:
    data = {
        "violation_reviews": [candidate("COMPARISON_DEFAMATION")],
        "uncertainty_codes": [],
    }

    ids, _ = attach_local_rules(
        data=data,
        store_alias="FS11_FOOD_REVIEW",
    )

    assert ids == [
        "RULE::FOOD_REVIEW::MFDS-05-DEC-006",
        "RULE::FOOD_REVIEW::MFDS-05-DEC-007",
    ]


def test_unknown_candidate_remains_unresolved() -> None:
    review = candidate("UNKNOWN_TYPE")
    data = {"violation_reviews": [review], "uncertainty_codes": []}

    ids, citations = attach_local_rules(
        data=data,
        store_alias="FS11_FOOD_REVIEW",
    )

    assert ids == []
    assert citations == []
    assert review["rule_ids"] == []
    assert "SEARCH_NO_RULE" in review["uncertainty_codes"]
    assert "SEARCH_NO_RULE" in data["uncertainty_codes"]


def test_tracking_contains_local_rule_citation() -> None:
    review = candidate("MEDICINE_CONFUSION")
    output = {
        "violation_reviews": [review],
        "uncertainty_codes": [],
        "file_search": {
            "retrieved_ids": ["OFFICIAL::1"],
            "citations": [
                {
                    "record_id": "OFFICIAL::1",
                    "file_name": "official.md",
                    "source": "file-1",
                    "page": None,
                    "excerpt": "official evidence",
                }
            ],
        },
    }

    attach_rules_to_tracking(output, "FS21_HFF_REVIEW")

    assert "RULE::HFF_REVIEW::MFDS-05-DEC-002" in output[
        "file_search"
    ]["retrieved_ids"]
    assert any(
        citation["file_name"] == "rule_catalog.json"
        for citation in output["file_search"]["citations"]
    )


class FakeResponse:
    id = "resp-test"
    output_text = json.dumps({"violation_reviews": []})

    def model_dump(self, mode: str = "json") -> dict:
        return {
            "output": [
                {
                    "type": "file_search_call",
                    "results": [],
                }
            ]
        }


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse()


class FakeVectorStores:
    def search(self, **kwargs):
        raise AssertionError("Rule vector search must not be called")


class FakeOpenAI:
    def __init__(self) -> None:
        self.responses = FakeResponses()
        self.vector_stores = FakeVectorStores()


def test_openai_stage_makes_one_response_call_and_zero_vector_searches(
    monkeypatch,
) -> None:
    fake = FakeOpenAI()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(openai_client, "OpenAI", lambda **kwargs: fake)
    monkeypatch.setattr(
        openai_client,
        "_resolve_vector_store_id",
        lambda client, store_alias: "vs-test",
    )

    result = openai_client.run(
        system_prompt="prompt",
        payload={"record_id": "R1"},
        schema_name="stage2_output.schema.json",
        store_alias="FS21_HFF_REVIEW",
    )

    assert len(fake.responses.calls) == 1
    assert result.supplemental_queries == []
