from __future__ import annotations

from scripts.run_pipeline import failure_record


def test_store_discovery_failure_is_classified_and_redacted() -> None:
    failure = failure_record(
        {"record_id": "T1"},
        "openai",
        "aggregate",
        RuntimeError(
            "FILE_SEARCH_STORE_DISCOVERY_FAILED "
            "vs_sensitive proj_sensitive org_sensitive"
        ),
    )

    assert failure["error_code"] == "FILE_SEARCH_STORE_UNAVAILABLE"
    assert "vs_sensitive" not in failure["message"]
    assert "proj_sensitive" not in failure["message"]
    assert "org_sensitive" not in failure["message"]


def test_quota_failure_is_classified() -> None:
    failure = failure_record(
        {"record_id": "T2"},
        "openai",
        "aggregate",
        RuntimeError("PROVIDER_QUOTA_EXCEEDED"),
    )

    assert failure["error_code"] == "PROVIDER_QUOTA_EXCEEDED"


def test_schema_failure_is_not_misclassified_by_allowed_code_text() -> None:
    failure = failure_record(
        {"record_id": "T3"},
        "openai",
        "stage1",
        RuntimeError(
            "JSON_SCHEMA_INVALID: products/3/uncertainty_codes/1: "
            "'PRODUCT_TYPE_UNCERTAIN_REVIEW' is not one of "
            "['JSON_SCHEMA_INVALID', 'PROVIDER_MODEL_UNSUPPORTED']"
        ),
    )

    assert failure["error_code"] == "JSON_SCHEMA_INVALID"


def test_retrieved_id_failure_is_classified() -> None:
    failure = failure_record(
        {"record_id": "T4"},
        "openai",
        "stage2",
        RuntimeError("RETRIEVED_ID_NOT_FOUND: ['food_review.md']"),
    )

    assert failure["error_code"] == "RETRIEVED_ID_NOT_FOUND"
