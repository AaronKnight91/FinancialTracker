"""
Turns raw fundamentals records into a DataFrame, adds derived columns,
and provides sorting/screening/display helpers used by main.py.
"""
import pandas as pd

import data.storage as storage

SORT_COLUMNS = {
    "pe": "trailing_pe",
    "pb": "price_to_book",
    "div_yield": "dividend_yield",
    "roe": "return_on_equity",
    "market_cap": "market_cap",
}

# Ascending makes sense for "cheapness" metrics; descending for "bigger is better" ones.
SORT_ASCENDING = {
    "pe": True,
    "pb": True,
    "div_yield": False,
    "roe": False,
    "market_cap": False,
}


def build_dataframe(records: list) -> pd.DataFrame:
    df = pd.DataFrame(records)
    # dividend_yield and return_on_equity come back from yfinance as fractions (0.03 = 3%)
    for pct_col in ["dividend_yield", "return_on_equity", "profit_margin"]:
        if pct_col in df.columns:
            df[pct_col + "_pct"] = df[pct_col] * 100
    return df


def sort_dataframe(df: pd.DataFrame, sort_by: str = "pe") -> pd.DataFrame:
    col = SORT_COLUMNS.get(sort_by, sort_by)
    if col not in df.columns:
        return df
    ascending = SORT_ASCENDING.get(sort_by, True)
    return df.sort_values(col, ascending=ascending, na_position="last").reset_index(drop=True)


def screen(df: pd.DataFrame, max_pe=None, min_dividend_yield=None, max_pb=None, min_roe=None) -> pd.DataFrame:
    """Filters expressed in human units: dividend yield / ROE as percent, e.g. 3 for 3%."""
    out = df.copy()
    if max_pe is not None and "trailing_pe" in out.columns:
        out = out[out["trailing_pe"].notna() & (out["trailing_pe"] <= max_pe)]
    if max_pb is not None and "price_to_book" in out.columns:
        out = out[out["price_to_book"].notna() & (out["price_to_book"] <= max_pb)]
    if min_dividend_yield is not None and "dividend_yield" in out.columns:
        out = out[out["dividend_yield"].notna() & (out["dividend_yield"] * 100 >= min_dividend_yield)]
    if min_roe is not None and "return_on_equity" in out.columns:
        out = out[out["return_on_equity"].notna() & (out["return_on_equity"] * 100 >= min_roe)]
    return out.reset_index(drop=True)


def format_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Human-friendly subset/formatting for console output (tabulate)."""
    cols_order = [
        "ticker", "name", "asset_class", "trailing_pe", "forward_pe",
        "price_to_book", "dividend_yield_pct", "return_on_equity_pct",
        "debt_to_equity", "market_cap", "source",
    ]
    cols = [c for c in cols_order if c in df.columns]
    display_df = df[cols].copy()

    rename = {
        "trailing_pe": "P/E", "forward_pe": "Fwd P/E", "price_to_book": "P/B",
        "dividend_yield_pct": "Div Yield %", "return_on_equity_pct": "ROE %",
        "debt_to_equity": "D/E", "market_cap": "Market Cap", "asset_class": "Class",
    }
    display_df = display_df.rename(columns=rename)

    for col in ["P/E", "Fwd P/E", "P/B", "Div Yield %", "ROE %", "D/E"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].round(2)
    if "Market Cap" in display_df.columns:
        display_df["Market Cap"] = display_df["Market Cap"].apply(
            lambda x: f"£{x/1e9:.1f}B" if pd.notna(x) and x >= 1e9
            else (f"£{x/1e6:.0f}M" if pd.notna(x) else None)
        )
    return display_df


# --- Trailing returns, computed from the long-term price history store ---

RETURN_WINDOWS = {"1m": 21, "3m": 63, "6m": 126, "1y": 252}  # approx trading days


def trailing_returns(tickers: list = None) -> pd.DataFrame:
    tickers = tickers or storage.all_price_tickers()
    rows = []
    for ticker in tickers:
        hist = storage.price_history(ticker)["close"].dropna()
        if hist.empty:
            continue
        row = {"ticker": ticker}
        latest_price = hist.iloc[-1]
        for label, days in RETURN_WINDOWS.items():
            if len(hist) > days:
                past_price = hist.iloc[-days - 1]
                row[f"return_{label}"] = round((latest_price / past_price - 1) * 100, 2)
            else:
                row[f"return_{label}"] = None
        rows.append(row)
    return pd.DataFrame(rows)


def with_trailing_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Left-merge trailing returns onto a fundamentals dataframe by ticker."""
    returns = trailing_returns(df["ticker"].tolist() if "ticker" in df.columns else None)
    if returns.empty:
        return df
    return df.merge(returns, on="ticker", how="left")
