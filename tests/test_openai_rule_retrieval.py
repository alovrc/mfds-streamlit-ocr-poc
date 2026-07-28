from __future__ import annotations

from adapters.openai.client import (
    _retrieve_missing_rules,
    _vector_search_metadata,
)


def review() -> dict:
    return {
        "violation_type": "MEDICINE_CONFUSION",
        "status": "HIGH",
        "risk_score": 10,
        "expression_ids": ["E1"],
        "rule_ids": [],
        "uncertainty_codes": ["SEARCH_NO_RULE"],
    }


def response(record_id: str | None) -> dict:
    text = f"record_id: {record_id}\n적용 Rule" if record_id else "근거 없음"
    return {
        "data": [
            {
                "file_id": "file-rule",
                "filename": "rule.md",
                "content": [{"type": "text", "text": text}],
            }
        ]
    }


class FakeVectorStores:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[dict]) -> None:
        self.vector_stores = FakeVectorStores(responses)


def payload_and_data() -> tuple[dict, dict]:
    payload = {
        "product_type": "HEALTH_FUNCTIONAL_FOOD",
        "product_subtype": "NOT_APPLICABLE",
        "stage1_product": {"product_name": "프리미엄 덴티시브"},
    }
    data = {
        "problem_expressions": [
            {
                "expression_id": "E1",
                "quote": "잇몸약 성분",
            }
        ],
        "violation_reviews": [review()],
        "uncertainty_codes": ["SEARCH_NO_RULE"],
    }
    return payload, data


def test_vector_search_metadata_accepts_only_rule_ids() -> None:
    result = {
        "data": [
            {
                "file_id": "file-rule",
                "filename": "rule.md",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "record_id: RULE::HFF_REVIEW::001\n"
                            "record_id: OFFICIAL_EVIDENCE::002"
                        ),
                    }
                ],
            }
        ]
    }

    ids, citations = _vector_search_metadata(result)

    assert ids == ["RULE::HFF_REVIEW::001"]
    assert citations[0].record_id == "RULE::HFF_REVIEW::001"


def test_filtered_rule_search_injects_rule_and_citation() -> None:
    payload, data = payload_and_data()
    client = FakeClient([response("RULE::HFF_REVIEW::MFDS-001")])

    ids, citations, queries = _retrieve_missing_rules(
        client=client,
        vector_store_id="vs-test",
        payload=payload,
        data=data,
    )

    assert ids == ["RULE::HFF_REVIEW::MFDS-001"]
    assert citations[0].record_id == ids[0]
    assert data["violation_reviews"][0]["rule_ids"] == ids
    assert "SEARCH_NO_RULE" not in data["violation_reviews"][0][
        "uncertainty_codes"
    ]
    assert "SEARCH_NO_RULE" not in data["uncertainty_codes"]
    assert len(queries) == 1
    filters = client.vector_stores.calls[0]["filters"]["filters"]
    assert {item["key"]: item["value"] for item in filters} == {
        "record_class": "RULE",
        "violation_type": "MEDICINE_CONFUSION",
        "active": True,
    }


def test_filtered_rule_search_replaces_unfiltered_model_rule_ids() -> None:
    payload, data = payload_and_data()
    data["violation_reviews"][0]["rule_ids"] = ["RULE::MIXED::OLD"]
    client = FakeClient([response("RULE::HFF_REVIEW::FILTERED")])

    ids, _, _ = _retrieve_missing_rules(
        client=client,
        vector_store_id="vs-test",
        payload=payload,
        data=data,
    )

    assert ids == ["RULE::HFF_REVIEW::FILTERED"]
    assert data["violation_reviews"][0]["rule_ids"] == ids


def test_empty_first_search_uses_different_fallback_query() -> None:
    payload, data = payload_and_data()
    client = FakeClient(
        [
            response(None),
            response("RULE::HFF_REVIEW::MFDS-002"),
        ]
    )

    ids, _, queries = _retrieve_missing_rules(
        client=client,
        vector_store_id="vs-test",
        payload=payload,
        data=data,
    )

    assert ids == ["RULE::HFF_REVIEW::MFDS-002"]
    assert len(queries) == 2
    assert queries[0] != queries[1]


def test_two_empty_searches_keep_search_no_rule() -> None:
    payload, data = payload_and_data()
    client = FakeClient([response(None), response(None)])

    ids, citations, queries = _retrieve_missing_rules(
        client=client,
        vector_store_id="vs-test",
        payload=payload,
        data=data,
    )

    assert ids == []
    assert citations == []
    assert len(queries) == 2
    assert data["violation_reviews"][0]["rule_ids"] == []
    assert "SEARCH_NO_RULE" in data["violation_reviews"][0][
        "uncertainty_codes"
    ]
