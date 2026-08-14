"""
Short-term TTL cache for raw fetch results (fundamentals dicts).

This is deliberately separate from data/storage.py:
  - cache.py:   "did I already fetch this ticker recently? don't hit the API again"
  - storage.py: "permanently accumulate a record of every run, forever"

Cache entries expire after CACHE_TTL_HOURS (default 24h), controlled via .env.
"""
import json
import sqlite3
import time
from contextlib import contextmanager

from config import CACHE_DB_PATH, CACHE_TTL_HOURS


@contextmanager
def _connect():
    conn = sqlite3.connect(CACHE_DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_cache():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fetch_cache (
                ticker TEXT PRIMARY KEY,
                fetched_at REAL NOT NULL,
                payload TEXT NOT NULL
            )
        """)


def get(ticker: str) -> dict | None:
    """Return the cached payload for a ticker if it's still fresh, else None."""
    init_cache()
    with _connect() as conn:
        row = conn.execute(
            "SELECT fetched_at, payload FROM fetch_cache WHERE ticker = ?",
            (ticker,),
        ).fetchone()

    if row is None:
        return None

    fetched_at, payload = row
    age_hours = (time.time() - fetched_at) / 3600
    if age_hours > CACHE_TTL_HOURS:
        return None

    return json.loads(payload)


def set(ticker: str, payload: dict) -> None:
    init_cache()
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO fetch_cache (ticker, fetched_at, payload)
               VALUES (?, ?, ?)""",
            (ticker, time.time(), json.dumps(payload)),
        )


def clear() -> None:
    init_cache()
    with _connect() as conn:
        conn.execute("DELETE FROM fetch_cache")
