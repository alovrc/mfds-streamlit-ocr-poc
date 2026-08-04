from __future__ import annotations

import io
import zipfile

from batch_input import parse_batch_bytes, parse_batch_text


def test_parse_csv_supports_korean_source_headers() -> None:
    raw = "제목,내용,사이트주소,사이트명\n게시물 1,본문 1,https://example.com,인스타그램\n".encode()

    rows = parse_batch_bytes("input.csv", raw)

    assert rows == [
        {
            "record_id": "BATCH-001",
            "title": "게시물 1",
            "body_text": "본문 1",
            "source_url": "https://example.com",
            "platform": "인스타그램",
            "product_name": "",
        }
    ]


def test_parse_jsonl_allows_url_only_rows() -> None:
    rows = parse_batch_text('{"record_id":"A-1","source_url":"https://example.com"}\n')

    assert rows[0]["record_id"] == "A-1"
    assert rows[0]["source_url"] == "https://example.com"


def test_parse_xlsx_reads_active_sheet() -> None:
    try:
        from openpyxl import Workbook
    except ImportError:
        return
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["record_id", "title", "body_text", "source_url"])
    worksheet.append(["A-1", "제목", "본문", ""])
    stream = io.BytesIO()
    workbook.save(stream)

    rows = parse_batch_bytes("input.xlsx", stream.getvalue())

    assert rows[0]["record_id"] == "A-1"
    assert rows[0]["body_text"] == "본문"
