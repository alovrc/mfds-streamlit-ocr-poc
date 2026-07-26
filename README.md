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
