from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "miricanvas_dashboard.sqlite3"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS crawl_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                message TEXT
            );

            CREATE TABLE IF NOT EXISTS artworks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                title TEXT,
                author TEXT,
                category TEXT,
                image_url TEXT,
                source_url TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artwork_keywords (
                artwork_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (artwork_id, keyword),
                FOREIGN KEY (artwork_id) REFERENCES artworks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS search_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                seed TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL DEFAULT 1,
                UNIQUE(keyword, seed)
            );

            CREATE INDEX IF NOT EXISTS idx_artworks_seen ON artworks(first_seen_at, last_seen_at);
            CREATE INDEX IF NOT EXISTS idx_artworks_author ON artworks(author);
            CREATE INDEX IF NOT EXISTS idx_keywords_keyword ON artwork_keywords(keyword);
            CREATE INDEX IF NOT EXISTS idx_suggestions_keyword ON search_suggestions(keyword);
            """
        )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(artworks)").fetchall()
        }
        if "category" not in columns:
            conn.execute("ALTER TABLE artworks ADD COLUMN category TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artworks_category ON artworks(category)")


def start_run(started_at: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO crawl_runs (started_at, status) VALUES (?, ?)",
            (started_at, "running"),
        )
        return int(cur.lastrowid)


def finish_run(run_id: int, finished_at: str, status: str, message: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE crawl_runs
            SET finished_at = ?, status = ?, message = ?
            WHERE id = ?
            """,
            (finished_at, status, message, run_id),
        )


def clear_snapshot_data() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM artwork_keywords")
        conn.execute("DELETE FROM artworks")
        conn.execute("DELETE FROM search_suggestions")


def upsert_artwork(item: dict[str, object], now: str) -> bool:
    keywords = [str(k).strip() for k in item.get("keywords", []) if str(k).strip()]
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO artworks (
                fingerprint, title, author, category, image_url, source_url, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                title = COALESCE(excluded.title, artworks.title),
                author = COALESCE(excluded.author, artworks.author),
                category = COALESCE(excluded.category, artworks.category),
                image_url = COALESCE(excluded.image_url, artworks.image_url),
                source_url = COALESCE(excluded.source_url, artworks.source_url),
                last_seen_at = excluded.last_seen_at
            """,
            (
                item["fingerprint"],
                item.get("title"),
                item.get("author"),
                item.get("category"),
                item.get("image_url"),
                item.get("source_url"),
                now,
                now,
            ),
        )
        artwork_id = conn.execute(
            "SELECT id FROM artworks WHERE fingerprint = ?",
            (item["fingerprint"],),
        ).fetchone()["id"]

        for keyword in sorted(set(keywords)):
            conn.execute(
                """
                INSERT OR IGNORE INTO artwork_keywords (artwork_id, keyword, first_seen_at)
                VALUES (?, ?, ?)
                """,
                (artwork_id, keyword, now),
            )
        return cur.rowcount == 1


def upsert_suggestion(keyword: str, seed: str, now: str) -> None:
    keyword = keyword.strip()
    if not keyword:
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO search_suggestions (keyword, seed, first_seen_at, last_seen_at, seen_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(keyword, seed) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                seen_count = search_suggestions.seen_count + 1
            """,
            (keyword, seed, now, now),
        )
