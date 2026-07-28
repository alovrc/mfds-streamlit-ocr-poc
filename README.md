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

제품유형 점수는 `food_confidence`와 `hff_confidence`로 분리한다.
`food_confidence >= 0.80`인 비상충 식품 후보는 식품 검토 경로로 정규화하며,
0.50~0.79 구간은 식품으로 확정하지 않고 `영양제` 표현의 건강기능식품 오인
가능성을 담당자 검토 대상으로 올리는 선별 기준으로만 사용한다.

## 결정론적 위험도·대표유형 집계

모델이 반환한 위험도는 후보값이다. 앱은 루트의 `risk_rules.json`을
적용하여 제8조제1항 제1호~제5호 위험도를 10·9·8·7점으로 고정하고,
유효한 원문 `expression_id`만 발생근거로 집계한다. 조항별 위험도는
최댓값이며 합산하거나 평균하지 않는다.

대표유형은 다음 두 기준을 각각 산출한다.

- 최다빈도: 발생횟수 → 위험도 → 최고위험 근거수 → 최초 위치 → 조항번호
- 최고위험: 위험도 → 최고위험 근거수 → 발생횟수 → 최초 최고위험 위치 → 조항번호

두 결과가 같으면 하나의 대표유형에 두 선정기준을 함께 표시한다. 현재
PoC Schema에는 문자 오프셋이 없으므로 중복 제거 단위는 제품·조항·
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
