"""
Data fetching: yfinance is the primary source (free, no key, decent LSE
price coverage). Financial Modeling Prep (FMP) is an optional fallback --
UK fundamentals on Yahoo are sometimes missing or stale, so any field that
comes back empty gets a second attempt from FMP if an API key is configured.

Results are TTL-cached (data/cache.py) so repeated runs within the cache
window don't re-hit either API, and are always persisted to the long-term
history store (data/storage.py) regardless of cache hits.
"""
import time

import requests
import yfinance as yf

from config import FMP_API_KEY, FMP_BASE_URL, PRICE_HISTORY_PERIOD, asset_class_for
import data.cache as cache
import data.storage as storage

# Fields we care about, and how to read them out of each source's response shape.
FUNDAMENTAL_FIELDS = [
    "currency", "market_cap", "trailing_pe", "forward_pe", "price_to_book",
    "dividend_yield", "fifty_two_week_high", "fifty_two_week_low", "beta",
    "debt_to_equity", "return_on_equity", "profit_margin",
]


def _fetch_yfinance(ticker: str) -> tuple[dict, "pd.DataFrame"]:
    t = yf.Ticker(ticker)

    hist = t.history(period=PRICE_HISTORY_PERIOD, auto_adjust=False)

    info = {}
    try:
        info = t.info or {}
    except Exception as e:
        print(f"  [warn] yfinance .info failed for {ticker}: {e}")

    fundamentals = {
        "currency": info.get("currency"),
        "market_cap": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "price_to_book": info.get("priceToBook"),
        "dividend_yield": info.get("dividendYield"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        "beta": info.get("beta"),
        "debt_to_equity": info.get("debtToEquity"),
        "return_on_equity": info.get("returnOnEquity"),
        "profit_margin": info.get("profitMargins"),
    }
    return fundamentals, hist


def _fetch_fmp_fallback(ticker: str, missing_fields: list) -> dict:
    """Only called for fields yfinance left empty, and only if FMP_API_KEY is set."""
    if not FMP_API_KEY or not missing_fields:
        return {}

    # FMP uses plain symbols for LSE tickers too (e.g. VOD.L works directly).
    filled = {}
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/quote/{ticker}",
            params={"apikey": FMP_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return {}
        row = data[0]

        fmp_map = {
            "market_cap": "marketCap",
            "trailing_pe": "pe",
            "fifty_two_week_high": "yearHigh",
            "fifty_two_week_low": "yearLow",
        }
        for field in missing_fields:
            fmp_key = fmp_map.get(field)
            if fmp_key and row.get(fmp_key) is not None:
                filled[field] = row[fmp_key]
    except Exception as e:
        print(f"  [warn] FMP fallback failed for {ticker}: {e}")

    return filled


def fetch_ticker(ticker: str, name: str, run_date: str, use_cache: bool = True, pause: float = 0.5) -> dict:
    """Fetch fundamentals (+ store price history), preferring cache, then yfinance, then FMP for gaps."""
    cached = cache.get(ticker) if use_cache else None
    if cached is not None:
        record = {**cached, "ticker": ticker, "run_date": run_date, "name": name}
        storage.save_snapshot({**record, "asset_class": asset_class_for(ticker)})
        return record

    fundamentals, hist = _fetch_yfinance(ticker)
    storage.save_prices(ticker, hist)

    missing = [f for f in FUNDAMENTAL_FIELDS if fundamentals.get(f) is None]
    if missing:
        fundamentals.update(_fetch_fmp_fallback(ticker, missing))

    fundamentals["source"] = "yfinance+fmp" if missing and FMP_API_KEY else "yfinance"
    cache.set(ticker, fundamentals)

    record = {**fundamentals, "ticker": ticker, "run_date": run_date, "name": name,
              "asset_class": asset_class_for(ticker)}
    storage.save_snapshot(record)

    time.sleep(pause)  # be polite to the APIs
    return record


def get_watchlist_fundamentals(tickers: dict, use_cache: bool = True) -> list:
    """tickers: {ticker: name}. Returns a list of fundamentals dicts, one per ticker."""
    import datetime as dt
    run_date = dt.date.today().isoformat()

    storage.init_db()

    records = []
    for ticker, name in tickers.items():
        print(f"Fetching {ticker} ({name})...")
        try:
            records.append(fetch_ticker(ticker, name, run_date, use_cache=use_cache))
        except Exception as e:
            print(f"  [error] failed on {ticker}: {e}")
            records.append({"ticker": ticker, "name": None})  # so it shows up as "missing"
    return records
