"""Internal Streamlit UI for the validated two-stage MFDS pipeline."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st

PACKAGE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE_ROOT))

from scripts.run_pipeline import failure_record, run
from auth import verify_password
from result_partition import independent_review_output

STORE_ALIASES = ("FS01_PRODUCT_GATE", "FS11_FOOD_REVIEW", "FS21_HFF_REVIEW")


def configure_from_secrets() -> None:
    """Copy Streamlit secrets to process-local variables without displaying them."""

    try:
        secrets = st.secrets
        direct_names = (
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "OPENAI_REASONING_EFFORT",
        )
        for name in direct_names:
            value = secrets.get(name, "")
            if value and not os.getenv(name):
                os.environ[name] = str(value)
        for alias in STORE_ALIASES:
            destination = f"OPENAI_{alias}_STORE_ID"
            value = secrets.get(destination) or secrets.get(
                f"OPENAI_{alias}", ""
            )
            if value and not os.getenv(destination):
                os.environ[destination] = str(value)
    except FileNotFoundError:
        # Local development can use process environment variables.
        pass


def require_password() -> None:
    """Stop the app before any provider configuration or work until authenticated."""

    if st.session_state.get("authenticated"):
        return

    try:
        salt = str(st.secrets["APP_PASSWORD_SALT"])
        expected = str(st.secrets["APP_PASSWORD_HASH"])
        iterations = int(st.secrets.get("APP_PASSWORD_ITERATIONS", 600_000))
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        st.error("앱 인증 설정이 없습니다. 관리자에게 문의하세요.")
        st.stop()

    st.title("MFDS 검토 시스템")
    st.caption("승인된 내부 검토자만 이용할 수 있습니다.")
    with st.form("login", clear_on_submit=True):
        password = st.text_input("접근 비밀번호", type="password")
        submitted = st.form_submit_button("로그인", type="primary")

    if submitted:
        if verify_password(password, salt, expected, iterations):
            st.session_state.authenticated = True
            st.session_state.login_failures = 0
            st.rerun()
        else:
            failures = int(st.session_state.get("login_failures", 0)) + 1
            st.session_state.login_failures = failures
            time.sleep(min(failures, 3))
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


def violation_set(output: dict[str, Any]) -> set[str]:
    return {
        review["violation_type"]
        for product in output.get("product_results", [])
        for review in product.get("violation_reviews", [])
        if review.get("status") not in {"NOT_DETECTED", "INSUFFICIENT_EVIDENCE"}
    }


def compare_outputs(
    openai_output: dict[str, Any], gemini_output: dict[str, Any]
) -> dict[str, Any]:
    left = violation_set(openai_output)
    right = violation_set(gemini_output)
    intersection = len(left & right)
    precision = intersection / len(right) if right else 1.0
    recall = intersection / len(left) if left else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    return {
        "record_id": openai_output["record_id"],
        "product_type_match": (
            openai_output["stage1"]["record_product_type"]
            == gemini_output["stage1"]["record_product_type"]
        ),
        "violation_precision_gemini_vs_openai_reference": precision,
        "violation_recall_gemini_vs_openai_reference": recall,
        "violation_f1": f1,
        "risk_absolute_error": abs(
            openai_output["record_overall_risk_score"]
            - gemini_output["record_overall_risk_score"]
        ),
        "human_review_match": (
            openai_output["requires_human_review"]
            == gemini_output["requires_human_review"]
        ),
        "interpretation_guardrail": (
            "공급자 간 결과 차이는 자동 우열 판정이 아니라 담당자 검토 대상으로 봅니다."
        ),
    }


def review_rows(results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for provider, output in results.items():
        for product in output.get("product_results", []):
            rows.append(
                {
                    "공급자": provider,
                    "레코드 ID": output["record_id"],
                    "제품 순번": product["product_index"],
                    "제품명": product["product_name"],
                    "상태": product["product_overall_status"],
                    "위험도": product["product_overall_risk_score"],
                    "담당자 검토": product["requires_human_review"],
                    "불확실성 코드": ", ".join(product["uncertainty_codes"]),
                }
            )
    return sorted(
        rows,
        key=lambda item: (
            not item["담당자 검토"],
            -item["위험도"],
            item["공급자"],
        ),
    )


def render_review_table(rows: list[dict[str, Any]]) -> None:
    """Render a compact review table without requiring pandas."""

    headers = ("공급자", "제품명", "상태", "위험도", "담당자 검토", "불확실성 코드")

    def safe(value: Any) -> str:
        return str(value).replace("|", "&#124;").replace("\n", " ")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(safe(row[header]) for header in headers) + " |")
    st.markdown("\n".join(lines))


def run_provider(provider: str, source: dict[str, Any]) -> None:
    if not source["record_id"]:
        st.error("레코드 ID를 입력하세요.")
        return
    if not source["title"] and not source["body_text"]:
        st.error("게시물 제목이나 본문 중 하나 이상을 입력하세요.")
        return

    with st.spinner(f"{provider} 1·2단계를 실행하고 있습니다."):
        try:
            output = run(provider, source)
        except Exception as error:
            failure = failure_record(source, provider, "aggregate", error)
            st.session_state.failures[provider] = failure
            st.error(f"{provider} 실행 실패: {failure['error_code']}")
        else:
            st.session_state.results[provider] = output
            st.session_state.failures.pop(provider, None)
            st.success(f"{provider} 실행과 계약 검증이 완료됐습니다.")


def render_input() -> dict[str, Any]:
    st.subheader("광고 입력")
    record_id = st.text_input("레코드 ID", value="MFDS-REVIEW-001")
    title = st.text_input("게시물 제목")
    body_text = st.text_area("게시물 본문", height=240)
    left, right = st.columns(2)
    platform = left.text_input("플랫폼", placeholder="예: 네이버 블로그")
    source_url = right.text_input("원문 URL")
    return {
        "record_id": record_id.strip(),
        "title": title.strip(),
        "body_text": body_text.strip(),
        "platform": platform.strip() or None,
        "source_url": source_url.strip() or None,
    }


def render_independent_report(report: dict[str, Any]) -> None:
    """Render findings from the current advertisement review."""

    st.subheader("광고 원문 독립검토 결과")
    findings = report["independent_findings"]
    if not findings:
        st.info("현재 광고 원문에서 탐지된 위반 가능 항목이 없습니다.")
    else:
        rows = [
            {
                "제품명": item["product_name"] or "-",
                "식품 confidence": item["food_confidence"],
                "건기식 confidence": item["hff_confidence"],
                "위반 가능 항목": item["violation_label"],
                "상태": item["status"],
                "위험도": item["risk_score"],
                "Rule ID 수": len(item["rule_ids"]),
                "공식근거 ID 수": len(item["official_evidence_ids"]),
                "불확실성": ", ".join(item["uncertainty_codes"]) or "-",
            }
            for item in findings
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
        with st.expander("항목별 검색 근거와 판단 사유"):
            st.json(findings, expanded=False)
    st.caption(report["independent_findings_scope"])


def render_results() -> None:
    results = st.session_state.results
    result_tab, comparison_tab, review_tab = st.tabs(
        ["광고 원문 독립검토", "공급자 비교", "담당자 검토"]
    )
    with result_tab:
        if not results:
            st.info("실행된 결과가 없습니다.")
        else:
            provider = st.selectbox("결과 공급자", list(results))
            output = results[provider]
            report = independent_review_output(output)
            first, second, third = st.columns(3)
            first.metric("게시물 위험도", output["record_overall_risk_score"])
            second.metric("전체 상태", output["record_overall_status"])
            third.metric(
                "담당자 검토", "필요" if output["requires_human_review"] else "불필요"
            )
            render_independent_report(report)
            with st.expander("원본 1·2단계 모델 결과"):
                st.json(output, expanded=False)
            download_left, download_right = st.columns(2)
            download_left.download_button(
                "독립검토 보고서 JSON 다운로드",
                json.dumps(report, ensure_ascii=False, indent=2),
                file_name=(
                    f"{output['record_id']}-{provider}-independent-review.json"
                ),
                mime="application/json",
                use_container_width=True,
            )
            download_right.download_button(
                "원본 모델 결과 JSON 다운로드",
                json.dumps(output, ensure_ascii=False, indent=2),
                file_name=f"{output['record_id']}-{provider}.json",
                mime="application/json",
                use_container_width=True,
            )
    with comparison_tab:
        if {"openai", "gemini"} <= results.keys():
            st.json(compare_outputs(results["openai"], results["gemini"]))
        else:
            st.info(
                "Gemini 작업이 중단되어 현재 공급자 간 종단 비교는 실행하지 않습니다."
            )
    with review_tab:
        rows = review_rows(results)
        if rows:
            render_review_table(rows)
        else:
            st.info("담당자 검토 대상이 없습니다.")
        if st.session_state.failures:
            st.warning("공급자 실패 기록")
            st.json(st.session_state.failures)


def main() -> None:
    st.set_page_config(
        page_title="MFDS 2단계 File Search 검토",
        page_icon="🔎",
        layout="wide",
    )
    require_password()
    configure_from_secrets()
    st.title("MFDS 2단계 Cloud File Search 검토")
    st.caption(
        "1단계 제품·경로 판정 → 제품별 2단계 검색 → 담당자 확인"
    )
    st.warning(
        "법적 최종 판단 도구가 아닙니다. 원문·검색 근거·사실성을 담당자가 확인해야 합니다."
    )
    st.sidebar.subheader("운영 상태")
    st.sidebar.success("OpenAI File Search 활성")
    st.sidebar.info("Gemini 일시 중단")
    if st.sidebar.button("로그아웃", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.results = {}
        st.session_state.failures = {}
        st.rerun()

    st.session_state.setdefault("results", {})
    st.session_state.setdefault("failures", {})
    source = render_input()
    st.divider()
    offline_col, openai_col, gemini_col, clear_col = st.columns(4)
    if offline_col.button("오프라인 계약 실행", use_container_width=True):
        run_provider("offline", source)
    if openai_col.button("OpenAI 실행", type="primary", use_container_width=True):
        run_provider("openai", source)
    gemini_col.button(
        "Gemini 중단됨",
        disabled=True,
        help="Gemini 프로젝트 접근 문제가 해결될 때까지 비활성화합니다.",
        use_container_width=True,
    )
    if clear_col.button("결과 초기화", use_container_width=True):
        st.session_state.results = {}
        st.session_state.failures = {}
        st.rerun()
    st.divider()
    render_results()


if __name__ == "__main__":
    main()
