"""Versioned exact lookup for the approved public HFF product master."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
import unicodedata
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PRODUCT_MASTER_VERSION = "2026-07-28-v1"
PRODUCT_MASTER_ROW_COUNT = 83_687
PRODUCT_MASTER_FILE_NAME = (
    "mfds_health_functional_food_product_master_83687.sqlite3"
)
PRODUCT_MASTER_RELEASE_URL = (
    "https://github.com/alovrc/mfds-streamlit/releases/download/"
    f"product-master-{PRODUCT_MASTER_VERSION}/{PRODUCT_MASTER_FILE_NAME}"
)
# Filled from the validated release asset by scripts/build_product_master.py.
PRODUCT_MASTER_SHA256 = "7cd71cd2b583b70dbd8cca204db02c81880ebc893960a7aa1220d9a7866729b4"
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class ProductMasterMatch:
    record_id: int
    product_name: str
    business_name: str
    product_type_name: str
    functionality: str


@dataclass(frozen=True)
class ProductMasterLookup:
    query: str
    status: str
    matches: tuple[ProductMasterMatch, ...] = ()
    error_code: str | None = None

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "status": self.status,
            "matches": [asdict(match) for match in self.matches],
            "error_code": self.error_code,
            "interpretation": (
                "EXACT_UNIQUE이면 공개 승인 건강기능식품 제품 마스터의 "
                "정규화 품목명 정확일치다. AMBIGUOUS는 제품유형 확정에 "
                "사용하지 않는다."
            ),
        }


def normalize_product_name(value: str) -> str:
    """Normalize only orthographic separators; this is not fuzzy matching."""

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s·ㆍ_/\-]+", "", normalized)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_database(path: Path, expected_sha256: str) -> None:
    if expected_sha256 and _sha256(path) != expected_sha256.lower():
        raise RuntimeError("PRODUCT_MASTER_SHA256_MISMATCH")
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
            row_count = db.execute(
                "SELECT value FROM metadata WHERE key = 'row_count'"
            ).fetchone()
            if not row_count or int(row_count[0]) != PRODUCT_MASTER_ROW_COUNT:
                raise RuntimeError("PRODUCT_MASTER_ROW_COUNT_INVALID")
            integrity = db.execute("PRAGMA quick_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError("PRODUCT_MASTER_SQLITE_INVALID")
    except sqlite3.Error as error:
        raise RuntimeError("PRODUCT_MASTER_SQLITE_INVALID") from error


def _download_database(destination: Path, url: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".download")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "mfds-streamlit-product-master/1.0"},
    )
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            with temporary.open("wb") as output:
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("PRODUCT_MASTER_DOWNLOAD_TOO_LARGE")
                    output.write(block)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def resolve_product_master() -> Path:
    """Resolve a local override or download the immutable public release asset."""

    local_override = os.getenv("MFDS_PRODUCT_MASTER_PATH", "").strip()
    expected_sha256 = (
        os.getenv("MFDS_PRODUCT_MASTER_SHA256", "").strip()
        or PRODUCT_MASTER_SHA256
    ).lower()
    if local_override:
        path = Path(local_override).expanduser().resolve()
        _validate_database(path, expected_sha256)
        return path

    if not expected_sha256:
        raise RuntimeError("PRODUCT_MASTER_SHA256_NOT_CONFIGURED")
    cache_root = Path(tempfile.gettempdir()) / "mfds-product-master"
    path = cache_root / f"{expected_sha256[:16]}-{PRODUCT_MASTER_FILE_NAME}"
    if not path.is_file():
        url = (
            os.getenv("MFDS_PRODUCT_MASTER_URL", "").strip()
            or PRODUCT_MASTER_RELEASE_URL
        )
        _download_database(path, url)
    try:
        _validate_database(path, expected_sha256)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def lookup_product(product_name: str | None) -> ProductMasterLookup:
    """Return an exact normalized-name lookup without silently using fuzzy logic."""

    query = (product_name or "").strip()
    if not query:
        return ProductMasterLookup(query="", status="NOT_REQUESTED")
    normalized = normalize_product_name(query)
    if not normalized:
        return ProductMasterLookup(query=query, status="NO_MATCH")
    try:
        path = resolve_product_master()
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
            rows = db.execute(
                """
                SELECT record_id, product_name, business_name,
                       product_type_name, functionality
                FROM products
                WHERE normalized_product_name = ?
                ORDER BY record_id
                LIMIT 11
                """,
                (normalized,),
            ).fetchall()
    except Exception as error:
        return ProductMasterLookup(
            query=query,
            status="UNAVAILABLE",
            error_code=str(error)[:160],
        )

    matches = tuple(
        ProductMasterMatch(
            record_id=int(row[0]),
            product_name=str(row[1]),
            business_name=str(row[2]),
            product_type_name=str(row[3]),
            functionality=str(row[4]),
        )
        for row in rows
    )
    if not matches:
        return ProductMasterLookup(query=query, status="NO_MATCH")
    if len(matches) == 1:
        return ProductMasterLookup(
            query=query,
            status="EXACT_UNIQUE",
            matches=matches,
        )
    return ProductMasterLookup(
        query=query,
        status="AMBIGUOUS",
        matches=matches,
    )
