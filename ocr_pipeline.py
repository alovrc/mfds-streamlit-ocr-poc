"""Local Tesseract OCR orchestration for the Streamlit OCR PoC."""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from web_capture import CaptureError, PageCapture, collect_page, fetch_image

OCR_STATUSES = {
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "NO_TEXT_DETECTED",
    "FAILED",
    "IMAGE_FETCH_FAILED",
}
MAX_IMAGES_PER_PAGE = 20
OCR_TIMEOUT_SECONDS = 20


_OCR_ALLOWED_CHARACTERS = re.compile(
    r"[^0-9A-Za-z\uac00-\ud7a3\u3131-\u314e\u314f-\u3163\s.,:;()\[\]/%+\-\u00d7x]"
)
_OCR_MEANINGFUL_WORD = re.compile(r"[\uac00-\ud7a3]{2,}|[A-Za-z]{2,}")
_OCR_MEANINGFUL_MEASUREMENT = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:mg|g|ml|%|\uac1c|\uc815|\ud3ec|x|\u00d7)\b",
    re.IGNORECASE,
)


def prepare_image(image_bytes: bytes) -> Image.Image:
    """Normalize and enlarge an image without inventing OCR confidence."""

    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image).convert("L")
    if image.width < 1600:
        scale = min(3.0, 1600 / max(image.width, 1))
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)),
            Image.Resampling.LANCZOS,
        )
    return ImageOps.autocontrast(image)


def tesseract_ocr(image_bytes: bytes) -> str:
    """Run Korean and English Tesseract OCR inside the Streamlit server."""

    import pytesseract

    image = prepare_image(image_bytes)
    return pytesseract.image_to_string(
        image,
        lang="kor+eng",
        config="--oem 1 --psm 6",
        timeout=OCR_TIMEOUT_SECONDS,
    ).strip()


def trim_ocr_text(text: str) -> str:
    """Remove OCR-only symbol noise without modifying the stored engine output.

    This is intentionally a conservative cleanup for the analysis payload. It
    does not repair character encoding or infer missing characters; the raw
    ``ocr_text`` remains available to the reviewer unchanged.
    """

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    lines: list[str] = []
    source_lines = normalized.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for source_line in source_lines:
        without_controls = "".join(
            " " if unicodedata.category(char).startswith("C") else char
            for char in source_line
        )
        cleaned = without_controls.replace("\ufffd", " ").replace("?", " ")
        cleaned = _OCR_ALLOWED_CHARACTERS.sub(" ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = cleaned.strip(" ,.:;+/\\-\u00d7")
        if not cleaned:
            continue
        if not (
            _OCR_MEANINGFUL_WORD.search(cleaned)
            or _OCR_MEANINGFUL_MEASUREMENT.search(cleaned)
        ):
            continue
        lines.append(cleaned)
    return "\n".join(lines)


def analysis_text(record: dict[str, Any]) -> str:
    """Select the reviewer text without overwriting the OCR engine output."""

    reviewed_text = record.get("reviewed_text")
    if reviewed_text:
        return str(reviewed_text).strip()
    return trim_ocr_text(str(record.get("ocr_text") or ""))


def merge_capture_text(
    title: str,
    body_text: str,
    ocr_records: list[dict[str, Any]],
) -> str:
    """Merge source text while preserving the source boundary of OCR text."""

    del title  # The title remains in the pipeline's dedicated title field.
    sections: list[str] = []
    if body_text.strip():
        sections.append(f"[BODY]\n{body_text.strip()}")
    for record in ocr_records:
        if not record.get("included_in_analysis"):
            continue
        text = analysis_text(record)
        if text:
            sections.append(f"[{record['source_id']}]\n{text}")
    return "\n\n".join(sections)


ImageFetcher = Callable[[str], tuple[str, bytes, str]]
OcrFunction = Callable[[bytes], str]
PageCollector = Callable[[str], PageCapture]


def collect_and_ocr(
    source_url: str,
    *,
    page_collector: PageCollector = collect_page,
    image_fetcher: ImageFetcher = fetch_image,
    ocr_function: OcrFunction = tesseract_ocr,
    max_images: int = MAX_IMAGES_PER_PAGE,
) -> dict[str, Any]:
    """Collect a public page and OCR a bounded, deduplicated image set."""

    page = page_collector(source_url)
    records: list[dict[str, Any]] = []
    asset_hashes: set[str] = set()
    duplicate_count = 0
    selected_urls = list(page.image_urls[:max_images])

    for index, image_url in enumerate(selected_urls, start=1):
        source_id = f"OCR_IMG_{index:03d}"
        try:
            final_url, image_bytes, _content_type = image_fetcher(image_url)
        except CaptureError as error:
            records.append(
                {
                    "source_id": source_id,
                    "image_url": image_url,
                    "ocr_text": "",
                    "reviewed_text": None,
                    "ocr_status": "IMAGE_FETCH_FAILED",
                    "error_code": error.code,
                    "included_in_analysis": False,
                }
            )
            continue

        digest = hashlib.sha256(image_bytes).hexdigest()
        if digest in asset_hashes:
            duplicate_count += 1
            continue
        asset_hashes.add(digest)

        try:
            text = ocr_function(image_bytes).strip()
        except (RuntimeError, OSError, UnidentifiedImageError) as error:
            records.append(
                {
                    "source_id": source_id,
                    "image_url": final_url,
                    "ocr_text": "",
                    "reviewed_text": None,
                    "ocr_status": "FAILED",
                    "error_code": type(error).__name__.upper(),
                    "included_in_analysis": False,
                    "_image_bytes": image_bytes,
                }
            )
            continue

        status = "SUCCESS" if text else "NO_TEXT_DETECTED"
        records.append(
            {
                "source_id": source_id,
                "image_url": final_url,
                "ocr_text": text,
                "reviewed_text": None,
                "ocr_status": status,
                "error_code": None,
                "included_in_analysis": bool(text),
                "_image_bytes": image_bytes,
            }
        )

    counts = Counter(record["ocr_status"] for record in records)
    merged_text = merge_capture_text(page.title, page.body_text, records)
    return {
        "requested_url": page.requested_url,
        "final_url": page.final_url,
        "title": page.title,
        "body_text": page.body_text,
        "image_discovered_count": len(page.image_urls),
        "image_selected_count": len(selected_urls),
        "duplicate_image_count": duplicate_count,
        "ocr_status_counts": dict(counts),
        "ocr_records": records,
        "merged_text": merged_text,
        "image_limit_reached": len(page.image_urls) > max_images,
    }
