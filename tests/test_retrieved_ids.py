from __future__ import annotations

from validators.core import validate_retrieved_ids


def test_retrieved_file_name_alias_is_accepted() -> None:
    output = {
        "file_search": {
            "retrieved_ids": ["file-FOOD-001"],
            "citations": [
                {
                    "record_id": "file-FOOD-001",
                    "file_name": "fs11_food_review_0017.md",
                    "source": "file-FOOD-001",
                }
            ],
        },
        "violation_reviews": [
            {
                "rule_ids": [],
                "official_evidence_ids": ["fs11_food_review_0017.md"],
                "case_ids": [],
            }
        ],
    }

    validate_retrieved_ids(output)
