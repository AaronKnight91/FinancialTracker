"""
Full dividend payment history, with a fallback source when yfinance's
coverage is too thin to be useful.

yfinance's `Ticker.dividends` is the primary source (free, no key), but for
some LSE-listed instruments it only covers a handful of years, or comes
back empty even for companies with a long real payment record. If
FMP_API_KEY is set and yfinance's history looks short, this also tries
Financial Modeling Prep's historical dividend endpoint and keeps whichever
source actually covers more years.

Used in two places:
  - main.py's --dividends flag, which persists full payment history and
    shows summary columns (years of history, trailing-12-month total, most
    recent payment) in the comparison table.
  - analysis/graham.py's "dividend record" criterion, which benefits from
    a longer history the same way -- closer to Graham's original 20-year
    window than yfinance alone usually provides.
"""
import datetime as dt

import requests
import yfinance as yf

from config import FMP_API_KEY, FMP_BASE_URL
import data.cache as cache

MIN_YEARS_BEFORE_FALLBACK = 5  # if yfinance covers fewer distinct years than this, also try FMP


def _distinct_years(records: list) -> int:
    return len({r["date"][:4] for r in records})


def fetch_dividends_yfinance(ticker: str) -> list:
    """Returns [{date, amount, source}, ...], oldest first, as reported by yfinance."""
    t = yf.Ticker(ticker)
    try:
        series = t.dividends
    except Exception as e:
        print(f"  [warn] yfinance dividend history unavailable for {ticker}: {e}")
        return []

    if series is None or series.empty:
        return []

    return [
        {"date": idx.strftime("%Y-%m-%d"), "amount": float(value), "source": "yfinance"}
        for idx, value in series.items()
    ]


def fetch_dividends_fmp(ticker: str) -> list:
    """Returns [{date, amount, source}, ...] from FMP's historical dividend
    endpoint. Returns [] if no API key is configured or the request fails."""
    if not FMP_API_KEY:
        return []

    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/historical-price-full/stock_dividend/{ticker}",
            params={"apikey": FMP_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"  [warn] FMP dividend history request failed for {ticker}: {e}")
        return []

    rows = payload.get("historical", []) if isinstance(payload, dict) else []
    records = []
    for row in rows:
        date = row.get("date")
        amount = row.get("adjDividend") if row.get("adjDividend") is not None else row.get("dividend")
        if date and amount is not None:
            records.append({"date": date, "amount": float(amount), "source": "fmp"})
    return sorted(records, key=lambda r: r["date"])


def get_dividend_history(ticker: str, use_cache: bool = True, min_years: int = MIN_YEARS_BEFORE_FALLBACK) -> list:
    """Returns the richest available [{date, amount, source}, ...] history for a ticker."""
    cache_key = f"dividends:{ticker}"
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    records = fetch_dividends_yfinance(ticker)

    if _distinct_years(records) < min_years and FMP_API_KEY:
        fmp_records = fetch_dividends_fmp(ticker)
        if _distinct_years(fmp_records) > _distinct_years(records):
            records = fmp_records

    if use_cache:
        cache.set(cache_key, records)

    return records


def summarize(records: list) -> dict:
    """Small summary for the main comparison table: years of history covered,
    trailing-12-month total, and the most recent payment."""
    if not records:
        return {
            "dividend_years_count": 0,
            "ttm_dividend": None,
            "last_dividend_date": None,
            "last_dividend_amount": None,
        }

    one_year_ago = dt.date.today() - dt.timedelta(days=365)
    ttm_total = sum(
        r["amount"] for r in records
        if dt.date.fromisoformat(r["date"]) >= one_year_ago
    )
    latest = max(records, key=lambda r: r["date"])

    return {
        "dividend_years_count": _distinct_years(records),
        "ttm_dividend": round(ttm_total, 4) if ttm_total else None,
        "last_dividend_date": latest["date"],
        "last_dividend_amount": latest["amount"],
    }
