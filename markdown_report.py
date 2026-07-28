"""Build a human-readable Markdown report from validated File Search output."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from result_partition import ACTIVE_STATUSES, VIOLATION_LABELS

UNCERTAINTY_LABELS = {
    "SEARCH_NO_OFFICIAL_EVIDENCE": "보조 공식근거 미검색",
}


def _uncertainty_text(codes: list[str]) -> str:
    return ", ".join(
        UNCERTAINTY_LABELS.get(str(code), str(code))
        for code in codes
    )


def _text(value: Any, default: str = "-") -> str:
    if value in (None, "", [], {}):
        return default
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) or default
    return str(value)


def _cell(value: Any) -> str:
    return _text(value).replace("|", "&#124;").replace("\r", " ").replace("\n", " ")


def _table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    if not rows:
        rows = [tuple("-" for _ in headers)]
    lines.extend(
        "| " + " | ".join(_cell(value) for value in row) + " |"
        for row in rows
    )
    return lines


def _expression_map(product: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("expression_id")): item
        for item in product.get("problem_expressions", [])
        if item.get("expression_id")
    }


def _citation_map(product: dict[str, Any]) -> dict[str, dict[str, Any]]:
    citations: dict[str, dict[str, Any]] = {}
    for item in product.get("file_search", {}).get("citations", []):
        record_id = str(item.get("record_id") or "")
        if record_id and (
            record_id not in citations
            or (
                not citations[record_id].get("excerpt")
                and item.get("excerpt")
            )
        ):
            citations[record_id] = item
    return citations


def _append_evidence_details(
    lines: list[str],
    label: str,
    record_ids: list[str],
    citations: dict[str, dict[str, Any]],
) -> None:
    lines.extend(["", f"#### {label}", ""])
    if not record_ids:
        lines.append("- 연결된 근거 없음")
        return
    for record_id in record_ids:
        citation = citations.get(str(record_id), {})
        file_name = _text(citation.get("file_name"), "파일명 확인 불가")
        page = citation.get("page")
        page_label = f", {page}쪽" if page else ""
        lines.append(f"- `{record_id}` — {file_name}{page_label}")
        excerpt = str(citation.get("excerpt") or "").strip()
        if excerpt:
            lines.append(f"  > {' '.join(excerpt.split())}")
        elif citation:
            lines.append("  > 검색 citation은 확인됐으나 발췌문은 제공되지 않음")
        else:
            lines.append("  > 실제 File Search citation과 일치 여부 확인 필요")


def _search_runs(output: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    runs: list[tuple[str, dict[str, Any]]] = []
    stage1_search = output.get("stage1", {}).get("file_search")
    if isinstance(stage1_search, dict):
        runs.append(("1단계 제품·경로 판정", stage1_search))
    for product in output.get("product_results", []):
        search = product.get("file_search")
        if isinstance(search, dict):
            product_name = _text(product.get("product_name"), "제품명 미확인")
            runs.append((f"2단계 위반 검토: {product_name}", search))
    return runs


def _all_uncertainty_codes(output: dict[str, Any]) -> list[str]:
    codes: list[str] = list(output.get("error_codes", []))
    codes.extend(output.get("stage1", {}).get("uncertainty_codes", []))
    for product in output.get("product_results", []):
        codes.extend(product.get("uncertainty_codes", []))
        for review in product.get("violation_reviews", []):
            codes.extend(review.get("uncertainty_codes", []))
    return list(dict.fromkeys(str(code) for code in codes if code))


def build_markdown_report(
    output: dict[str, Any],
    provider: str,
    source: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> str:
    """Return a standalone UTF-8 Markdown result report."""

    source = source or {}
    stage1 = output.get("stage1", {})
    stage1_products = {
        item.get("product_index"): item for item in stage1.get("products", [])
    }
    routes = {
        item.get("product_index"): item for item in stage1.get("routes", [])
    }
    generated_at = generated_at or datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    lines = [
        "# MFDS Cloud File Search 결과보고서",
        "",
        "> 법적 최종 판단 도구가 아닙니다. 최종 판단 전 담당자가 원문과 "
        "검색 근거 및 사실성을 확인해야 합니다.",
        "",
        "## 1. 검토 개요",
        "",
    ]
    lines.extend(
        _table(
            ("항목", "내용"),
            [
                ("보고서 생성시각", generated_at),
                ("공급자", provider),
                ("레코드 ID", output.get("record_id")),
                ("플랫폼", source.get("platform")),
                ("게시물 제목", source.get("title")),
                ("입력 제품명", source.get("product_name")),
                ("원문 URL", source.get("source_url")),
            ],
        )
    )
    lines.extend(
        [
            "",
            "## 2. 전체 결과 요약",
            "",
        ]
    )
    lines.extend(
        _table(
            ("항목", "결과"),
            [
                ("전체 검토상태", output.get("record_overall_status")),
                (
                    "유효 최고위험 점수",
                    f"{output.get('record_overall_risk_score', '-')}/10",
                ),
                (
                    "담당자 검토",
                    "필요" if output.get("requires_human_review") else "불필요",
                ),
                ("레코드 제품유형", stage1.get("record_product_type")),
                ("제품 존재", stage1.get("product_presence")),
                ("판매광고 문맥", stage1.get("sales_ad_context")),
                ("1단계 판단 사유", stage1.get("short_reason")),
            ],
        )
    )
    supported_reviews = [
        review
        for product in output.get("product_results", [])
        for review in product.get("violation_reviews", [])
        if review.get("status") in {"HIGH", "REVIEW", "LOW"}
    ]
    unresolved = [
        review
        for product in output.get("product_results", [])
        for review in product.get("violation_reviews", [])
        if review.get("status") == "INSUFFICIENT_EVIDENCE"
    ]
    if (
        output.get("record_overall_status") == "SUFFICIENT_EVIDENCE"
        and supported_reviews
    ):
        lines.extend(
            [
                "",
                (
                    "> 원문 문제표현과 Rule ID가 연결된 유효 위반 후보가 있어 전체 "
                    "검토상태를 `SUFFICIENT_EVIDENCE`로 평가했습니다. "
                    "공식근거 ID와 사례 ID는 보조 검색근거이며, 검색되지 "
                    "않아도 Rule ID와 Rule 설명으로 판단근거를 제시합니다."
                ),
            ]
        )
    if unresolved:
        lines.extend(
            [
                "",
                (
                    f"> 증거요건 미충족 후보 {len(unresolved)}개는 "
                    "`INSUFFICIENT_EVIDENCE`로 유지되며 담당자 확인이 "
                    "필요합니다."
                ),
            ]
        )

    deterministic = output.get("deterministic_aggregation", {})
    if deterministic:
        lines.extend(
            [
                "",
                "### 2.1 결정론적 위험도·대표유형 집계",
                "",
                (
                    f"- 규칙 버전: `{_text(deterministic.get('rules_version'))}`"
                ),
                (
                    "- 운영기준 SHA-256: "
                    f"`{_text(deterministic.get('rules_source_sha256'))}`"
                ),
                (
                    "- 집계 범위: 「식품 등의 표시·광고에 관한 법률」 "
                    "제8조제1항 제1호~제5호"
                ),
                (
                    "- 점수 원칙: 조항별 유효 발생근거의 최댓값을 사용하며 "
                    "합산·평균하지 않음"
                ),
                (
                    "- PoC 발생 단위: 제품·조항·판단유형별 고유 "
                    "`expression_id`"
                ),
                "",
            ]
        )
        lines.extend(
            _table(
                (
                    "조항",
                    "대표유형",
                    "고유 문제표현 수",
                    "위험도",
                    "최고위험 근거수",
                    "연결 판단유형",
                ),
                [
                    (
                        f"제{item.get('article_item')}호",
                        item.get("article_name"),
                        item.get("occurrence_count"),
                        item.get("risk_score"),
                        item.get("highest_risk_occurrence_count"),
                        item.get("violation_types"),
                    )
                    for item in deterministic.get(
                        "article_summaries", []
                    )
                ],
            )
        )
        representative_rows = []
        for item in deterministic.get("representative_types", []):
            selected_by = {
                "most_frequent": "최다빈도",
                "highest_risk": "최고위험",
            }
            representative_rows.append(
                (
                    f"제{item.get('article_item')}호",
                    item.get("article_name"),
                    item.get("occurrence_count"),
                    item.get("risk_score"),
                    [
                        selected_by.get(value, value)
                        for value in item.get("selected_by", [])
                    ],
                )
            )
        lines.extend(["", "#### 대표유형", ""])
        lines.extend(
            _table(
                (
                    "조항",
                    "대표유형",
                    "고유 문제표현 수",
                    "위험도",
                    "선정기준",
                ),
                representative_rows,
            )
        )
        lines.extend(
            [
                "",
                (
                    "동일 `expression_id`는 같은 제품·조항·판단유형에서 "
                    "한 번만 집계합니다. 현재 PoC Schema에는 문자 위치 "
                    "오프셋이 없어 겹침 위치 판정 대신 `expression_id`를 "
                    "사용합니다."
                ),
            ]
        )

    lines.extend(["", "## 3. 제품 분류·신뢰도·라우팅", ""])
    product_rows: list[tuple[Any, ...]] = []
    for product in output.get("product_results", []):
        product_index = product.get("product_index")
        classified = stage1_products.get(product_index, {})
        route = routes.get(product_index, {})
        product_rows.append(
            (
                product_index,
                product.get("product_name"),
                classified.get("product_type") or product.get("product_type"),
                classified.get("food_confidence"),
                classified.get("hff_confidence"),
                classified.get("confidence"),
                route.get("stage2_route"),
                route.get("store_alias"),
                ", ".join(classified.get("evidence_ids", [])),
                _uncertainty_text(
                    classified.get("uncertainty_codes", [])
                ),
            )
        )
    lines.extend(
        _table(
            (
                "순번",
                "제품명",
                "제품유형",
                "식품 confidence",
                "건기식 confidence",
                "전체 confidence",
                "2단계 경로",
                "검색 저장소",
                "제품분류 근거 ID",
                "불확실성",
            ),
            product_rows,
        )
    )
    lines.extend(
        [
            "",
            "confidence는 자동 분류의 신뢰점수이며 제품유형을 법적으로 "
            "확정하는 값이 아닙니다.",
            "",
            "## 4. 위반 가능 항목",
            "",
        ]
    )
    finding_rows: list[tuple[Any, ...]] = []
    active_reviews: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for product in output.get("product_results", []):
        for review in product.get("violation_reviews", []):
            label = VIOLATION_LABELS.get(
                str(review.get("violation_type")),
                str(review.get("violation_type")),
            )
            finding_rows.append(
                (
                    product.get("product_name"),
                    label,
                    review.get("status"),
                    review.get("risk_score"),
                    len(review.get("rule_ids", [])),
                    len(review.get("official_evidence_ids", [])),
                    len(review.get("case_ids", [])),
                )
            )
            if review.get("status") in ACTIVE_STATUSES:
                active_reviews.append((product, review))
    lines.extend(
        _table(
            (
                "제품명",
                "위반 가능 항목",
                "상태",
                "위험도",
                "Rule ID",
                "공식근거 ID",
                "사례 ID",
            ),
            finding_rows,
        )
    )

    if not active_reviews:
        lines.extend(["", "현재 광고 원문에서 활성 위반 후보가 탐지되지 않았습니다."])
    for index, (product, review) in enumerate(active_reviews, start=1):
        label = VIOLATION_LABELS.get(
            str(review.get("violation_type")),
            str(review.get("violation_type")),
        )
        expressions = _expression_map(product)
        citations = _citation_map(product)
        lines.extend(
            [
                "",
                f"### 4.{index}. {_text(product.get('product_name'))} — {label}",
                "",
                f"- 상태: `{_text(review.get('status'))}`",
                f"- 위험도: `{_text(review.get('risk_score'))}/10`",
                f"- 판단 사유: {_text(review.get('score_reason'))}",
                f"- Rule ID: {_text(review.get('rule_ids'))}",
                f"- 공식근거 ID: {_text(review.get('official_evidence_ids'))}",
                f"- 사례 ID: {_text(review.get('case_ids'))}",
                "- 불확실성 코드: "
                + (
                    _uncertainty_text(
                        review.get("uncertainty_codes", [])
                    )
                    or "-"
                ),
                "- 문제 표현:",
            ]
        )
        if not review.get("official_evidence_ids"):
            lines.append(
                "- 공식근거 ID 미검색 시 근거 설명: 적용 Rule ID와 로컬 "
                "Rule 기준 설명을 판단근거로 사용함"
            )
        expression_ids = review.get("expression_ids", [])
        if not expression_ids:
            lines.append("  - 확인된 직접 인용 없음")
        for expression_id in expression_ids:
            expression = expressions.get(str(expression_id), {})
            quote = _text(expression.get("quote"), "인용문 확인 필요")
            source_field = _text(expression.get("source_field"), "위치 미확인")
            lines.append(
                f"  - `{expression_id}` ({source_field}): “{quote}”"
            )
        _append_evidence_details(
            lines,
            "적용 Rule(로컬 결정론적 연결)",
            list(review.get("rule_ids", [])),
            citations,
        )
        _append_evidence_details(
            lines,
            "공식 검색근거·인용문",
            list(review.get("official_evidence_ids", [])),
            citations,
        )
        _append_evidence_details(
            lines,
            "참고 사례",
            list(review.get("case_ids", [])),
            citations,
        )

    lines.extend(["", "## 5. File Search 및 로컬 Rule 근거", ""])
    runs = _search_runs(output)
    lines.extend(
        _table(
            (
                "단계",
                "저장소",
                "검색 실행",
                "검색 ID 수",
                "citation 수",
                "지연시간(ms)",
            ),
            [
                (
                    name,
                    search.get("store_alias"),
                    search.get("file_search_run"),
                    len(search.get("retrieved_ids", [])),
                    len(search.get("citations", [])),
                    search.get("latency_ms"),
                )
                for name, search in runs
            ],
        )
    )
    citation_rows: list[tuple[Any, ...]] = []
    seen_citations: set[tuple[str, str, str]] = set()
    for name, search in runs:
        for citation in search.get("citations", []):
            key = (
                str(search.get("store_alias") or ""),
                str(citation.get("record_id") or ""),
                str(citation.get("source") or ""),
            )
            if key in seen_citations:
                continue
            seen_citations.add(key)
            citation_rows.append(
                (
                    name,
                    search.get("store_alias"),
                    citation.get("record_id"),
                    citation.get("file_name"),
                    citation.get("source"),
                    citation.get("page"),
                )
            )
    lines.extend(["", "### 근거 citation 목록", ""])
    lines.extend(
        _table(
            ("단계", "저장소", "근거 ID", "파일명", "출처", "페이지"),
            citation_rows,
        )
    )
    lines.extend(
        [
            "",
            "`rule_catalog.json`의 Rule ID는 후보 위반유형에 따라 앱이 "
            "결정론적으로 연결한 기준이며 별도 File Search 결과가 아닙니다. "
            "그 밖의 citation은 모델이 실제로 검색한 자료의 추적정보입니다. "
            "Rule 연결이나 검색 사실만으로 법적 판단이 자동 확정되지는 않으며, "
            "담당자가 원문과 공식근거의 직접 적용 여부를 확인해야 합니다.",
            "",
            "## 6. 담당자 확인사항",
            "",
        ]
    )
    uncertainty_codes = _all_uncertainty_codes(output)
    review_items = [
        "광고 원문의 문제 표현이 현재 게시물에 실제 존재하는지 확인",
        "제품유형·제품 DB 일치 여부와 표시사항 확인",
        "위반 후보별 Rule 및 공식근거가 해당 표현에 직접 적용되는지 확인",
        "File Search citation과 로컬 Rule의 원문, 버전 및 시행일 확인",
    ]
    if uncertainty_codes:
        review_items.append(
            "오류·불확실성 코드 확인: "
            + _uncertainty_text(uncertainty_codes)
        )
    lines.extend(f"- {item}" for item in review_items)
    lines.extend(
        [
            "",
            "## 7. 판정 범위",
            "",
            "이 보고서는 현재 광고 원문을 제품정보·Rule·공식근거와 대조해 "
            "탐지한 위반 가능 항목을 정리한 것입니다. 최종 판단 전 담당자가 "
            "원문과 검색 근거를 확인해야 합니다.",
            "",
        ]
    )
    return "\n".join(lines)
