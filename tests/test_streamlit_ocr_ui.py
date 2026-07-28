import base64

from streamlit.testing.v1 import AppTest

from auth import password_digest


def test_authenticated_trial_ui_exposes_ocr_capture_controls() -> None:
    salt = base64.b64encode(b"ocr-poc-test-salt").decode("ascii")
    app = AppTest.from_file("streamlit_app.py", default_timeout=10)
    app.secrets["APP_PASSWORD_SALT"] = salt
    app.secrets["APP_PASSWORD_HASH"] = password_digest(
        "test-password",
        salt,
        100_000,
    )
    app.secrets["APP_PASSWORD_ITERATIONS"] = 100_000

    app.run()
    app.text_input[0].set_value("test-password")
    app.button[0].click()
    app.run()

    assert not app.exception
    assert any("OCR 시험" in title.value for title in app.title)
    assert any(
        button.label == "URL 수집·한글 OCR 실행"
        for button in app.button
    )
    assert any(
        field.label == "게시물 본문＋OCR 병합문"
        for field in app.text_area
    )
