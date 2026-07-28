from __future__ import annotations

import sqlite3

import product_master
from product_master import (
    ProductMasterLookup,
    ProductMasterMatch,
    lookup_product,
    normalize_product_name,
)
from scripts.run_pipeline import apply_product_master_lookup


def test_normalize_product_name_is_exact_but_separator_tolerant() -> None:
    assert normalize_product_name(" 프리미엄  덴티시브 ") == "프리미엄덴티시브"
    assert normalize_product_name("ABC-123") == "abc123"


def test_unique_exact_lookup(tmp_path, monkeypatch) -> None:
    database = tmp_path / "master.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute(
            """
            CREATE TABLE products (
                record_id INTEGER PRIMARY KEY,
                product_name TEXT,
                business_name TEXT,
                product_type_name TEXT,
                functionality TEXT,
                normalized_product_name TEXT,
                normalized_business_name TEXT
            )
            """
        )
        db.execute(
            "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "프리미엄 덴티시브",
                "(주)비오팜",
                "프로폴리스추출물",
                "항산화에 도움을 줄 수 있음",
                "프리미엄덴티시브",
                "(주)비오팜",
            ),
        )
    monkeypatch.setattr(product_master, "resolve_product_master", lambda: database)

    lookup = lookup_product("프리미엄  덴티시브")

    assert lookup.status == "EXACT_UNIQUE"
    assert lookup.matches[0].business_name == "(주)비오팜"


def test_exact_master_match_forces_hff_route() -> None:
    output = {
        "record_product_type": "FOOD_FALLBACK",
        "products": [
            {
                "product_index": 0,
                "product_name": "프리미엄 덴티시브",
                "product_type": "FOOD_FALLBACK",
                "product_subtype": "UNKNOWN_FOOD",
                "confidence": 0.55,
                "food_confidence": 0.55,
                "hff_confidence": 0.48,
                "analysis_target": True,
                "evidence_ids": [],
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
        "uncertainty_codes": ["HFF_DB_NO_MATCH"],
        "requires_human_review": True,
        "short_reason": "모델 분류",
    }
    lookup = ProductMasterLookup(
        query="프리미엄 덴티시브",
        status="EXACT_UNIQUE",
        matches=(
            ProductMasterMatch(
                record_id=42,
                product_name="프리미엄 덴티시브",
                business_name="(주)비오팜",
                product_type_name="프로폴리스추출물",
                functionality="항산화",
            ),
        ),
    )

    apply_product_master_lookup(output, lookup)

    product = output["products"][0]
    assert output["record_product_type"] == "HEALTH_FUNCTIONAL_FOOD"
    assert product["hff_confidence"] == 1.0
    assert product["evidence_ids"] == ["HFF_MASTER::42"]
    assert output["routes"][0]["store_alias"] == "FS21_HFF_REVIEW"
    assert "HFF_DB_NO_MATCH" not in output["uncertainty_codes"]


def test_ambiguous_name_does_not_force_hff() -> None:
    output = {
        "products": [
            {
                "product_index": 0,
                "product_type": "FOOD_FALLBACK",
                "uncertainty_codes": [],
            }
        ],
        "uncertainty_codes": [],
        "requires_human_review": False,
    }
    match = ProductMasterMatch(1, "중복제품", "업체", "유형", "기능성")
    lookup = ProductMasterLookup(
        query="중복제품",
        status="AMBIGUOUS",
        matches=(match, match),
    )

    apply_product_master_lookup(output, lookup)

    assert output["products"][0]["product_type"] == "FOOD_FALLBACK"
    assert "CONFLICTING_PRODUCT_TYPE_EVIDENCE" in output["uncertainty_codes"]
