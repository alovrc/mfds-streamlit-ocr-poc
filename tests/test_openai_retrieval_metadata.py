from __future__ import annotations

from adapters.openai.client import _retrieval_metadata


def test_file_result_without_record_id_keeps_file_level_citation() -> None:
    response = {
        "output": [
            {
                "type": "file_search_call",
                "results": [
                    {
                        "file_id": "file-FOODCODE",
                        "filename": "food_code_2026_40_ch1_5.md",
                        "text": "식품의 기준 및 규격 고시전문 본문",
                    }
                ],
            }
        ]
    }

    file_search_run, retrieved_ids, citations = _retrieval_metadata(response)

    assert file_search_run is True
    assert retrieved_ids == ["file-FOODCODE"]
    assert len(citations) == 1
    assert citations[0].record_id == "file-FOODCODE"
    assert citations[0].file_name == "food_code_2026_40_ch1_5.md"
    assert citations[0].source == "file-FOODCODE"


def test_record_level_citations_still_take_precedence() -> None:
    response = {
        "output": [
            {
                "type": "file_search_call",
                "results": [
                    {
                        "file_id": "file-CASE",
                        "filename": "cases.md",
                        "text": "record_id: CASE-001",
                    }
                ],
            }
        ]
    }

    _, retrieved_ids, citations = _retrieval_metadata(response)

    assert retrieved_ids == ["CASE-001"]
    assert citations[0].record_id == "CASE-001"
    assert citations[0].file_name == "cases.md"
