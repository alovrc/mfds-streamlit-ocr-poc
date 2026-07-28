import io

from PIL import Image

from ocr_pipeline import (
    OCR_STATUSES,
    analysis_text,
    collect_and_ocr,
    merge_capture_text,
    prepare_image,
)
from web_capture import CaptureError, PageCapture


def test_prepare_image_converts_and_enlarges_small_input() -> None:
    image = Image.new("RGB", (400, 200), color="white")
    stream = io.BytesIO()
    image.save(stream, format="PNG")

    prepared = prepare_image(stream.getvalue())

    assert prepared.mode == "L"
    assert prepared.width == 1200
    assert prepared.height == 600


def test_analysis_text_prefers_reviewer_text() -> None:
    assert (
        analysis_text(
            {
                "ocr_text": "엔진 원문",
                "reviewed_text": "담당자 수정",
            }
        )
        == "담당자 수정"
    )
    assert analysis_text({"ocr_text": "엔진 원문", "reviewed_text": None}) == (
        "엔진 원문"
    )


def test_merge_capture_text_keeps_source_boundaries() -> None:
    records = [
        {
            "source_id": "OCR_IMG_001",
            "ocr_text": "엔진 원문",
            "reviewed_text": "담당자 수정",
            "included_in_analysis": True,
        },
        {
            "source_id": "OCR_IMG_002",
            "ocr_text": "제외 문구",
            "reviewed_text": None,
            "included_in_analysis": False,
        },
    ]

    merged = merge_capture_text("별도 제목", "본문 원문", records)

    assert merged == "[BODY]\n본문 원문\n\n[OCR_IMG_001]\n담당자 수정"
    assert "별도 제목" not in merged
    assert "제외 문구" not in merged


def test_collect_and_ocr_deduplicates_and_records_failures() -> None:
    page = PageCapture(
        requested_url="https://example.com/post",
        final_url="https://example.com/post",
        title="시험 제목",
        body_text="본문",
        image_urls=(
            "https://example.com/a.png",
            "https://example.com/a-copy.png",
            "https://example.com/empty.png",
            "https://example.com/fail.png",
        ),
    )
    payloads = {
        "https://example.com/a.png": b"same-image",
        "https://example.com/a-copy.png": b"same-image",
        "https://example.com/empty.png": b"empty-image",
    }

    def image_fetcher(url: str):
        if url.endswith("fail.png"):
            raise CaptureError("FETCH_FAILED", "failed")
        return url, payloads[url], "image/png"

    def ocr_function(data: bytes) -> str:
        return "" if data == b"empty-image" else "질병 치료"

    result = collect_and_ocr(
        page.requested_url,
        page_collector=lambda _url: page,
        image_fetcher=image_fetcher,
        ocr_function=ocr_function,
    )

    assert result["image_discovered_count"] == 4
    assert result["duplicate_image_count"] == 1
    assert len(result["ocr_records"]) == 3
    assert result["ocr_status_counts"] == {
        "SUCCESS": 1,
        "NO_TEXT_DETECTED": 1,
        "IMAGE_FETCH_FAILED": 1,
    }
    assert all(
        record["ocr_status"] in OCR_STATUSES
        for record in result["ocr_records"]
    )
    assert "[OCR_IMG_001]\n질병 치료" in result["merged_text"]
