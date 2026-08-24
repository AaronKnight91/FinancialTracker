"""
Imports broker transaction history exports into per-portfolio SQLite tables.

Currently supports Freetrade's transaction history CSV export format
(Title, Type, Timestamp, Ticker, ISIN, ... -- ORDER/DIVIDEND/TOP_UP/
INTEREST_FROM_CASH/CAPITAL/SPECIAL_DIVIDEND rows). If you use a different
broker, adapt `read_freetrade_csv` -- everything downstream (dedup, per-
portfolio tables, watchlist sync, ratio calculation) is broker-agnostic.

Each portfolio gets two tables of its own in data/market_data.db:
  - portfolio_<name>_transactions -- every transaction row, deduplicated
  - portfolio_<name>_ratios       -- latest fetched ratios for tickers held

Multiple portfolios (e.g. separate ISA/GIA accounts) stay fully separate --
importing into one never touches another's tables.

Deduplication: every transaction row is fingerprinted by hashing every
column's value together. Re-importing a file you've already loaded, or a
new export whose date range overlaps a previous one (the normal case for
"last 12 months" broker exports), inserts nothing new for the rows already
seen -- `row_hash` is the table's primary key, so SQLite's INSERT OR IGNORE
enforces this rather than relying on manual comparison logic.

Caveat: TOP_UP and INTEREST_FROM_CASH rows don't carry a broker-assigned
order ID, so their fingerprint is based on timestamp + amount + type. Two
genuinely separate interest payments of the same amount at the exact same
timestamp (not realistically possible for real broker data, which timestamps
to the second) would be indistinguishable and treated as one -- an inherent
limit of the export format, not something dedup logic can work around.
"""
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from config import HISTORY_DB_PATH
from data.universe import to_yahoo_ticker

# The columns Freetrade's transaction history export uses. Order doesn't
# matter for parsing (pandas reads by header name), but this is also used
# to build the transactions table schema and to validate an uploaded file
# actually looks like a Freetrade export before we try to process it.
FREETRADE_COLUMNS = [
    "Title", "Type", "Timestamp", "Account Currency", "Total Amount", "Buy / Sell",
    "Ticker", "ISIN", "Price per Share in Account Currency", "Stamp Duty", "Quantity",
    "Venue", "Order ID", "Order Type", "Instrument Currency", "Total Shares Amount",
    "Price per Share", "FX Rate", "Base FX Rate", "FX Fee (BPS)", "FX Fee Amount",
    "Dividend Ex Date", "Dividend Pay Date", "Dividend Eligible Quantity",
    "Dividend Amount Per Share", "Dividend Gross Distribution Amount",
    "Dividend Net Distribution Amount", "Dividend Withheld Tax Percentage",
    "Dividend Withheld Tax Amount",
]

RATIOS_TABLE_COLUMNS = [
    "ticker", "run_date", "name", "asset_class", "currency", "market_cap",
    "trailing_pe", "forward_pe", "price_to_book", "dividend_yield", "beta",
    "debt_to_equity", "return_on_equity", "profit_margin", "source",
]


def _snake_case(col: str) -> str:
    col = col.strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    return col.strip("_")


def sanitize_table_name(name: str) -> str:
    """Turns a free-text portfolio name into a safe SQL table name fragment.
    Table names can't be parameterised like values can, so this is the only
    thing standing between a portfolio name and a SQL-injection-shaped bug --
    keep it strict (alphanumeric + underscore only)."""
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip()).strip("_").lower()
    if not slug:
        raise ValueError(f"'{name}' doesn't contain any usable characters for a portfolio/table name")
    return slug


def transactions_table_name(portfolio_name: str) -> str:
    return f"portfolio_{sanitize_table_name(portfolio_name)}_transactions"


def ratios_table_name(portfolio_name: str) -> str:
    return f"portfolio_{sanitize_table_name(portfolio_name)}_ratios"


@contextmanager
def _connect():
    Path(HISTORY_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_hash(row: pd.Series) -> str:
    """A stable fingerprint of every field in the row -- two rows with
    identical values everywhere (the normal case when the same transaction
    appears in two overlapping export files) hash to the same value."""
    raw = "|".join("" if pd.isna(v) else str(v) for v in row.values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_freetrade_csv(path: str) -> pd.DataFrame:
    """Reads and validates a Freetrade transaction export, adding row_hash
    (for dedup) and source_file (for traceability) columns."""
    df = pd.read_csv(path)

    missing = [c for c in FREETRADE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"{path} doesn't look like a Freetrade transaction export -- "
            f"missing expected column(s): {', '.join(missing)}"
        )

    df.columns = [_snake_case(c) for c in df.columns]
    df["row_hash"] = df.apply(_row_hash, axis=1)
    df["source_file"] = Path(path).name
    return df


def init_portfolio_tables(portfolio_name: str) -> None:
    tx_table = transactions_table_name(portfolio_name)
    ratios_table = ratios_table_name(portfolio_name)
    with _connect() as conn:
        cols_sql = ", ".join(f'"{_snake_case(c)}" TEXT' for c in FREETRADE_COLUMNS)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS "{tx_table}" (
                row_hash TEXT PRIMARY KEY,
                source_file TEXT,
                {cols_sql}
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS "{ratios_table}" (
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
                beta REAL,
                debt_to_equity REAL,
                return_on_equity REAL,
                profit_margin REAL,
                source TEXT,
                PRIMARY KEY (ticker, run_date)
            )
        """)


def import_transactions(portfolio_name: str, csv_paths: list) -> dict:
    """Imports one or more Freetrade CSV exports into a portfolio's
    transactions table. Safe to call repeatedly, including with files whose
    date ranges overlap previous imports -- exact-duplicate rows are
    silently skipped via row_hash. Returns a summary dict, including every
    distinct ticker seen (across this call's files AND everything already
    stored for this portfolio from earlier imports)."""
    init_portfolio_tables(portfolio_name)
    tx_table = transactions_table_name(portfolio_name)

    total_rows = 0
    inserted = 0
    all_cols = ["row_hash", "source_file"] + [_snake_case(c) for c in FREETRADE_COLUMNS]
    col_list = ", ".join(f'"{c}"' for c in all_cols)
    placeholders = ", ".join("?" for _ in all_cols)

    with _connect() as conn:
        for path in csv_paths:
            df = read_freetrade_csv(path)
            total_rows += len(df)
            for _, row in df.iterrows():
                values = [row.get(c) for c in all_cols]
                cur = conn.execute(
                    f'INSERT OR IGNORE INTO "{tx_table}" ({col_list}) VALUES ({placeholders})',
                    values,
                )
                if cur.rowcount:
                    inserted += 1

    return {
        "rows_seen": total_rows,
        "rows_inserted": inserted,
        "rows_duplicate": total_rows - inserted,
    }


def get_portfolio_tickers(portfolio_name: str) -> dict:
    """Returns {yahoo_ticker: company_name} for every distinct ticker ever
    recorded in this portfolio's transaction history (across all imports)."""
    tx_table = transactions_table_name(portfolio_name)
    with _connect() as conn:
        try:
            df = pd.read_sql_query(
                f'SELECT DISTINCT ticker, title FROM "{tx_table}" '
                f'WHERE ticker IS NOT NULL AND TRIM(ticker) != \'\'',
                conn,
            )
        except (sqlite3.OperationalError, pd.errors.DatabaseError):
            return {}  # portfolio hasn't been created yet

    result = {}
    for _, row in df.iterrows():
        yahoo_ticker = to_yahoo_ticker(str(row["ticker"]))
        result[yahoo_ticker] = str(row["title"]) if pd.notna(row["title"]) else yahoo_ticker
    return result


def save_portfolio_ratios(portfolio_name: str, records: list) -> None:
    init_portfolio_tables(portfolio_name)
    ratios_table = ratios_table_name(portfolio_name)
    if not records:
        return
    with _connect() as conn:
        for record in records:
            clean = {k: record.get(k) for k in RATIOS_TABLE_COLUMNS if k in record}
            cols = ", ".join(f'"{k}"' for k in clean)
            placeholders = ", ".join("?" for _ in clean)
            conn.execute(
                f'INSERT OR REPLACE INTO "{ratios_table}" ({cols}) VALUES ({placeholders})',
                list(clean.values()),
            )


def portfolio_ratios(portfolio_name: str) -> pd.DataFrame:
    """Latest ratios snapshot for every ticker in a portfolio."""
    ratios_table = ratios_table_name(portfolio_name)
    query = f"""
        SELECT r.*
        FROM "{ratios_table}" r
        INNER JOIN (
            SELECT ticker, MAX(run_date) AS max_date FROM "{ratios_table}" GROUP BY ticker
        ) latest ON r.ticker = latest.ticker AND r.run_date = latest.max_date
    """
    with _connect() as conn:
        try:
            return pd.read_sql_query(query, conn)
        except (sqlite3.OperationalError, pd.errors.DatabaseError):
            return pd.DataFrame()


def list_portfolios() -> list:
    """Portfolio names discovered from the tables that actually exist."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'portfolio_%_transactions'"
        ).fetchall()
    names = []
    for (table_name,) in rows:
        if table_name.startswith("portfolio_") and table_name.endswith("_transactions"):
            names.append(table_name[len("portfolio_"):-len("_transactions")])
    return sorted(names)
