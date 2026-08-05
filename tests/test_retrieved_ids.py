from __future__ import annotations

from scripts.run_pipeline import quarantine_unretrieved_evidence_ids
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


def test_unretrieved_evidence_is_quarantined_for_human_review() -> None:
    output = {
        "file_search": {
            "retrieved_ids": ["file-FOOD-001"],
            "citations": [],
        },
        "requires_human_review": False,
        "uncertainty_codes": [],
        "violation_reviews": [
            {
                "official_evidence_ids": ["MFDS-L-HFF43-0059"],
                "case_ids": [],
                "uncertainty_codes": [],
            }
        ],
    }

    quarantine_unretrieved_evidence_ids(output)

    review = output["violation_reviews"][0]
    assert review["official_evidence_ids"] == []
    assert "RETRIEVED_ID_NOT_FOUND" in review["uncertainty_codes"]
    assert "SEARCH_NO_OFFICIAL_EVIDENCE" in review["uncertainty_codes"]
    assert output["requires_human_review"] is True
