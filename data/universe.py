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

# Wikimedia's bot-detection wants a genuinely descriptive User-Agent (see
# https://meta.wikimedia.org/wiki/User-Agent_policy) -- a browser-spoofing
# string is more likely to get flagged as automated traffic, not less.
_HEADERS = {
    "User-Agent": "lse-analyzer/1.0 (personal finance research script; low request volume) python-requests"
}

# Be gentle: a short pause between the two page fetches, and back off with
# retries if Wikipedia's rate limiter fires (a single 403 doesn't have to
# fail the whole run).
_REQUEST_DELAY_SECONDS = 2
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = [3, 8, 15]


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


def _read_cache(ignore_ttl: bool = False) -> dict | None:
    if not UNIVERSE_CACHE_PATH.exists():
        return None
    try:
        with open(UNIVERSE_CACHE_PATH) as f:
            cached = json.load(f)
        if not ignore_ttl:
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
    containing a 'Ticker' column. Returns {yahoo_ticker: company_name}.
    Retries with backoff on rate-limit/server errors (Wikipedia's bot
    mitigation can reject an occasional request even at low volume)."""
    last_error = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            last_error = e
            status = e.response.status_code if e.response is not None else None
            if status in (403, 429) or (status is not None and status >= 500):
                if attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_BACKOFF_SECONDS[attempt]
                    print(f"  [warn] Wikipedia returned {status} for {url}, retrying in {wait}s "
                          f"(attempt {attempt + 1}/{_MAX_RETRIES})...")
                    time.sleep(wait)
                    continue
            raise
    else:
        raise last_error

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
    Cached locally for UNIVERSE_CACHE_TTL_DAYS since this barely changes.

    Resilient to partial failure: if FTSE 100 succeeds but FTSE 250 doesn't
    (or vice versa), returns what it has rather than losing everything. If
    both fail outright, falls back to a stale cache if one exists, rather
    than leaving --discover with nothing to scan.
    """
    if use_cache and not force_refresh:
        cached = _read_cache()
        if cached is not None:
            return cached

    universe = {}
    errors = []

    print("Fetching FTSE 100 constituents from Wikipedia...")
    try:
        universe.update(_scrape_wikipedia_table(WIKI_FTSE_100_URL))
    except Exception as e:
        errors.append(("FTSE 100", e))
        print(f"  [warn] Couldn't fetch FTSE 100 constituents: {e}")

    if include_250:
        time.sleep(_REQUEST_DELAY_SECONDS)  # a small gap between requests, not a burst
        print("Fetching FTSE 250 constituents from Wikipedia...")
        try:
            universe.update(_scrape_wikipedia_table(WIKI_FTSE_250_URL))
        except Exception as e:
            errors.append(("FTSE 250", e))
            print(f"  [warn] Couldn't fetch FTSE 250 constituents: {e}")

    if not universe:
        # Total failure -- try a stale cache before giving up entirely.
        stale = _read_cache(ignore_ttl=True)
        if stale:
            print("Wikipedia fetch failed completely; using a previously cached ticker "
                  "list instead (may be out of date -- rerun with --discover-refresh-universe "
                  "later to update it).")
            return stale
        raise RuntimeError(
            "Couldn't fetch the FTSE universe from Wikipedia and no cached copy exists. "
            "This is usually Wikipedia's rate limiting, not a code problem -- wait a few "
            "minutes and try again, or in the meantime try "
            "'--discover --discover-ftse100-only' (one page instead of two), or "
            "'--discover --discover-source fmp' if you have an FMP_API_KEY set."
        )

    if errors and use_cache:
        print(f"Note: proceeding with a partial universe ({len(universe)} companies) -- "
              f"{', '.join(name for name, _ in errors)} couldn't be fetched this run.")

    if use_cache and not errors:
        # Only cache a complete result -- caching a partial list would silently
        # shrink future runs until the next forced refresh.
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
