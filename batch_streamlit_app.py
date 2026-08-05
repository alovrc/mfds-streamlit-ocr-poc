"""Separate sequential background batch app for the MFDS review flow."""

from __future__ import annotations

import json
import os
from typing import Any

import streamlit as st

from batch_input import parse_batch_bytes, parse_batch_text
from batch_queue import BatchWorker
from markdown_report import build_markdown_report
from streamlit_app import (
    configure_from_secrets,
    require_password,
    render_openai_model_selector,
)


@st.cache_resource
def _worker() -> BatchWorker:
    return BatchWorker()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _batch_jsonl(worker: BatchWorker, jobs: list[dict[str, Any]]) -> bytes:
    rows: list[dict[str, Any]] = []
    for item in jobs:
        job = worker.get(item["job_id"])
        if not job:
            continue
        rows.append(
            {
                "job": job.public(),
                "source": job.resolved_source or job.source,
                "output": job.output,
                "failure": job.failure,
            }
        )
    return b"".join(_json_bytes(row) for row in rows)


def _render_completed_downloads(worker: BatchWorker, jobs: list[dict[str, Any]]) -> None:
    completed = [item for item in jobs if item["status"] == "SUCCEEDED"]
    if not completed:
        return
    st.subheader("완료 결과")
    selected_id = st.selectbox(
        "다운로드할 완료 건",
        [item["job_id"] for item in completed],
        format_func=lambda value: next(
            f"{item['record_id']} · {item['job_id']}"
            for item in completed
            if item["job_id"] == value
        ),
        key="batch_completed_job",
    )
    selected = worker.get(selected_id)
    if not selected or not selected.output:
        return
    source = selected.resolved_source or selected.source
    st.download_button(
        "선택 결과 JSON 다운로드",
        _json_bytes(
            {
                "job": selected.public(),
                "source": source,
                "output": selected.output,
            }
        ),
        file_name=f"{selected.job_id}.json",
        mime="application/json; charset=utf-8",
        use_container_width=True,
    )
    st.download_button(
        "선택 결과 Markdown 다운로드",
        build_markdown_report(selected.output, "openai", source).encode("utf-8"),
        file_name=f"{selected.job_id}.md",
        mime="text/markdown; charset=utf-8",
        use_container_width=True,
    )
    st.download_button(
        "전체 배치 결과 JSONL 다운로드",
        _batch_jsonl(worker, jobs),
        file_name="mfds_batch_results.jsonl",
        mime="application/x-ndjson; charset=utf-8",
        use_container_width=True,
    )


@st.fragment(run_every="2s")
def _render_queue_status(worker: BatchWorker) -> None:
    jobs = worker.snapshots()
    if not jobs:
        st.info("아직 제출된 배치 작업이 없습니다.")
        return
    counts = {status: sum(item["status"] == status for item in jobs) for status in (
        "QUEUED", "RUNNING", "SUCCEEDED", "FAILED"
    )}
    first, second, third, fourth = st.columns(4)
    first.metric("대기", counts["QUEUED"])
    second.metric("진행 중", counts["RUNNING"])
    third.metric("완료", counts["SUCCEEDED"])
    fourth.metric("실패", counts["FAILED"])
    st.dataframe(
        [
            {
                "순번": item["sequence"],
                "레코드 ID": item["record_id"],
                "제목": item["title"],
                "상태": item["status"],
                "모델": item["model"],
                "PaddleOCR": "포함" if item["paddle_ocr"] else "비포함",
                "오류": item["error_code"],
            }
            for item in jobs
        ],
        hide_index=True,
        use_container_width=True,
    )


def _preview_rows(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "순번": index,
            "레코드 ID": source["record_id"],
            "제목": source["title"],
            "본문": source["body_text"],
            "원문 URL": source["source_url"],
            "사이트명": source["platform"],
            "제품명": source["product_name"],
        }
        for index, source in enumerate(sources, start=1)
    ]


def main() -> None:
    st.set_page_config(
        page_title="MFDS 순차 배치 검토",
        page_icon="📦",
        layout="wide",
    )
    require_password()
    configure_from_secrets()
    openai_configured = bool(os.getenv("OPENAI_API_KEY", "").strip())
    selected_model = render_openai_model_selector()
    use_paddle_ocr = st.sidebar.toggle(
        "PaddleOCR 이미지 분석 포함",
        value=True,
        help="URL 입력에 포함된 이미지의 한국어 PaddleOCR 분석 여부를 선택합니다."
        " CSV 본문에 이미 들어온 텍스트에는 영향을 주지 않습니다.",
    )
    worker = _worker()

    st.title("MFDS 2단계 Cloud File Search 순차 배치")
    st.caption("배치 입력 → 한 건씩 URL 수집·PaddleOCR → OpenAI 분석 → 결과 다운로드")
    st.warning(
        "법적 최종 판단 도구가 아닙니다. Streamlit Cloud가 재시작되면 실행 중인 배치가 중단될 수 있습니다."
    )
    st.info(
        "현재 앱과 별도 배포된 배치 시험 앱입니다. 작업은 FIFO 순서로 한 건씩 처리됩니다."
    )
    st.sidebar.subheader("운영 상태")
    if openai_configured:
        st.sidebar.success("OpenAI File Search 활성")
    else:
        st.sidebar.error("OPENAI_API_KEY 미설정")
    st.sidebar.caption(
        "OCR: PaddleOCR 한국어 모델 "
        + ("포함" if use_paddle_ocr else "비포함")
    )

    uploaded = st.file_uploader(
        "배치 입력 파일",
        type=["csv", "jsonl", "ndjson", "txt", "xlsx"],
        help="헤더: record_id, title, body_text, source_url, platform, product_name",
    )
    pasted = st.text_area(
        "또는 CSV·JSONL 붙여넣기",
        height=160,
        placeholder='record_id,title,body_text,source_url\nBATCH-001,,,https://example.com/post',
    )
    parse_col, clear_col = st.columns(2)
    if parse_col.button("배치 입력 검증", type="secondary", use_container_width=True):
        try:
            if uploaded:
                sources = parse_batch_bytes(uploaded.name, uploaded.getvalue())
            elif pasted.strip():
                sources = parse_batch_text(pasted)
            else:
                raise ValueError("파일을 업로드하거나 CSV·JSONL을 붙여넣어 주세요.")
        except (UnicodeDecodeError, ValueError) as error:
            st.session_state.pop("batch_sources", None)
            st.error(str(error))
        else:
            st.session_state.batch_sources = sources
            st.success(f"{len(sources)}건의 배치 입력을 확인했습니다.")
    if clear_col.button("입력 초기화", use_container_width=True):
        st.session_state.pop("batch_sources", None)
        st.rerun()

    sources = st.session_state.get("batch_sources", [])
    if sources:
        st.subheader("배치 미리보기")
        st.dataframe(_preview_rows(sources), hide_index=True, use_container_width=True)
        if st.button(
            f"{len(sources)}건 순차 배치 실행",
            type="primary",
            disabled=not openai_configured,
            help="현재 선택한 OpenAI 모델로 FIFO 순서대로 실행합니다.",
            use_container_width=True,
        ):
            submitted = worker.submit_many(
                sources,
                selected_model,
                use_paddle_ocr,
            )
            st.session_state.batch_submitted_ids = submitted
            st.success(f"{len(submitted)}건을 순차 작업 큐에 등록했습니다.")

    st.divider()
    st.subheader("배치 진행 상태")
    _render_queue_status(worker)
    jobs = worker.snapshots()
    _render_completed_downloads(worker, jobs)


if __name__ == "__main__":
    main()
