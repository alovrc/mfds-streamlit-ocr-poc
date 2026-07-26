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
