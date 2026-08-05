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
from result_partition import independent_review_output


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


def _failure_payload(job: Any) -> dict[str, Any]:
    failure = job.failure or {}
    return {
        "job": job.public(),
        "source": job.resolved_source or job.source,
        "failure": failure,
    }


def _failure_markdown(job: Any) -> bytes:
    failure = job.failure or {}
    source = job.resolved_source or job.source
    lines = [
        "# MFDS Batch Failure Result",
        "",
        f"- record_id: {job.source.get('record_id', '')}",
        f"- job_id: {job.job_id}",
        f"- model: {job.model}",
        f"- PaddleOCR: {'included' if job.use_paddle_ocr else 'excluded'}",
        "",
        "## Error",
        "",
        f"- error_code: {failure.get('error_code', 'UNKNOWN')}",
        f"- stage: {failure.get('stage', '')}",
        "",
        "```text",
        str(failure.get('message', 'No error details.')),
        "```",
        "",
        "## Input",
        "",
        "```json",
        json.dumps(source, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _render_completed_downloads(worker: BatchWorker, jobs: list[dict[str, Any]]) -> None:
    terminal = [item for item in jobs if item["status"] in {"SUCCEEDED", "FAILED"}]
    if not terminal:
        return
    st.subheader("작업 결과")
    selected_id = st.selectbox(
        "결과를 확인하거나 다운로드할 작업",
        [item["job_id"] for item in terminal],
        format_func=lambda value: next(
            f"{item['record_id']} · {item['status']} · {item['job_id']}"
            for item in terminal
            if item["job_id"] == value
        ),
        key="batch_terminal_job",
    )
    selected = worker.get(selected_id)
    if not selected:
        return
    if selected.status == "FAILED":
        failure = selected.failure or {}
        st.error(
            f"{failure.get('error_code', 'UNKNOWN')}: "
            f"{failure.get('message', 'No error details.')}",
        )
        st.download_button(
            "실패 결과 JSON 다운로드",
            _json_bytes(_failure_payload(selected)),
            file_name=f"{selected.job_id}.failure.json",
            mime="application/json; charset=utf-8",
            width="stretch",
        )
        st.download_button(
            "실패 결과 Markdown 다운로드",
            _failure_markdown(selected),
            file_name=f"{selected.job_id}.failure.md",
            mime="text/markdown; charset=utf-8",
            width="stretch",
        )
        st.download_button(
            "전체 배치 결과 JSONL 다운로드",
            _batch_jsonl(worker, jobs),
            file_name="mfds_batch_results.jsonl",
            mime="application/x-ndjson; charset=utf-8",
            width="stretch",
        )
        return
    if not selected.output:
        return
    source = selected.resolved_source or selected.source
    independent_review = independent_review_output(selected.output)
    st.json(selected.output, expanded=True)
    st.subheader("법령 조항·근거 상태")
    st.json(independent_review, expanded=True)
    st.download_button(
        "선택 결과 JSON 다운로드",
        _json_bytes(
            {
                "job": selected.public(),
                "source": source,
                "output": selected.output,
                "independent_review": independent_review,
            }
        ),
        file_name=f"{selected.job_id}.json",
        mime="application/json; charset=utf-8",
        width="stretch",
    )
    st.download_button(
        "선택 결과 Markdown 다운로드",
        build_markdown_report(selected.output, "openai", source).encode("utf-8"),
        file_name=f"{selected.job_id}.md",
        mime="text/markdown; charset=utf-8",
        width="stretch",
    )
    st.download_button(
        "전체 배치 결과 JSONL 다운로드",
        _batch_jsonl(worker, jobs),
        file_name="mfds_batch_results.jsonl",
        mime="application/x-ndjson; charset=utf-8",
        width="stretch",
    )


@st.fragment(run_every="2s")
def _render_sidebar_queue_status(worker: BatchWorker) -> None:
    jobs = worker.snapshots()
    counts = {
        status: sum(item["status"] == status for item in jobs)
        for status in ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED")
    }
    st.sidebar.divider()
    st.sidebar.subheader("배치 진행 상태")
    if counts["RUNNING"]:
        st.sidebar.caption("상태: 처리 중")
    elif counts["QUEUED"]:
        st.sidebar.caption("상태: 대기 중")
    elif counts["FAILED"]:
        st.sidebar.caption("상태: 실패 확인 필요")
    elif jobs:
        st.sidebar.caption("상태: 완료")
    else:
        st.sidebar.caption("상태: 작업 없음")
    st.sidebar.write(f"대기　**{counts['QUEUED']}**")
    st.sidebar.write(f"진행 중　**{counts['RUNNING']}**")
    st.sidebar.write(f"완료　**{counts['SUCCEEDED']}**")
    st.sidebar.write(f"실패　**{counts['FAILED']}**")
    active = next(
        (item for item in jobs if item["status"] == "RUNNING"),
        None,
    )
    if active:
        st.sidebar.caption(f"현재 처리: {active['record_id']}")


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
    _render_completed_downloads(worker, jobs)
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
        width="stretch",
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
    _render_sidebar_queue_status(worker)

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
    if parse_col.button("배치 입력 검증", type="secondary", width="stretch"):
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
    if clear_col.button("입력 초기화", width="stretch"):
        st.session_state.pop("batch_sources", None)
        st.rerun()

    sources = st.session_state.get("batch_sources", [])
    if sources:
        st.subheader("배치 미리보기")
        st.dataframe(_preview_rows(sources), hide_index=True, width="stretch")
        if st.button(
            f"{len(sources)}건 순차 배치 실행",
            type="primary",
            disabled=not openai_configured,
            help="현재 선택한 OpenAI 모델로 FIFO 순서대로 실행합니다.",
            width="stretch",
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


if __name__ == "__main__":
    main()
