from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app import storage


ROOT = Path(__file__).resolve().parents[1]
MIRICANVAS_URL = "https://www.miricanvas.com/v2/ko/design2"
USER_DATA_DIR = ROOT / "user_data" / "miricanvas"

CHROMIUM_ARGS = ["--disable-crash-reporter", "--disable-crashpad"]
ELEMENT_API_URL = "https://api.miricanvas.com/api/element"
ELEMENT_TYPE_BY_CATEGORY = {
    "일러스트": "ILLUST_GROUP",
    "요소 전체": "ELEMENT",
    "애니메이션": "ANI",
    "선": "LINE",
    "프레임": "FRAME",
    "차트": "CHART",
    "텍스트": "MOCKUP_TEXT",
}
DEFAULT_CATEGORIES = ["일러스트", "애니메이션", "선", "프레임", "차트", "텍스트"]
DEFAULT_PAGE_SIZE = 50
DEFAULT_PAGES = 9
NOISE_WORDS = {
    "검색",
    "일러스트",
    "요소",
    "템플릿",
    "사진",
    "업로드",
    "더보기",
    "전체",
    "닫기",
    "추천",
    "최근",
    "인기",
    "프리미엄",
    "무료",
}


def normalize_keyword(value: str) -> str:
    value = re.sub(r"[#,\[\]{}()|]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def split_keywords(*values: str | None) -> list[str]:
    keywords: set[str] = set()
    for value in values:
        if not value:
            continue
        pieces = re.split(r"[,#/·•\n\r\t]+", value)
        for piece in pieces:
            keyword = normalize_keyword(piece)
            if 1 < len(keyword) <= 40 and keyword not in NOISE_WORDS:
                keywords.add(keyword)
    return sorted(keywords)


def split_hashtags(*values: str | None) -> list[str]:
    hashtags: set[str] = set()
    for value in values:
        if not value:
            continue
        for match in re.findall(r"#[0-9a-zA-Z가-힣_+-]+", value):
            keyword = normalize_keyword(match)
            if 1 < len(keyword) <= 40 and keyword not in NOISE_WORDS:
                hashtags.add(keyword)
    return sorted(hashtags)


def fingerprint_for(item: dict[str, object]) -> str:
    basis = "|".join(
        str(item.get(key) or "")
        for key in ("remote_id", "image_url", "title", "author", "source_url")
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def unique_texts(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        keyword = normalize_keyword(value)
        if keyword and keyword not in seen and keyword not in NOISE_WORDS:
            seen.add(keyword)
            result.append(keyword)
    return result


def click_first(page, selectors: list[str], timeout: int = 1500) -> bool:
    for selector in selectors:
        try:
            target = page.locator(selector).first
            if target.count() and target.is_visible(timeout=timeout):
                target.click(timeout=timeout)
                page.wait_for_timeout(800)
                return True
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    return False


def collect_left_panel_elements(page, limit: int) -> list[dict[str, object]]:
    raw_items = page.evaluate(
        """
        (limit) => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 24 && rect.height > 24 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const leftPanels = Array.from(document.querySelectorAll('aside, nav, section, div'))
            .map((el) => ({ el, rect: el.getBoundingClientRect() }))
            .filter(({ el, rect }) => {
              if (!visible(el)) return false;
              if (rect.left > 560 || rect.top > 180) return false;
              if (rect.width < 180 || rect.width > 560) return false;
              if (rect.height < 320) return false;
              return true;
            })
            .sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));
          const root = leftPanels.length ? leftPanels[0].el : document.body;
          const candidates = [];
          const nodes = Array.from(root.querySelectorAll('img, [role="img"], button, a, [data-testid], [aria-label]'));
          const attrText = (el) => {
            const attrs = [];
            for (const attr of el.attributes || []) {
              const name = attr.name.toLowerCase();
              if (
                name === 'alt' ||
                name === 'title' ||
                name === 'aria-label' ||
                name.includes('keyword') ||
                name.includes('tag') ||
                name.includes('name')
              ) {
                attrs.push(attr.value);
              }
            }
            return attrs.filter(Boolean).join(' ');
          };
          for (const el of nodes) {
            if (!visible(el)) continue;
            const card = el.closest('[data-testid], [class*="item"], [class*="card"], [class*="thumbnail"], [class*="element"], li, button, a') || el;
            if (!root.contains(card)) continue;
            const img = card.querySelector('img') || (el.tagName === 'IMG' ? el : null);
            const imageUrl = img ? (img.currentSrc || img.src || '') : '';
            const title = [attrText(el), img && attrText(img), attrText(card)].filter(Boolean).join(' ');
            const text = (card.innerText || '').trim();
            const hrefNode = card.closest('a') || card.querySelector('a');
            const href = hrefNode ? hrefNode.href : location.href;
            const rect = card.getBoundingClientRect();
            if (!imageUrl && !title && !text) continue;
            candidates.push({
              title,
              text,
              image_url: imageUrl,
              source_url: href,
              panel_left: Math.round(rect.left),
              panel_top: Math.round(rect.top)
            });
            if (candidates.length >= limit) break;
          }
          return candidates;
        }
        """,
        limit,
    )

    items: list[dict[str, object]] = []
    for raw in raw_items:
        title = normalize_keyword(raw.get("title") or "")
        text = str(raw.get("text") or "")
        author = extract_author(text)
        hashtags = split_hashtags(str(raw.get("title") or ""), text)
        keywords = sorted(set(split_keywords(title, text)) | set(hashtags))
        item = {
            "title": title or None,
            "author": author,
            "image_url": raw.get("image_url") or None,
            "source_url": raw.get("source_url") or MIRICANVAS_URL,
            "keywords": keywords,
        }
        item["fingerprint"] = fingerprint_for(item)
        if item["image_url"] or item["title"] or item["keywords"]:
            items.append(item)
    return items


def collect_visible_artworks(page, limit: int) -> list[dict[str, object]]:
    return collect_left_panel_elements(page, limit)


def extract_author(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if re.search(r"(작가|creator|author|by)\s*[:：]?", line, re.IGNORECASE):
            cleaned = re.sub(r"^(작가|creator|author|by)\s*[:：]?\s*", "", line, flags=re.IGNORECASE)
            return cleaned[:80] or None
    if len(lines) >= 2 and len(lines[-1]) <= 40:
        return lines[-1]
    return None


def element_keywords(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return split_keywords("|".join(str(item) for item in value))
    return split_keywords(str(value).replace("|", "\n"))


def item_from_element_api(
    raw: dict[str, object],
    source_url: str,
    now: str,
    category: str,
) -> dict[str, object]:
    account_info = raw.get("accountInfo") if isinstance(raw.get("accountInfo"), dict) else {}
    update_account_info = raw.get("updateAccountInfo") if isinstance(raw.get("updateAccountInfo"), dict) else {}
    author = (
        raw.get("licenseName")
        or raw.get("creatorName")
        or account_info.get("name")
        or update_account_info.get("name")
    )
    keywords = sorted(
        set(element_keywords(raw.get("keywords")))
        | set(element_keywords(raw.get("originKeywords")))
    )
    remote_id = raw.get("key") or raw.get("idx") or raw.get("resourceId")
    item = {
        "remote_id": remote_id,
        "title": raw.get("name") or raw.get("title"),
        "author": author,
        "category": category,
        "image_url": raw.get("thumbnailUrl") or raw.get("previewUrl") or raw.get("animatedThumbnailUrl"),
        "source_url": f"{source_url}#{remote_id}" if remote_id else source_url,
        "keywords": keywords,
        "first_seen_at": now,
    }
    item["fingerprint"] = fingerprint_for(item)
    return item


def fetch_element_api(
    keyword: str,
    element_category: str,
    page: int,
    page_size: int,
    tier: str,
) -> tuple[str, list[dict[str, object]]]:
    element_type = ELEMENT_TYPE_BY_CATEGORY.get(element_category, "ILLUST_GROUP")
    params = {
        "typeList": element_type,
        "page": page,
        "pageSize": page_size,
        "status": "ACTIVE",
        "tier": tier,
        "includePresetV2": "true",
        "domain": "production",
        "language": "ko",
    }
    if keyword:
        params["keyword"] = keyword
        params["keywordCategoryEntityType"] = "ELEMENT"
    url = f"{ELEMENT_API_URL}?{urlencode(params)}"
    response = requests.get(
        url,
        headers={
            "Accept": "application/json",
            "Origin": "https://www.miricanvas.com",
            "Referer": MIRICANVAS_URL,
            "User-Agent": "Mozilla/5.0",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    items = data.get("list") or []
    if not isinstance(items, list):
        return url, []
    return url, [item for item in items if isinstance(item, dict)]


def collect_elements_from_api(
    keywords: list[str],
    element_category: str,
    pages: int,
    page_size: int,
    now: str,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    normalized_keywords = [""] + [keyword.strip() for keyword in keywords if keyword.strip()]
    for keyword in dict.fromkeys(normalized_keywords):
        for tier in ["FREE", "PREMIUM"]:
            for page in range(1, pages + 1):
                source_url, raw_items = fetch_element_api(
                    keyword=keyword,
                    element_category=element_category,
                    page=page,
                    page_size=page_size,
                    tier=tier,
                )
                for raw in raw_items:
                    item = item_from_element_api(raw, source_url, now, element_category)
                    if item["keywords"] or item["title"] or item["image_url"]:
                        items.append(item)
    return items


def collect_site_elements_from_api(now: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for category in DEFAULT_CATEGORIES:
        for tier in ["FREE", "PREMIUM"]:
            for page in range(1, DEFAULT_PAGES + 1):
                source_url, raw_items = fetch_element_api(
                    keyword="",
                    element_category=category,
                    page=page,
                    page_size=DEFAULT_PAGE_SIZE,
                    tier=tier,
                )
                for raw in raw_items:
                    item = item_from_element_api(raw, source_url, now, category)
                    if item["keywords"] or item["title"] or item["image_url"]:
                        items.append(item)
    return items


def collect_search_suggestions(page, seeds: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    input_selectors = [
        'input[type="search"]',
        'input[placeholder*="검색"]',
        '[contenteditable="true"]',
        'textarea[placeholder*="검색"]',
    ]
    for seed in [""] + seeds:
        if not click_first(page, input_selectors, timeout=1000):
            continue
        keyboard = page.keyboard
        keyboard.press("Meta+A")
        keyboard.press("Control+A")
        keyboard.press("Backspace")
        if seed:
            keyboard.type(seed, delay=30)
        page.wait_for_timeout(1200)
        suggestions = page.evaluate(
            """
            () => {
              const nodes = Array.from(document.querySelectorAll(
                '[role="option"], [role="listbox"] *, [class*="suggest"] *, [class*="autocomplete"] *, li, button'
              ));
              return nodes
                .map((node) => (node.innerText || node.textContent || '').trim())
                .filter((text) => text.length >= 2 && text.length <= 80);
            }
            """
        )
        result[seed or "focus"] = unique_texts(suggestions)[:50]
    return result


def run_update(
    headless: bool = False,
    artwork_limit: int = 120,
    seeds: list[str] | None = None,
    element_category: str = "일러스트",
    pages: int = 1,
) -> dict[str, int | str]:
    storage.init_db()
    now = datetime.now().isoformat(timespec="seconds")
    run_id = storage.start_run(now)
    added = 0
    seen = 0
    suggestions = 0

    try:
        storage.clear_snapshot_data()
        if seeds:
            items = collect_elements_from_api(
                keywords=seeds,
                element_category=element_category,
                pages=max(1, pages),
                page_size=max(1, min(100, artwork_limit)),
                now=now,
            )
        else:
            items = collect_site_elements_from_api(now)

        for item in items:
            seen += 1
            if storage.upsert_artwork(item, now):
                added += 1

        message = f"artworks seen={seen}, new={added}, suggestions={suggestions}"
        storage.finish_run(run_id, datetime.now().isoformat(timespec="seconds"), "success", message)
        return {"status": "success", "seen": seen, "new": added, "suggestions": suggestions}
    except Exception as exc:
        storage.finish_run(run_id, datetime.now().isoformat(timespec="seconds"), "failed", str(exc))
        return {"status": "failed", "message": str(exc), "seen": seen, "new": added, "suggestions": suggestions}


if __name__ == "__main__":
    print(run_update())
