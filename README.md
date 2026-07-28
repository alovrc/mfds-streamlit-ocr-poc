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

위험점수와 관계없이 활성 후보는 원문 문제표현, 로컬 Rule ID 및 File
Search에서 회수한 공식근거 ID가 모두 연결되어야 유효하다. 하나라도
누락되면 해당 후보를 `INSUFFICIENT_EVIDENCE`로 유지하고 위험도·대표유형
집계에서 제외한다. 공식근거가 연결된 유효 후보가 하나라도 있으면 레코드
전체 검토상태는 `SUFFICIENT_EVIDENCE`이며, 다른 미해결 후보는 별도
담당자 검토 대상으로 표시한다.

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

## 결정론적 로컬 Rule 카탈로그

Rule은 의미검색 대상이 아니라 버전 고정 기준표로 취급한다. 검증된 활성
Rule 원천에서 제1호부터 제7호까지의 대표 법적 기준을 추출한
`rule_catalog.json`을 앱 소스와 함께 배포한다. 모델이 반환한 `rule_ids`는
신뢰하지 않고 제거하며, 후보 위반유형과 제품 경로에 따라 로컬 코드가
`RULE::FOOD_REVIEW::*` 또는 `RULE::HFF_REVIEW::*` ID를 결정론적으로
연결한다.

File Search는 공식 근거와 사례 검색에만 사용한다. 활성 후보에 대응하는
로컬 Rule이 없으면 `SEARCH_NO_RULE`, 공식근거가 없으면
`SEARCH_NO_OFFICIAL_EVIDENCE`와 `INSUFFICIENT_EVIDENCE`로 담당자 검토에
전달한다. 따라서 정상적인 제품
한 건은 1단계 제품유형 검색 1회와 2단계 위반 검토 검색 1회, 총 2회의
Responses API 호출로 처리되며 별도 Vector Store Rule 검색은 발생하지
않는다.

카탈로그에는 원천 데이터 버전과 원천 JSONL SHA-256을 함께 기록한다.
구체적인 고시 조건에만 적용되는 보조 Rule은 유형만 같다는 이유로 자동
연결하지 않고, 각 위반유형의 기본 법적 기준만 사용한다.
