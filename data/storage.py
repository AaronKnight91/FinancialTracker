"""
Long-term storage (separate from the short-term cache in data/cache.py).

Every run appends a fundamentals snapshot dated `run_date`, and upserts
price history. Over months of cron runs on the Pi, this builds an actual
time series you can chart or screen against historically -- not just
"what does today look like".
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from config import HISTORY_DB_PATH


@contextmanager
def _connect():
    Path(HISTORY_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                PRIMARY KEY (ticker, date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                ticker TEXT NOT NULL,
                run_date TEXT NOT NULL,
                name TEXT,
                asset_class TEXT,
                currency TEXT,
                market_cap REAL,
                trailing_pe REAL,
                forward_pe REAL,
                price_to_book REAL,
                dividend_yield REAL,
                fifty_two_week_high REAL,
                fifty_two_week_low REAL,
                beta REAL,
                debt_to_equity REAL,
                return_on_equity REAL,
                profit_margin REAL,
                source TEXT,
                graham_score TEXT,
                passes_graham INTEGER,
                PRIMARY KEY (ticker, run_date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dividends (
                ticker TEXT NOT NULL,
                ex_date TEXT NOT NULL,
                amount REAL,
                source TEXT,
                PRIMARY KEY (ticker, ex_date)
            )
        """)


def save_prices(ticker: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    rows = [
        (
            ticker,
            idx.strftime("%Y-%m-%d"),
            None if pd.isna(row.get("Open")) else float(row["Open"]),
            None if pd.isna(row.get("High")) else float(row["High"]),
            None if pd.isna(row.get("Low")) else float(row["Low"]),
            None if pd.isna(row.get("Close")) else float(row["Close"]),
            None if pd.isna(row.get("Volume")) else int(row["Volume"]),
        )
        for idx, row in df.iterrows()
    ]
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


def save_snapshot(record: dict) -> None:
    with _connect() as conn:
        cols = ", ".join(record.keys())
        placeholders = ", ".join("?" for _ in record)
        conn.execute(
            f"INSERT OR REPLACE INTO snapshots ({cols}) VALUES ({placeholders})",
            list(record.values()),
        )


def save_graham_result(ticker: str, run_date: str, graham_score: str, passes_graham) -> None:
    """Updates the existing snapshot row for (ticker, run_date) with Graham
    screen results, so pass/fail is tracked historically alongside the ratios."""
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE snapshots SET graham_score = ?, passes_graham = ? WHERE ticker = ? AND run_date = ?",
            (graham_score, None if passes_graham is None else int(passes_graham), ticker, run_date),
        )


def save_dividends(ticker: str, records: list) -> None:
    """records: [{date, amount, source}, ...] as returned by data/dividends.py."""
    if not records:
        return
    rows = [(ticker, r["date"], r["amount"], r.get("source")) for r in records]
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO dividends (ticker, ex_date, amount, source) VALUES (?, ?, ?, ?)",
            rows,
        )


def dividend_history(ticker: str) -> pd.DataFrame:
    with _connect() as conn:
        df = pd.read_sql_query(
            "SELECT * FROM dividends WHERE ticker = ? ORDER BY ex_date", conn, params=(ticker,)
        )
    return df


def latest_snapshot() -> pd.DataFrame:
    query = """
        SELECT s.*
        FROM snapshots s
        INNER JOIN (
            SELECT ticker, MAX(run_date) AS max_date
            FROM snapshots GROUP BY ticker
        ) latest ON s.ticker = latest.ticker AND s.run_date = latest.max_date
    """
    with _connect() as conn:
        return pd.read_sql_query(query, conn)


def price_history(ticker: str) -> pd.DataFrame:
    with _connect() as conn:
        df = pd.read_sql_query(
            "SELECT * FROM prices WHERE ticker = ? ORDER BY date", conn, params=(ticker,)
        )
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def all_price_tickers() -> list:
    with _connect() as conn:
        return pd.read_sql_query("SELECT DISTINCT ticker FROM prices", conn)["ticker"].tolist()
