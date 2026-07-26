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
