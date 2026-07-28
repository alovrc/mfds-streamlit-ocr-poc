# MFDS Streamlit deployment shell

OpenAI File Search 기반 MFDS 2단계 검토 앱의 공개 배포용 최소 저장소다.
앱 기능은 비밀번호 인증 이후에만 접근할 수 있다.

## 공개 저장소 제외 대상

- API 키와 실제 Vector Store ID
- 실제 DB와 File Search 업로드 파일
- 실제 광고 원문과 내부 자료
- `.streamlit/secrets.toml`

운영 비밀값은 Streamlit Community Cloud Secrets에서만 관리한다.

## 검증 상태

- OpenAI File Search 실검증 완료
- Gemini 실행 일시 중단
- 법적 최종 판단 도구가 아닌 담당자 검토 보조 도구

## 로컬 실행

```powershell
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

## 광고 원문 독립검토 보고서

앱은 현재 광고 원문을 File Search의 제품정보·Rule·공식근거와 대조해 위반 가능
항목을 검토한다. 검증된 원본 파이프라인 JSON도 독립검토 보고서와 별도로
확인하고 내려받을 수 있다.

제품유형 점수는 `food_confidence`와 `hff_confidence`로 분리한다. 제품 마스터
정확일치가 없는 경우 한 점수가 0.50 이상이고 다른 점수보다 0.05를 초과하여
높을 때 각각 식품 또는 건강기능식품 후보 경로로 대칭 라우팅한다. 두 점수가
모두 0.50 미만이거나 차이가 0.05 이하이거나 상충 근거가 있으면 품목 불명확으로
처리하여 자동 2단계 라우팅을 중단하고 담당자 검토를 요구한다.

`FS01_PRODUCT_GATE`의 기본 검색 저장소는 최신 통합 공전 저장소
`MFDS_FS01_PRODUCT_TYPE_20260728_V01`이다. Streamlit Secrets의
`OPENAI_FS01_PRODUCT_GATE_STORE_ID`가 설정되어 있으면 해당 ID가 우선하므로
운영 전환 시에는 기존 키명은 유지하고 값만 신규 저장소 ID로 교체한다.

## 결정론적 위험도·대표유형 집계

모델이 반환한 위험도는 후보값이다. 앱은 루트의 `risk_rules.json`을
적용하여 제8조제1항 제1호~제5호 위험도를 10·9·8·7점으로 고정하고,
유효한 원문 `expression_id`만 발생근거로 집계한다. 조항별 위험도는
최댓값이며 합산하거나 평균하지 않는다.

대표유형은 다음 두 기준을 각각 산출한다.

- 최다빈도: 발생횟수 → 위험도 → 최고위험 근거수 → 행정처분 우선순위 →
  최초 위치 → 조항번호
- 최고위험: 위험도 → 최고위험 근거수 → 발생횟수 → 행정처분 우선순위 →
  최초 최고위험 위치 → 조항번호

두 결과가 같으면 하나의 대표유형에 두 선정기준을 함께 표시한다. 현재
제1호와 제2호가 완전히 동률이면 행정처분 강도가 높은 제1호를 대표로
선정한다. PoC Schema에는 문자 오프셋이 없으므로 중복 제거 단위는 제품·조항·
판단유형별 고유 `expression_id`이다. 비교·비방은 현행 제1호~제5호
집계 범위 밖이므로 탐지 후보에는 남기되 결정론적 전체 위험도와
대표유형에는 포함하지 않는다.

## 공개 승인 건강기능식품 제품 마스터

제품명 입력값이 있으면 GitHub Release의 버전 고정 SQLite 제품 마스터를
내려받아 정규화 품목명 정확조회를 수행한다. 유일한 정확일치만
`HEALTH_FUNCTIONAL_FOOD` 및 `FS21_HFF_REVIEW` 라우팅의 결정론적 근거로
사용한다. 미일치나 중복 품목명은 건강기능식품 확정 근거로 사용하지 않는다.

Release 자산은 83,687행 공개 승인 원천 CSV에서 재현 가능하게 생성하며,
앱은 SHA-256과 행 수, SQLite 무결성을 확인한 뒤 사용한다.
버전·원천 및 SQLite 해시는 `data/product_master_manifest.json`에서 확인한다.

```powershell
python scripts/build_product_master.py `
  --source "C:\path\mfds_health_functional_food_product_master_83687.csv" `
  --output ".codex_tmp\mfds_health_functional_food_product_master_83687.sqlite3"
```

## Rule 전용 검색

위반유형 후보가 탐지되면 혼합 검색 결과에 의존하지 않고 후보별 Rule
검색을 별도로 실행한다. File Search 파일의 `record_class=RULE`,
`violation_type`, `active=true` attributes를 API 필터로 강제하며, 1차
검색이 비어 있으면 동의어를 포함한 보완 질의로 한 번 더 검색한다.

활성 위반항목에는 실제 검색된 `RULE::` ID가 하나 이상 필요하다. Rule이
확보되지 않으면 위험도와 대표유형 집계에서 제외하고
`SEARCH_NO_RULE`, `INSUFFICIENT_EVIDENCE`로 담당자 검토에 전달한다.

검증된 원천 JSONL에서 필터 가능한 Rule 파일을 생성·동기화하는 명령은
다음과 같다. 생성 파일은 `.codex_tmp`에만 남고 공개 저장소에 커밋하지
않는다.

```powershell
python scripts/sync_rule_corpus.py `
  --source-root "C:\path\filesearch_upload" `
  --apply
```
