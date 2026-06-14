from __future__ import annotations

import sys
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import storage
from app.crawler import run_update


st.set_page_config(page_title="미리캔버스 키워드 대시보드", layout="wide")
storage.init_db()

PAGE_SIZE = 15


def query_df(sql: str, params: tuple[object, ...] = ()) -> pd.DataFrame:
    with storage.connect() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def paginated(df: pd.DataFrame, key: str) -> tuple[pd.DataFrame, int, int]:
    if df.empty:
        return df, 1, 1
    total_pages = max(1, (len(df) + PAGE_SIZE - 1) // PAGE_SIZE)
    page_key = f"{key}_page_value"
    page = int(st.session_state.get(page_key, 1))
    page = min(max(page, 1), total_pages)
    st.session_state[page_key] = page
    start = (page - 1) * PAGE_SIZE
    return df.iloc[start : start + PAGE_SIZE], page, total_pages


def pager(key: str, page: int, total_pages: int, total_rows: int) -> None:
    if total_rows <= PAGE_SIZE:
        return
    page_key = f"{key}_page_value"
    left, middle, right = st.columns([1, 2, 1])
    with left:
        if st.button("이전", key=f"{key}_prev", use_container_width=True, disabled=page <= 1):
            st.session_state[page_key] = page - 1
            st.rerun()
    with middle:
        selected = st.number_input(
            "페이지",
            min_value=1,
            max_value=total_pages,
            value=page,
            step=1,
            key=f"{key}_page_input",
        )
        if selected != page:
            st.session_state[page_key] = int(selected)
            st.rerun()
        st.caption(f"{page} / {total_pages} 페이지 · 총 {total_rows}개")
    with right:
        if st.button("다음", key=f"{key}_next", use_container_width=True, disabled=page >= total_pages):
            st.session_state[page_key] = page + 1
            st.rerun()


def clear_keyword_filter() -> None:
    had_filter = bool(st.session_state.get("selected_keyword_filter", ""))
    st.session_state["selected_keyword_filter"] = ""
    st.session_state["keyword_search_input"] = ""
    st.session_state["artwork_page_value"] = 1
    st.session_state["keyword_page_value"] = 1
    st.session_state["active_view"] = "요소" if had_filter else "키워드 랭킹"


def clear_author_filter() -> None:
    had_filter = bool(st.session_state.get("selected_author_filter", ""))
    st.session_state["selected_author_filter"] = ""
    st.session_state["author_search_input"] = ""
    st.session_state["artwork_page_value"] = 1
    st.session_state["author_page_value"] = 1
    st.session_state["active_view"] = "요소" if had_filter else "작가"


def clear_all_filters() -> None:
    st.session_state["selected_keyword_filter"] = ""
    st.session_state["selected_author_filter"] = ""
    st.session_state["keyword_search_input"] = ""
    st.session_state["author_search_input"] = ""
    st.session_state["artwork_page_value"] = 1
    st.session_state["keyword_page_value"] = 1
    st.session_state["author_page_value"] = 1
    st.session_state["active_view"] = "요소"


def apply_author_search() -> None:
    st.session_state["author_page_value"] = 1
    st.session_state["active_view"] = "작가"


def apply_keyword_search() -> None:
    st.session_state["keyword_page_value"] = 1
    st.session_state["active_view"] = "키워드 랭킹"


def top_keywords(keyword: str = "") -> pd.DataFrame:
    params: list[object] = []
    where = "WHERE a.category = '일러스트'"
    if keyword.strip():
        where += " AND k.keyword LIKE ?"
        params.append(f"%{keyword.strip()}%")
    return query_df(
        f"""
        SELECT
            k.keyword,
            COUNT(*) AS count,
            MIN(k.first_seen_at) AS first_seen_at
        FROM artwork_keywords k
        JOIN artworks a ON a.id = k.artwork_id
        {where}
        GROUP BY k.keyword
        ORDER BY count DESC, keyword ASC
        """,
        tuple(params),
    )


def keyword_neighbors(keyword: str) -> list[str]:
    df = query_df(
        """
        SELECT k2.keyword, COUNT(*) AS count
        FROM artwork_keywords k1
        JOIN artwork_keywords k2 ON k2.artwork_id = k1.artwork_id
        JOIN artworks a ON a.id = k1.artwork_id
        WHERE a.category = '일러스트'
          AND k1.keyword = ?
          AND k2.keyword != ?
        GROUP BY k2.keyword
        ORDER BY count DESC, k2.keyword ASC
        LIMIT 4
        """,
        (keyword, keyword),
    )
    if df.empty:
        return []
    return df["keyword"].dropna().astype(str).tolist()


def ai_recommendations() -> pd.DataFrame:
    keywords = top_keywords()
    if keywords.empty:
        return pd.DataFrame(columns=["추천 키워드", "근거", "그림 방향"])

    recommendations: list[dict[str, str]] = []
    used: set[str] = set()
    for _, row in keywords.head(80).iterrows():
        keyword = str(row["keyword"])
        if keyword in used or len(keyword) <= 1:
            continue
        neighbors = keyword_neighbors(keyword)
        neighbor_text = ", ".join(neighbors[:3]) if neighbors else "단독 활용"
        if neighbors:
            direction = f"{keyword} 키워드를 중심으로 {', '.join(neighbors[:2])} 요소를 결합한 일러스트"
        else:
            direction = f"{keyword} 키워드를 명확하게 보여주는 단품 일러스트"
        recommendations.append(
            {
                "추천 키워드": keyword,
                "근거": f"현재 일러스트 {int(row['count'])}개에서 등장 · 연관: {neighbor_text}",
                "그림 방향": direction,
            }
        )
        used.add(keyword)
        if len(recommendations) == 20:
            break

    return pd.DataFrame(recommendations)


def author_activity(author: str) -> pd.DataFrame:
    params: list[object] = ["일러스트"]
    where = "WHERE category = ? AND author IS NOT NULL AND author != ''"
    if author.strip():
        where += " AND author LIKE ?"
        params.append(f"%{author.strip()}%")
    return query_df(
        f"""
        SELECT author, COUNT(*) AS artwork_count, MIN(first_seen_at) AS first_seen_at, MAX(last_seen_at) AS last_seen_at
        FROM artworks
        {where}
        GROUP BY author
        ORDER BY artwork_count DESC, last_seen_at DESC
        """,
        tuple(params),
    )


def recent_artworks(author: str, keyword: str) -> pd.DataFrame:
    params: list[object] = ["일러스트"]
    where = "WHERE a.category = ?"
    if author.strip():
        where += " AND COALESCE(a.author, '') LIKE ?"
        params.append(f"%{author.strip()}%")
    if keyword.strip():
        where += """
        AND EXISTS (
            SELECT 1
            FROM artwork_keywords keyword_filter
            WHERE keyword_filter.artwork_id = a.id
              AND keyword_filter.keyword LIKE ?
        )
        """
        params.append(f"%{keyword.strip()}%")
    return query_df(
        f"""
        SELECT
            a.title,
            a.author,
            a.category,
            GROUP_CONCAT(k.keyword, ', ') AS keywords,
            a.first_seen_at,
            a.last_seen_at,
            a.image_url
        FROM artworks a
        LEFT JOIN artwork_keywords k ON k.artwork_id = a.id
        {where}
        GROUP BY a.id
        ORDER BY a.last_seen_at DESC
        """,
        tuple(params),
    )


def latest_run() -> pd.DataFrame:
    return query_df(
        """
        SELECT started_at, finished_at, status, message
        FROM crawl_runs
        ORDER BY id DESC
        LIMIT 10
        """
    )


def render_table(df: pd.DataFrame, key: str) -> None:
    visible_df, page, total_pages = paginated(df, key)
    st.dataframe(visible_df, hide_index=True, use_container_width=True)
    pager(key, page, total_pages, len(df))


def render_clickable_table(df: pd.DataFrame, key: str, selected_column: str, button_label: str) -> None:
    visible_df, page, total_pages = paginated(df, key)
    if visible_df.empty:
        st.dataframe(visible_df, hide_index=True, use_container_width=True)
        return

    header_cols = st.columns([1, 4, 2, 3, 3])
    header_cols[0].caption("선택")
    for col, name in zip(header_cols[1:], visible_df.columns[:4]):
        col.caption(str(name))

    for idx, (_, row) in enumerate(visible_df.iterrows()):
        cols = st.columns([1, 4, 2, 3, 3])
        selected_value = str(row[selected_column])
        if cols[0].button(button_label, key=f"{key}_select_{page}_{idx}", use_container_width=True):
            if selected_column == "keyword":
                st.session_state["selected_keyword_filter"] = selected_value
                st.session_state["selected_author_filter"] = ""
            elif selected_column == "author":
                st.session_state["selected_author_filter"] = selected_value
            st.session_state["artwork_page_value"] = 1
            st.session_state["active_view"] = "요소"
            st.rerun()
        for col, name in zip(cols[1:], visible_df.columns[:4]):
            value = row[name]
            col.write("" if pd.isna(value) else value)

    pager(key, page, total_pages, len(df))


def render_artwork_gallery(df: pd.DataFrame) -> None:
    visible_df, page, total_pages = paginated(df, "artwork")
    if visible_df.empty:
        st.info("조건에 맞는 일러스트가 없습니다.")
        return

    for row_start in range(0, len(visible_df), 3):
        cols = st.columns(3)
        for col, (_, row) in zip(cols, visible_df.iloc[row_start : row_start + 3].iterrows()):
            with col:
                title = escape(str(row.get("title") or "제목 없음"))
                author = escape(str(row.get("author") or "작가 정보 없음"))
                image_url = escape(str(row.get("image_url") or ""))
                keywords = [
                    escape(keyword.strip())
                    for keyword in str(row.get("keywords") or "").split(",")
                    if keyword.strip()
                ][:12]
                keyword_html = "".join(
                    f"<span class='keyword-chip'>{keyword}</span>"
                    for keyword in keywords
                )
                image_html = (
                    f"<img class='art-card-image' src='{image_url}' alt='{title}'>"
                    if image_url
                    else "<div class='art-card-empty'>이미지 없음</div>"
                )
                st.markdown(
                    f"""
                    <div class="art-card">
                        <div class="art-card-media">{image_html}</div>
                        <div class="art-card-body">
                            <div class="art-card-title">{title}</div>
                            <div class="art-card-author">{author}</div>
                            <div class="art-card-keywords">{keyword_html}</div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    pager("artwork", page, total_pages, len(df))


def render_recommendations(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("추천을 만들 데이터가 없습니다. 먼저 업데이트를 실행하세요.")
        return

    for row_start in range(0, len(df), 2):
        cols = st.columns(2)
        for col, (_, row) in zip(cols, df.iloc[row_start : row_start + 2].iterrows()):
            with col:
                keyword = escape(str(row["추천 키워드"]))
                reason = escape(str(row["근거"]))
                direction = escape(str(row["그림 방향"]))
                st.markdown(
                    f"""
                    <div class="recommend-card">
                        <div class="recommend-keyword">{keyword}</div>
                        <div class="recommend-direction">{direction}</div>
                        <div class="recommend-reason">{reason}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; }
    div[data-testid="stMetric"] {
        background: #f7f7f4;
        border: 1px solid #e7e3dc;
        border-radius: 8px;
        padding: 14px 16px;
    }
    section[data-testid="stSidebar"] {
        background: #fbfaf7;
    }
    section[data-testid="stSidebar"] > div {
        padding-top: 0.75rem;
    }
    section[data-testid="stSidebar"] h2 {
        font-size: 18px;
        line-height: 1.2;
        margin: 0 0 8px;
        padding: 0;
    }
    section[data-testid="stSidebar"] hr {
        margin: 12px 0;
    }
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        gap: 0.45rem;
    }
    section[data-testid="stSidebar"] div[data-testid="stButton"] {
        margin-bottom: 2px;
    }
    section[data-testid="stSidebar"] div[data-testid="stButton"] button {
        min-height: 34px;
        padding: 6px 10px;
        border-radius: 7px;
    }
    section[data-testid="stSidebar"] div[data-testid="stTextInput"] {
        margin-bottom: 2px;
    }
    section[data-testid="stSidebar"] div[data-testid="stTextInput"] label p {
        color: #26211c;
        font-weight: 800;
        font-size: 13px;
    }
    section[data-testid="stSidebar"] div[data-testid="stTextInput"] input {
        background: #ffffff;
        border: 1.5px solid #8d8173;
        border-radius: 8px;
        color: #211d19;
        box-shadow: 0 2px 8px rgba(34, 31, 27, 0.08);
        min-height: 36px;
    }
    section[data-testid="stSidebar"] div[data-testid="stTextInput"] input:focus {
        border-color: #2d6cdf;
        box-shadow: 0 0 0 2px rgba(45, 108, 223, 0.18);
    }
    section[data-testid="stSidebar"] div[data-testid="stTextInput"] input::placeholder {
        color: #756b60;
        opacity: 1;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 6px;
        padding: 8px 12px;
    }
    .art-card {
        background: #ffffff;
        border: 1px solid #e8e3da;
        border-radius: 8px;
        box-shadow: 0 8px 24px rgba(34, 31, 27, 0.08);
        overflow: hidden;
        margin-bottom: 18px;
    }
    .art-card-media {
        background: #f7f5f0;
        border-bottom: 1px solid #ece7df;
        display: flex;
        align-items: center;
        justify-content: center;
        aspect-ratio: 4 / 3;
    }
    .art-card-image {
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
        display: block;
    }
    .art-card-empty {
        color: #8a8175;
        font-size: 13px;
    }
    .art-card-body {
        padding: 12px 13px 14px;
    }
    .art-card-title {
        font-size: 15px;
        font-weight: 700;
        line-height: 1.35;
        margin-bottom: 5px;
        color: #25211d;
        word-break: keep-all;
    }
    .art-card-author {
        font-size: 12px;
        color: #746c61;
        margin-bottom: 9px;
    }
    .art-card-keywords {
        display: flex;
        flex-wrap: wrap;
        gap: 5px;
    }
    .keyword-chip {
        border: 1px solid #e3ded5;
        background: #faf8f4;
        border-radius: 999px;
        color: #4b433a;
        font-size: 11px;
        line-height: 1;
        padding: 5px 7px;
    }
    .recommend-card {
        background: #ffffff;
        border: 1px solid #e4ded4;
        border-radius: 8px;
        box-shadow: 0 8px 22px rgba(34, 31, 27, 0.07);
        padding: 16px 17px;
        margin-bottom: 14px;
    }
    .recommend-keyword {
        color: #1f1b17;
        font-size: 18px;
        font-weight: 800;
        margin-bottom: 8px;
    }
    .recommend-direction {
        color: #3e372f;
        font-size: 14px;
        line-height: 1.45;
        margin-bottom: 10px;
    }
    .recommend-reason {
        color: #796f63;
        font-size: 12px;
        line-height: 1.4;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("미리캔버스 요소 키워드")
st.caption("업데이트 버튼을 누를 때마다 기존 수집 데이터를 비우고, 그 시점의 요소 약 5000개에서 키워드 빈도를 다시 집계합니다.")

with st.sidebar:
    st.header("업데이트")
    if st.button("사이트 키워드 업데이트", type="primary", use_container_width=True):
        with st.spinner("미리캔버스 요소 API에서 데이터를 가져오는 중입니다..."):
            result = run_update()
        if result["status"] == "success":
            st.session_state["active_view"] = "AI 추천"
            st.success(f"완료: 요소 {result['seen']}개 확인, 신규 {result['new']}개")
        else:
            st.error(f"업데이트 실패: {result.get('message', '알 수 없는 오류')}")

    st.divider()
    author_search = st.text_input(
        "일러스트 작가 검색",
        key="author_search_input",
        on_change=apply_author_search,
        placeholder="작가명 입력 후 Enter",
    )
    st.button("작가 검색", use_container_width=True, on_click=apply_author_search)

    keyword_search = st.text_input(
        "키워드 검색",
        key="keyword_search_input",
        on_change=apply_keyword_search,
        placeholder="키워드 입력 후 Enter",
    )
    st.button("키워드 검색", use_container_width=True, on_click=apply_keyword_search)

    selected_author_filter = st.session_state.get("selected_author_filter", "")
    selected_keyword_filter = st.session_state.get("selected_keyword_filter", "")
    has_keyword_state = bool(selected_keyword_filter or keyword_search.strip())
    has_author_state = bool(selected_author_filter or author_search.strip())

    st.divider()
    st.caption("선택된 필터")
    st.write(f"키워드: {selected_keyword_filter or '전체'}")
    st.write(f"작가: {selected_author_filter or '전체'}")
    reset_keyword_col, reset_author_col = st.columns(2)
    reset_keyword_col.button(
        "키워드 초기화",
        use_container_width=True,
        disabled=not has_keyword_state,
        on_click=clear_keyword_filter,
    )
    reset_author_col.button(
        "작가 초기화",
        use_container_width=True,
        disabled=not has_author_state,
        on_click=clear_author_filter,
    )
    st.button(
        "전체 초기화",
        use_container_width=True,
        disabled=not (has_keyword_state or has_author_state),
        on_click=clear_all_filters,
    )

runs = latest_run()
keyword_df = top_keywords(keyword_search)
author_df = author_activity(author_search)
artwork_df = recent_artworks(selected_author_filter, selected_keyword_filter)
recommendation_df = ai_recommendations()

if not runs.empty:
    st.caption(f"최근 업데이트: {runs.iloc[0]['finished_at'] or runs.iloc[0]['started_at']} · {runs.iloc[0]['status']}")

if selected_author_filter or selected_keyword_filter:
    view_parts = []
    if selected_keyword_filter:
        view_parts.append(f"`{selected_keyword_filter}` 키워드")
    if selected_author_filter:
        view_parts.append(f"`{selected_author_filter}` 작가")
    st.info("현재 보기: " + " + ".join(view_parts) + "의 일러스트")

metric_cols = st.columns(3)
metric_cols[0].metric("수집 키워드", len(keyword_df))
metric_cols[1].metric("작가", len(author_df))
metric_cols[2].metric("수집 요소", len(artwork_df))

view_options = ["AI 추천", "키워드 랭킹", "작가", "요소", "업데이트"]
if st.session_state.get("active_view") not in view_options:
    st.session_state["active_view"] = "AI 추천"

active_view = st.segmented_control(
    "보기",
    view_options,
    selection_mode="single",
    key="active_view",
    label_visibility="collapsed",
)

if active_view == "AI 추천":
    st.subheader("향후 2주 추천 키워드")
    render_recommendations(recommendation_df)

elif active_view == "키워드 랭킹":
    st.subheader("키워드 랭킹")
    render_clickable_table(keyword_df, "keyword", "keyword", "보기")

elif active_view == "작가":
    st.subheader("일러스트 작가")
    render_clickable_table(author_df, "author", "author", "보기")

elif active_view == "요소":
    title_parts = []
    if selected_author_filter:
        title_parts.append(selected_author_filter)
    if selected_keyword_filter:
        title_parts.append(f"#{selected_keyword_filter}")
    st.subheader(" · ".join(title_parts) + " 일러스트" if title_parts else "전체 일러스트")
    render_artwork_gallery(artwork_df)

elif active_view == "업데이트":
    st.subheader("업데이트 기록")
    render_table(runs, "run")

if runs.empty:
    st.info("아직 업데이트 기록이 없습니다. 왼쪽의 업데이트 버튼을 눌러 첫 데이터를 수집하세요.")
