# Streamlit 서버 내 Tesseract OCR 시험판

이 브랜치는 운영 `main`과 Streamlit 운영 앱을 변경하지 않고 URL 수집과
한글 OCR 가능성을 시험하기 위한 개발본이다.

## 범위

- 공개 `http`·`https` URL의 제목·본문·이미지 URL 수집
- 네이버 블로그 `mainFrame` 문서 처리
- 리다이렉트마다 URL과 DNS 주소 재검증
- 내부·사설·예약 IP, 비표준 포트, 사용자정보 포함 URL 차단
- HTML 2MB, 이미지 10MB, 이미지 20개, 리다이렉트 5회 제한
- 이미지 SHA-256 중복 제거
- Streamlit 서버의 Tesseract `kor+eng` OCR
- OCR 원문과 담당자 수정문구 분리 보존
- 본문과 분석 대상 OCR 문구의 출처표시 병합
- 기존 OpenAI 1·2단계 분석 입력으로 전달

OpenAI OCR이나 외부 OCR API는 호출하지 않는다. OCR 처리 자체의 OpenAI
API 호출 수는 0회다.

## Cloud 패키지

Streamlit Community Cloud는 `packages.txt`에서 다음 Debian 패키지를
설치한다.

```text
tesseract-ocr
tesseract-ocr-kor
tesseract-ocr-eng
```

Python 패키지는 `requirements.txt`의 `pytesseract`, `Pillow`, `httpx`,
`beautifulsoup4`를 사용한다.

## OCR 상태

```text
SUCCESS
PARTIAL_SUCCESS
NO_TEXT_DETECTED
FAILED
IMAGE_FETCH_FAILED
```

PoC 화면과 분석 데이터에는 OCR confidence를 저장하거나 표시하지 않는다.

## 실행

```powershell
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

Windows 로컬 실행에는 Tesseract와 한글·영문 언어팩을 별도로 설치해야 한다.
Streamlit Community Cloud에서는 `packages.txt`가 이를 담당한다.

## 검증

```powershell
python -m compileall -q .
python -m pytest -q
```

운영 반영 전에는 별도 시험 앱으로 배포해 다음을 확인해야 한다.

1. 한글 Tesseract 언어팩 로딩
2. 네이버 블로그 활성 게시물의 본문·이미지 수집
3. 이미지별 OCR 원문 및 실패상태 표시
4. 담당자 수정문구 재병합
5. 기존 OpenAI 결과와 Markdown 보고서 생성
6. CPU·메모리·처리시간

게시물이 삭제·비공개·차단된 경우에는 과거 본문과 이미지를 복원하지 못하며,
현재 공개된 차단 안내문만 수집될 수 있다.
