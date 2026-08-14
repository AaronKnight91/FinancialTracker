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
"""
import argparse
import json
from datetime import datetime

from tabulate import tabulate

from config import OUTPUT_DIR, DEFAULT_WATCHLIST_PATH, ASSET_CLASSES, ALL_INSTRUMENTS
from data.fetchers import get_watchlist_fundamentals
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
    parser.add_argument("--no-save", action="store_true", help="Don't write a CSV snapshot to output/")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.all:
        tickers = ALL_INSTRUMENTS
    elif args.asset_class:
        tickers = ASSET_CLASSES[args.asset_class]
    else:
        tickers = load_watchlist(args.watchlist)

    print(f"Fetching fundamentals for {len(tickers)} tickers...")
    records = get_watchlist_fundamentals(tickers, use_cache=not args.refresh)

    df = build_dataframe(records)

    if args.history:
        df = with_trailing_returns(df)

    if args.graham:
        non_company = []
        if "asset_class" in df.columns:
            non_company = df.loc[df["asset_class"] != "company", "ticker"].tolist()
        if non_company:
            print(f"Note: Graham criteria only apply to companies -- skipping (N/A): {', '.join(non_company)}\n")

        min_cap = args.graham_min_market_cap if args.graham_min_market_cap is not None else DEFAULT_MIN_MARKET_CAP
        df = graham_screen(df, use_cache=not args.refresh, min_market_cap=min_cap)

        if args.graham_only:
            df = df[df["passes_graham"] == True]  # noqa: E712 (pandas boolean mask, not a plain bool compare)
            if df.empty:
                print("No companies passed every evaluable Graham criterion.")
                return

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
        out_path = OUTPUT_DIR / f"lse_snapshot_{timestamp}.csv"
        df.to_csv(out_path, index=False)
        print(f"\nSaved full snapshot to {out_path}")


if __name__ == "__main__":
    main()
