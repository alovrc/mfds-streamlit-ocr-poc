#!/usr/bin/env python3
"""Build and validate the approved public HFF product-master SQLite asset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3
import sys
from datetime import date
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from product_master import PRODUCT_MASTER_ROW_COUNT, normalize_product_name

EXPECTED_COLUMNS = ("품목명", "업소명", "품목유형명", "기능성")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(source: Path, output: Path) -> dict[str, str | int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".building")
    temporary.unlink(missing_ok=True)
    row_count = 0
    with source.open("r", encoding="utf-8-sig", newline="") as source_handle:
        reader = csv.DictReader(source_handle)
        if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
            raise ValueError(
                f"unexpected columns: {reader.fieldnames}; "
                f"expected {EXPECTED_COLUMNS}"
            )
        with sqlite3.connect(temporary) as db:
            db.executescript(
                """
                PRAGMA journal_mode = OFF;
                PRAGMA synchronous = OFF;
                CREATE TABLE products (
                    record_id INTEGER PRIMARY KEY,
                    product_name TEXT NOT NULL,
                    business_name TEXT NOT NULL,
                    product_type_name TEXT NOT NULL,
                    functionality TEXT NOT NULL,
                    normalized_product_name TEXT NOT NULL,
                    normalized_business_name TEXT NOT NULL
                );
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            batch: list[tuple[object, ...]] = []
            for row_count, row in enumerate(reader, start=1):
                product_name = (row["품목명"] or "").strip()
                business_name = (row["업소명"] or "").strip()
                if not product_name:
                    raise ValueError(f"missing product name at row {row_count + 1}")
                batch.append(
                    (
                        row_count,
                        product_name,
                        business_name,
                        (row["품목유형명"] or "").strip(),
                        (row["기능성"] or "").strip(),
                        normalize_product_name(product_name),
                        normalize_product_name(business_name),
                    )
                )
                if len(batch) == 2_000:
                    db.executemany(
                        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)",
                        batch,
                    )
                    batch.clear()
            if batch:
                db.executemany(
                    "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
            if row_count != PRODUCT_MASTER_ROW_COUNT:
                raise ValueError(
                    f"row count {row_count} != {PRODUCT_MASTER_ROW_COUNT}"
                )
            db.executescript(
                """
                CREATE INDEX idx_products_normalized_name
                    ON products(normalized_product_name);
                CREATE INDEX idx_products_normalized_name_business
                    ON products(
                        normalized_product_name,
                        normalized_business_name
                    );
                """
            )
            metadata = {
                "schema_version": "1",
                "corpus_version": "2026-07-28-v1",
                "row_count": str(row_count),
                "source_file_name": (
                    "mfds_health_functional_food_product_master_83687.csv"
                ),
                "source_sha256": sha256(source),
                "built_on": date.today().isoformat(),
            }
            db.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                metadata.items(),
            )
            db.commit()
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError("SQLite integrity_check failed")
        db.close()
    temporary.replace(output)
    return {
        "row_count": row_count,
        "source_sha256": sha256(source),
        "sqlite_sha256": sha256(output),
        "sqlite_bytes": output.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.source, args.output)
    for key, value in report.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
