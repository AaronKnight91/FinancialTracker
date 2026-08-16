"""
Builds a universe of LSE-listed company tickers to scan, so --discover
doesn't depend on a hand-maintained watchlist.

Two sources:
  1. Wikipedia's FTSE 100 + FTSE 250 constituent tables (free, no API key,
     ~350 companies). This is the default.
  2. Financial Modeling Prep's stock screener, filtered to the LSE exchange
     (requires FMP_API_KEY). Broader coverage than the FTSE 350, and lets
     us apply some filters (e.g. market cap) server-side before fetching
     anything from yfinance -- worth using if you have a key.

Either way, the result is a plain {ticker: name} dict in the same shape
`config.ALL_INSTRUMENTS` already uses, so it drops straight into the
existing fetch/analysis pipeline.
"""
import io
import json
import time
from pathlib import Path

import pandas as pd
import requests

from config import BASE_DIR, FMP_API_KEY, FMP_BASE_URL

UNIVERSE_CACHE_PATH = BASE_DIR / "data" / "universe_cache.json"
UNIVERSE_CACHE_TTL_DAYS = 30  # constituent lists barely change; no need to re-scrape often

WIKI_FTSE_100_URL = "https://en.wikipedia.org/wiki/FTSE_100_Index"
WIKI_FTSE_250_URL = "https://en.wikipedia.org/wiki/FTSE_250_Index"

# Wikipedia blocks the default pandas/urllib user-agent fairly often
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; lse-analyzer/1.0; personal research use)"}


def to_yahoo_ticker(epic: str) -> str:
    """Normalises an LSE EPIC/ticker code to yfinance's format.
    e.g. 'VOD' -> 'VOD.L', 'BT.A' -> 'BT-A.L', 'SN.' -> 'SN.L'
    This is a heuristic, not a lookup table -- most tickers convert cleanly,
    but a handful of edge cases may not resolve to a valid Yahoo ticker.
    Anything that doesn't will simply show up as a "no data returned"
    ticker later in the pipeline rather than break the run.
    """
    epic = epic.strip().rstrip(".")
    epic = epic.replace(".", "-")
    return f"{epic}.L"


def _read_cache() -> dict | None:
    if not UNIVERSE_CACHE_PATH.exists():
        return None
    try:
        with open(UNIVERSE_CACHE_PATH) as f:
            cached = json.load(f)
        age_days = (time.time() - cached["fetched_at"]) / 86400
        if age_days > UNIVERSE_CACHE_TTL_DAYS:
            return None
        return cached["tickers"]
    except Exception:
        return None


def _write_cache(tickers: dict) -> None:
    UNIVERSE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(UNIVERSE_CACHE_PATH, "w") as f:
        json.dump({"fetched_at": time.time(), "tickers": tickers}, f)


def _scrape_wikipedia_table(url: str) -> dict:
    """Fetches a Wikipedia constituents page and pulls out the table
    containing a 'Ticker' column. Returns {yahoo_ticker: company_name}."""
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()

    tables = pd.read_html(io.StringIO(resp.text))
    ticker_table = None
    for table in tables:
        cols = [str(c).strip() for c in table.columns]
        if "Ticker" in cols and ("Company" in cols or "Company " in cols):
            ticker_table = table
            break

    if ticker_table is None:
        raise ValueError(f"Couldn't find a constituents table with 'Ticker'/'Company' columns at {url}")

    ticker_table.columns = [str(c).strip() for c in ticker_table.columns]
    company_col = "Company" if "Company" in ticker_table.columns else "Company "

    result = {}
    for _, row in ticker_table.iterrows():
        epic = str(row["Ticker"]).strip()
        name = str(row[company_col]).strip()
        if not epic or epic.lower() == "nan":
            continue
        result[to_yahoo_ticker(epic)] = name
    return result


def fetch_ftse_universe(include_250: bool = True, use_cache: bool = True, force_refresh: bool = False) -> dict:
    """FTSE 100 (+ optionally FTSE 250) constituents, scraped from Wikipedia.
    Cached locally for UNIVERSE_CACHE_TTL_DAYS since this barely changes."""
    if use_cache and not force_refresh:
        cached = _read_cache()
        if cached is not None:
            return cached

    universe = {}
    print("Fetching FTSE 100 constituents from Wikipedia...")
    universe.update(_scrape_wikipedia_table(WIKI_FTSE_100_URL))

    if include_250:
        print("Fetching FTSE 250 constituents from Wikipedia...")
        universe.update(_scrape_wikipedia_table(WIKI_FTSE_250_URL))

    if use_cache:
        _write_cache(universe)

    return universe


def fetch_fmp_universe(min_market_cap: float = None, limit: int = None) -> dict:
    """Uses FMP's stock screener to list LSE-listed companies, optionally
    pre-filtered by market cap server-side (cuts down what we need to fetch
    from yfinance afterward). Requires FMP_API_KEY. Returns {} if no key is
    configured or the request fails, so callers can fall back to Wikipedia."""
    if not FMP_API_KEY:
        print("No FMP_API_KEY configured -- skipping FMP universe source.")
        return {}

    params = {
        "exchange": "LSE",
        "isActivelyTrading": "true",
        "apikey": FMP_API_KEY,
    }
    if min_market_cap is not None:
        params["marketCapMoreThan"] = min_market_cap
    if limit is not None:
        params["limit"] = limit

    try:
        resp = requests.get(f"{FMP_BASE_URL}/stock-screener", params=params, timeout=20)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        print(f"  [warn] FMP screener request failed: {e}")
        return {}

    universe = {}
    for row in rows:
        symbol = row.get("symbol")
        name = row.get("companyName")
        if symbol:
            universe[symbol] = name or symbol
    return universe


def get_universe(source: str = "wikipedia", include_250: bool = True, min_market_cap: float = None,
                  limit: int = None, use_cache: bool = True, force_refresh: bool = False) -> dict:
    """Single entry point used by main.py. source: 'wikipedia' or 'fmp'."""
    if source == "fmp":
        universe = fetch_fmp_universe(min_market_cap=min_market_cap, limit=limit)
        if universe:
            return universe
        print("Falling back to the Wikipedia FTSE universe...")

    universe = fetch_ftse_universe(include_250=include_250, use_cache=use_cache, force_refresh=force_refresh)
    if limit is not None:
        universe = dict(list(universe.items())[:limit])
    return universe
