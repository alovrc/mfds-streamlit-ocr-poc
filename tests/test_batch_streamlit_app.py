import pytest

pytest.importorskip("streamlit")

from batch_streamlit_app import _preview_rows
from streamlit_app import OPENAI_MODEL_OPTIONS


def test_openai_model_options_put_luna_first() -> None:
    assert OPENAI_MODEL_OPTIONS == (
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
    )


def test_batch_preview_displays_all_input_columns() -> None:
    rows = _preview_rows(
        [
            {
                "record_id": "A-1",
                "title": "제목",
                "body_text": "본문",
                "source_url": "https://example.com/post",
                "platform": "인스타그램",
                "product_name": "블루베리",
            }
        ]
    )

    assert rows == [
        {
            "순번": 1,
            "레코드 ID": "A-1",
            "제목": "제목",
            "본문": "본문",
            "원문 URL": "https://example.com/post",
            "사이트명": "인스타그램",
            "제품명": "블루베리",
        }
    ]
