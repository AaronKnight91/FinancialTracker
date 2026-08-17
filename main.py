"""
LSE Analyzer CLI.

Usage:
    python main.py
    python main.py --sort div_yield
    python main.py --watchlist my_list.json
    python main.py --asset-class etf
    python main.py --all
    python main.py --refresh
    python main.py --screen --max-pe 15 --min-div-yield 3
    python main.py --history
    python main.py --graham
    python main.py --asset-class company --graham --graham-only
    python main.py --discover                      # scan the FTSE 100+250 for Graham passers
    python main.py --discover --discover-limit 30   # quick test run on a subset
    python main.py --dividends                      # full dividend payment history + summary columns
"""
import argparse
import json
from datetime import datetime

import pandas as pd
from tabulate import tabulate

from config import OUTPUT_DIR, DEFAULT_WATCHLIST_PATH, ASSET_CLASSES, ALL_INSTRUMENTS
from data.fetchers import get_watchlist_fundamentals
import data.universe as universe
import data.dividends as dividends
import data.storage as storage
from analysis.ratios import build_dataframe, sort_dataframe, format_for_display, screen, with_trailing_returns
from analysis.graham import graham_screen, CRITERIA_LABELS, DEFAULT_MIN_MARKET_CAP


def load_watchlist(path) -> dict:
    """Returns {ticker: name}. Falls back to ticker as its own name if the
    JSON only has a flat list (keeps the original watchlist.json format working)."""
    with open(path) as f:
        data = json.load(f)
    tickers = data["watchlist"]
    return {t: ALL_INSTRUMENTS.get(t, t) for t in tickers}


def parse_args():
    parser = argparse.ArgumentParser(description="Screen LSE-listed companies, ETFs, ETCs, and gilt ETFs on valuation ratios.")
    parser.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST_PATH), help="Path to watchlist JSON file")
    parser.add_argument("--asset-class", choices=list(ASSET_CLASSES.keys()), default=None,
                         help="Use the full built-in list for one asset class instead of the watchlist file")
    parser.add_argument("--all", action="store_true", help="Use every configured instrument across all asset classes")
    parser.add_argument("--sort", default="pe", choices=["pe", "pb", "div_yield", "roe", "market_cap"])
    parser.add_argument("--refresh", action="store_true", help="Ignore cache and force fresh data fetch")
    parser.add_argument("--screen", action="store_true", help="Apply screening filters instead of just listing")
    parser.add_argument("--max-pe", type=float, default=None)
    parser.add_argument("--min-div-yield", type=float, default=None, help="Percent, e.g. 3 for 3%%")
    parser.add_argument("--max-pb", type=float, default=None)
    parser.add_argument("--min-roe", type=float, default=None, help="Percent, e.g. 10 for 10%%")
    parser.add_argument("--history", action="store_true", help="Include trailing 1m/3m/6m/1y price returns")
    parser.add_argument("--graham", action="store_true",
                         help="Evaluate each company against Benjamin Graham's defensive-investor criteria (see README for caveats)")
    parser.add_argument("--graham-only", action="store_true",
                         help="With --graham, show only companies that pass every evaluable Graham criterion")
    parser.add_argument("--graham-min-market-cap", type=float, default=None,
                         help="Override the 'adequate size' market cap floor used by --graham (default £100M)")
    parser.add_argument("--discover", action="store_true",
                         help="Scan the whole FTSE 100+250 universe for Graham passers, instead of a fixed watchlist")
    parser.add_argument("--discover-source", choices=["wikipedia", "fmp"], default="wikipedia",
                         help="Where to source the universe of tickers to scan (fmp requires FMP_API_KEY, falls back to wikipedia)")
    parser.add_argument("--discover-ftse100-only", action="store_true",
                         help="With --discover, scan only the FTSE 100 (faster) instead of FTSE 100+250")
    parser.add_argument("--discover-limit", type=int, default=None,
                         help="With --discover, cap the number of companies scanned (useful for a quick test run)")
    parser.add_argument("--discover-refresh-universe", action="store_true",
                         help="Force re-fetching the ticker universe instead of using the local cache (~30 day TTL)")
    parser.add_argument("--discover-show-all", action="store_true",
                         help="With --discover, show every company scanned, not just those that pass Graham's criteria")
    parser.add_argument("--prefilter-max-pe", type=float, default=20.0,
                         help="With --discover, only run full (slow) Graham checks on companies at or below this P/E first (default 20)")
    parser.add_argument("--prefilter-max-pb", type=float, default=2.5,
                         help="With --discover, only run full (slow) Graham checks on companies at or below this P/B first (default 2.5)")
    parser.add_argument("--dividends", action="store_true",
                         help="Fetch full dividend payment history for each instrument (falls back to FMP "
                              "if yfinance's coverage is thin and FMP_API_KEY is set), add summary columns, "
                              "and export the full payment history to output/")
    parser.add_argument("--no-save", action="store_true", help="Don't write a CSV snapshot to output/")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.discover:
        print(f"Discovering LSE company universe (source={args.discover_source})...")
        tickers = universe.get_universe(
            source=args.discover_source,
            include_250=not args.discover_ftse100_only,
            limit=args.discover_limit,
            force_refresh=args.discover_refresh_universe,
        )
        print(f"Universe: {len(tickers)} companies to scan. This fetches fresh data per "
              f"company, so a full FTSE 350 run can take a while on first run -- results "
              f"are cached for next time.\n")
        args.graham = True  # the whole point of --discover is finding Graham passers
    elif args.all:
        tickers = ALL_INSTRUMENTS
    elif args.asset_class:
        tickers = ASSET_CLASSES[args.asset_class]
    else:
        tickers = load_watchlist(args.watchlist)

    print(f"Fetching fundamentals for {len(tickers)} tickers...")
    records = get_watchlist_fundamentals(tickers, use_cache=not args.refresh)

    df = build_dataframe(records)
    if args.discover:
        # Discovered tickers aren't in config.py's asset-class lists, so tag
        # them explicitly -- we know they're all equities from the source.
        df["asset_class"] = "company"

    if args.history:
        df = with_trailing_returns(df)

    if args.graham:
        non_company = []
        if "asset_class" in df.columns:
            non_company = df.loc[df["asset_class"] != "company", "ticker"].tolist()
        if non_company:
            print(f"Note: Graham criteria only apply to companies -- skipping (N/A): {', '.join(non_company)}\n")

        min_cap = args.graham_min_market_cap if args.graham_min_market_cap is not None else DEFAULT_MIN_MARKET_CAP

        graham_target_df = df
        prefiltered_out = pd.DataFrame()
        if args.discover:
            # Full Graham checks need 3 extra API calls per company (balance sheet,
            # income statement, dividends) -- with hundreds of companies that adds up,
            # so reject the obviously-too-expensive ones first using data we already have.
            pe_ok = df["trailing_pe"].isna() | (df["trailing_pe"] <= args.prefilter_max_pe)
            pb_ok = df["price_to_book"].isna() | (df["price_to_book"] <= args.prefilter_max_pb)
            keep_mask = pe_ok & pb_ok
            graham_target_df = df[keep_mask]
            prefiltered_out = df[~keep_mask].copy()
            print(f"Quick pre-filter (P/E <= {args.prefilter_max_pe}, P/B <= {args.prefilter_max_pb}): "
                  f"{len(graham_target_df)}/{len(df)} companies advance to full Graham checks "
                  f"({len(prefiltered_out)} excluded up front -- widen with --prefilter-max-pe/--prefilter-max-pb "
                  f"if you don't want this shortcut).\n")

        df_screened = graham_screen(graham_target_df, use_cache=not args.refresh, min_market_cap=min_cap)

        if not prefiltered_out.empty:
            prefiltered_out["graham_score"] = "excluded by pre-filter"
            prefiltered_out["passes_graham"] = False
            for col in CRITERIA_LABELS.values():
                prefiltered_out[col] = None
            df = pd.concat([df_screened, prefiltered_out], ignore_index=True, sort=False)
        else:
            df = df_screened

        if args.graham_only or (args.discover and not args.discover_show_all):
            df = df[df["passes_graham"] == True]  # noqa: E712 (pandas boolean mask, not a plain bool compare)
            if df.empty:
                print("No companies passed every evaluable Graham criterion.")
                return

    if args.dividends:
        print(f"Fetching dividend history for {len(df)} tickers "
              f"(falls back to FMP if yfinance's coverage looks thin and FMP_API_KEY is set)...")
        storage.init_db()

        all_payments = []
        years_col, ttm_col, last_date_col, last_amount_col = [], [], [], []
        for _, row in df.iterrows():
            ticker = row["ticker"]
            records = dividends.get_dividend_history(ticker, use_cache=not args.refresh)
            storage.save_dividends(ticker, records)
            all_payments.extend({"ticker": ticker, **r} for r in records)

            summary = dividends.summarize(records)
            years_col.append(summary["dividend_years_count"])
            ttm_col.append(summary["ttm_dividend"])
            last_date_col.append(summary["last_dividend_date"])
            last_amount_col.append(summary["last_dividend_amount"])

        df["dividend_years_count"] = years_col
        df["ttm_dividend"] = ttm_col
        df["last_dividend_date"] = last_date_col
        df["last_dividend_amount"] = last_amount_col

        if all_payments and not args.no_save:
            payments_df = pd.DataFrame(all_payments)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            div_path = OUTPUT_DIR / f"dividend_history_{timestamp}.csv"
            payments_df.to_csv(div_path, index=False)
            print(f"Saved full dividend payment history ({len(payments_df)} payments across "
                  f"{df['ticker'].nunique()} tickers) to {div_path}\n")
        elif not all_payments:
            print("No dividend history found for any ticker in this run.\n")

    if args.screen:
        df = screen(
            df,
            max_pe=args.max_pe,
            min_dividend_yield=args.min_div_yield,
            max_pb=args.max_pb,
            min_roe=args.min_roe,
        )
        if df.empty:
            print("No instruments matched the screening criteria.")
            return

    df = sort_dataframe(df, sort_by=args.sort)

    missing = df[df["name"].isna()]["ticker"].tolist() if "name" in df.columns else []
    if missing:
        print(f"Warning: no data returned for: {', '.join(missing)} (check ticker or data source availability)\n")

    display_df = format_for_display(df)
    if args.history:
        return_cols = [c for c in df.columns if c.startswith("return_")]
        if return_cols:
            display_df[return_cols] = df[return_cols]

    if args.graham:
        graham_cols = ["graham_score", "passes_graham"] + list(CRITERIA_LABELS.values())
        graham_cols = [c for c in graham_cols if c in df.columns]
        for c in graham_cols:
            display_df[c] = df[c]

    if args.dividends:
        div_display = {
            "dividend_years_count": "Div Yrs", "ttm_dividend": "TTM Div",
            "last_dividend_date": "Last Div Date", "last_dividend_amount": "Last Div Amt",
        }
        for col, label in div_display.items():
            if col in df.columns:
                display_df[label] = df[col]

    print(tabulate(display_df, headers="keys", tablefmt="simple", showindex=False))

    if args.graham:
        print(
            "\nGraham criteria are approximated from free data (typically ~4 years of "
            "financial history vs. Graham's original 10-20 year windows). 'None'/blank "
            "means a criterion couldn't be evaluated and isn't counted for or against the "
            "company. See README.md for exactly how each criterion is computed."
        )

    if not args.no_save:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "graham_discovery" if args.discover else "lse_snapshot"
        out_path = OUTPUT_DIR / f"{prefix}_{timestamp}.csv"
        df.to_csv(out_path, index=False)
        print(f"\nSaved full snapshot to {out_path}")


if __name__ == "__main__":
    main()
