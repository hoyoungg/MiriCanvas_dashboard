# 미리캔버스 키워드 대시보드

로컬 PC에서만 동작하는 Python 대시보드입니다. 사용자가 업데이트 버튼을 누를 때마다 기존 수집 데이터를 비우고, 미리캔버스 요소 API에서 그 시점의 요소 약 5000개를 가져와 키워드, 작가, 썸네일 정보를 SQLite에 저장합니다.

## 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app/dashboard.py
```

브라우저가 뜨면 왼쪽 사이드바의 `사이트 키워드 업데이트` 버튼을 누르세요. 키워드를 직접 입력하지 않아도, 요소 API에서 가져온 최신 요소들의 `keywords`와 `originKeywords`를 기준으로 랭킹을 만듭니다.

업데이트가 끝나면 `AI 추천` 탭에서 현재 많이 올라온 일러스트 키워드와 함께 등장한 연관 키워드를 바탕으로, 향후 약 2주 동안 참고할 만한 그림 키워드 20개를 확인할 수 있습니다.

## 팝업 API 찾기

요소 탭을 눌렀을 때 뜨는 팝업의 실제 API를 찾으려면 네트워크 로거를 실행하세요.

```bash
source .venv/bin/activate
python app/discover_api.py
```

열린 브라우저에서 직접 `요소` 탭과 원하는 팝업을 누르면 `fetch/XHR` 요청 후보가 터미널과 `data/network_logs/*.jsonl`에 저장됩니다.

## 저장 데이터

- `data/miricanvas_dashboard.sqlite3`: 수집 결과
- `user_data/miricanvas`: API 탐색용 Playwright 브라우저 프로필과 로그인 세션

## 주의

미리캔버스 화면 구조가 바뀌면 일부 선택자가 맞지 않을 수 있습니다. 이 도구는 로그인 우회나 제한 우회를 하지 않고, 사용자가 접근 가능한 화면에서 보이는 데이터만 수집하도록 설계했습니다.
