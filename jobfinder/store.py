"""Small SQLite store that survives restarts: HTTP cache, jobs already seen, and hidden companies."""

import gzip
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

DB_PATH = os.environ.get(
    "JOBFINDER_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jobfinder.db"),
)

_lock = threading.Lock()
_ready = False


@contextmanager
def _connect():
    """Open a connection, commit on success, and always close it."""
    global _ready
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    if not _ready:
        with _lock:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS http_cache (url TEXT PRIMARY KEY, etag TEXT, checked_at REAL, body BLOB);
                CREATE TABLE IF NOT EXISTS seen_jobs (url TEXT PRIMARY KEY, first_seen REAL, last_seen REAL);
                CREATE TABLE IF NOT EXISTS blocked_companies (name TEXT PRIMARY KEY);
            """)
            _ready = True
    try:
        with conn:  # commits, or rolls back on error
            yield conn
    finally:
        conn.close()


# --- HTTP cache (bodies are gzipped; some boards are >10 MB of JSON) ---

def cache_get(url: str) -> tuple[str | None, float, bytes | None]:
    with _connect() as conn:
        row = conn.execute("SELECT etag, checked_at, body FROM http_cache WHERE url = ?", (url,)).fetchone()
    if not row:
        return None, 0.0, None
    return row[0], row[1], gzip.decompress(row[2])


def cache_put(url: str, etag: str | None, body: bytes) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO http_cache VALUES (?, ?, ?, ?)",
                     (url, etag, time.time(), gzip.compress(body, compresslevel=5)))


def cache_touch(url: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE http_cache SET checked_at = ? WHERE url = ?", (time.time(), url))


# --- Jobs seen in earlier searches ---

def mark_seen(urls: list[str]) -> set[str]:
    """Record these job URLs as seen and return the ones that had never been seen before."""
    now = time.time()
    with _connect() as conn:
        known = set()
        for i in range(0, len(urls), 500):  # SQLite caps the number of query parameters
            chunk = urls[i:i + 500]
            marks = ",".join("?" * len(chunk))
            known.update(r[0] for r in conn.execute(f"SELECT url FROM seen_jobs WHERE url IN ({marks})", chunk))
        conn.executemany("INSERT OR IGNORE INTO seen_jobs VALUES (?, ?, ?)", [(u, now, now) for u in urls])
        conn.executemany("UPDATE seen_jobs SET last_seen = ? WHERE url = ?", [(now, u) for u in urls])
    return set(urls) - known


# --- Hidden companies ---

def blocked_companies() -> list[str]:
    with _connect() as conn:
        return [r[0] for r in conn.execute("SELECT name FROM blocked_companies ORDER BY name COLLATE NOCASE")]


def block_company(name: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO blocked_companies VALUES (?)", (name.strip(),))


def unblock_company(name: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM blocked_companies WHERE name = ?", (name.strip(),))
