from __future__ import annotations

import sys
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


def top_keywords() -> pd.DataFrame:
    return query_df(
        """
        SELECT
            k.keyword,
            COUNT(*) AS count,
            MIN(k.first_seen_at) AS first_seen_at
        FROM artwork_keywords k
        JOIN artworks a ON a.id = k.artwork_id
        WHERE a.category = '일러스트'
        GROUP BY k.keyword
        ORDER BY count DESC, keyword ASC
        """
    )


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


def illustration_authors(author: str) -> list[str]:
    df = author_activity(author)
    if df.empty:
        return []
    return df["author"].dropna().astype(str).tolist()


def recent_artworks(author: str, keyword: str) -> pd.DataFrame:
    params: list[object] = ["일러스트"]
    where = "WHERE a.category = ?"
    if author.strip():
        where += " AND COALESCE(a.author, '') = ?"
        params.append(author.strip())
    if keyword.strip():
        where += """
        AND EXISTS (
            SELECT 1
            FROM artwork_keywords keyword_filter
            WHERE keyword_filter.artwork_id = a.id
              AND keyword_filter.keyword = ?
        )
        """
        params.append(keyword.strip())
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
    for row_start in range(0, len(visible_df), 3):
        cols = st.columns(3)
        for col, (_, row) in zip(cols, visible_df.iloc[row_start : row_start + 3].iterrows()):
            with col:
                if row.get("image_url"):
                    st.image(row["image_url"], use_container_width=True)
                st.markdown(f"**{row.get('title') or '제목 없음'}**")
                st.caption(row.get("author") or "작가 정보 없음")
                if row.get("keywords"):
                    st.write(row["keywords"])
                st.caption(f"확인: {row.get('last_seen_at')}")
    st.dataframe(visible_df, hide_index=True, use_container_width=True)
    pager("artwork", page, total_pages, len(df))


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
    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 6px;
        padding: 8px 12px;
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
            st.success(f"완료: 요소 {result['seen']}개 확인, 신규 {result['new']}개")
        else:
            st.error(f"업데이트 실패: {result.get('message', '알 수 없는 오류')}")

    st.divider()
    author_filter = st.text_input("일러스트 작가 검색")
    author_options = illustration_authors(author_filter)
    selected_author = st.selectbox(
        "작가 선택",
        ["전체 일러스트 작가"] + author_options,
        index=0,
    )
    if selected_author != "전체 일러스트 작가":
        st.session_state["selected_author_filter"] = selected_author

    selected_author_filter = st.session_state.get("selected_author_filter", "")
    selected_keyword_filter = st.session_state.get("selected_keyword_filter", "")

    st.divider()
    st.caption("선택된 필터")
    st.write(f"작가: {selected_author_filter or '전체'}")
    st.write(f"키워드: {selected_keyword_filter or '전체'}")
    if st.button("필터 초기화", use_container_width=True):
        st.session_state["selected_author_filter"] = ""
        st.session_state["selected_keyword_filter"] = ""
        st.rerun()

runs = latest_run()
keyword_df = top_keywords()
author_df = author_activity(author_filter)
artwork_df = recent_artworks(selected_author_filter, selected_keyword_filter)

if not runs.empty:
    st.caption(f"최근 업데이트: {runs.iloc[0]['finished_at'] or runs.iloc[0]['started_at']} · {runs.iloc[0]['status']}")

if selected_author_filter or selected_keyword_filter:
    st.info(
        f"요소 탭 필터: 작가 `{selected_author_filter or '전체'}` · 키워드 `{selected_keyword_filter or '전체'}`"
    )

metric_cols = st.columns(3)
metric_cols[0].metric("수집 키워드", len(keyword_df))
metric_cols[1].metric("작가", len(author_df))
metric_cols[2].metric("수집 요소", len(artwork_df))

view_options = ["키워드 랭킹", "작가", "요소", "업데이트"]
active_view = st.segmented_control(
    "보기",
    view_options,
    selection_mode="single",
    default=st.session_state.get("active_view", "키워드 랭킹"),
    label_visibility="collapsed",
)

if active_view:
    st.session_state["active_view"] = active_view

if active_view == "키워드 랭킹":
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
