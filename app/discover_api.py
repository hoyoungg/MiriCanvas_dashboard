from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Request, Response, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
MIRICANVAS_URL = "https://www.miricanvas.com/v2/ko/design2"
USER_DATA_DIR = ROOT / "user_data" / "miricanvas"
LOG_DIR = ROOT / "data" / "network_logs"
CHROMIUM_ARGS = ["--disable-crash-reporter", "--disable-crashpad"]

INTERESTING_WORDS = [
    "keyword",
    "keywords",
    "tag",
    "tags",
    "hashtag",
    "element",
    "elements",
    "illustration",
    "recommend",
    "suggest",
    "search",
    "asset",
    "creator",
    "author",
    "키워드",
    "태그",
    "해시",
    "요소",
    "일러스트",
    "추천",
    "검색",
    "작가",
]


def looks_interesting(text: str) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in INTERESTING_WORDS)


def safe_body(response: Response) -> str | None:
    try:
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type and "text" not in content_type:
            return None
        body = response.text()
        if len(body) > 30_000:
            body = body[:30_000] + "\n... truncated ..."
        return body
    except Exception:
        return None


def request_summary(request: Request) -> dict[str, object]:
    try:
        post_data = request.post_data or ""
    except UnicodeDecodeError:
        post_data = "[binary or compressed request body skipped]"
    except Exception as exc:
        post_data = f"[request body unavailable: {exc}]"
    if len(post_data) > 10_000:
        post_data = post_data[:10_000] + "\n... truncated ..."
    return {
        "method": request.method,
        "url": request.url,
        "resource_type": request.resource_type,
        "post_data": post_data,
    }


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"miricanvas-network-{started_at}.jsonl"
    print(f"Network log: {log_path}")
    print("브라우저에서 미리캔버스 요소 탭을 누르고, 원하는 팝업을 직접 열어주세요.")
    print("요청이 들어오면 후보 API가 터미널과 JSONL 파일에 기록됩니다.")
    print("끝내려면 이 터미널에서 Ctrl+C를 누르세요.")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(USER_DATA_DIR),
            headless=False,
            viewport={"width": 1440, "height": 1000},
            locale="ko-KR",
            args=CHROMIUM_ARGS,
        )
        page = context.pages[0] if context.pages else context.new_page()

        def on_response(response: Response) -> None:
            request = response.request
            if request.resource_type not in {"fetch", "xhr"}:
                return

            summary = request_summary(request)
            body = safe_body(response)
            haystack = " ".join(
                [
                    str(summary["url"]),
                    str(summary["post_data"]),
                    body or "",
                ]
            )
            if not looks_interesting(haystack):
                return

            record = {
                "time": datetime.now().isoformat(timespec="seconds"),
                "status": response.status,
                "request": summary,
                "response_headers": dict(response.headers),
                "response_body": body,
            }
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            print(f"[{response.status}] {request.method} {request.url}")
            if summary["post_data"]:
                print(f"  post_data: {str(summary['post_data'])[:300]}")
            if body:
                print(f"  body: {body[:300].replace(chr(10), ' ')}")

        page.on("response", on_response)
        page.goto(MIRICANVAS_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1_000)

        try:
            while True:
                page.wait_for_timeout(1_000)
        except KeyboardInterrupt:
            context.close()


if __name__ == "__main__":
    main()
