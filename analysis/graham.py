"""
Benjamin Graham's "defensive investor" criteria, from The Intelligent
Investor (ch. 14), adapted to what's actually available for free via
yfinance for LSE-listed companies.

Graham's 7 original criteria, and how each is approximated here:

1. Adequate size of enterprise
   Graham used a minimum sales figure (in 1970s dollars). Here: market cap
   above a configurable floor (default £100M) -- a rough modern proxy, not
   a like-for-like conversion.

2. Sufficiently strong financial condition
   Current ratio >= 2, AND long-term debt <= working capital. Pulled from
   the latest available balance sheet.

3. Earnings stability
   Graham wanted positive earnings in every one of the last 10 years.
   yfinance's free annual statements typically only go back ~4 years, so
   this checks positive net income in every year yfinance provides --
   a shorter window than Graham intended.

4. Dividend record
   Graham wanted an uninterrupted dividend record for 20 years. Checked
   here over whatever span of dividend history yfinance returns -- again,
   usually much shorter than 20 years.

5. Earnings growth
   Graham wanted a minimum 33% increase in EPS over 10 years, comparing
   3-year averages at each end. Here it's simplified to: growth from the
   earliest to the latest available annual EPS (or net income, if EPS
   isn't reported) >= 33%, over whatever period is available.

6. Moderate P/E ratio
   Trailing P/E <= 15 (Graham's classic threshold).

7. Moderate price-to-assets
   Price-to-book <= 1.5. Also checked as Graham's combined shortcut:
   P/E x P/B <= 22.5 ("the Graham Number" test).

IMPORTANT: because free data sources give a shorter history than Graham's
original test calls for, this is a directional screen, not a faithful
reproduction of Graham's method. Criteria that can't be evaluated from
available data are marked None ("N/A") rather than counted as a pass or
fail, and are excluded from the pass/fail scoring.
"""
import time

import pandas as pd
import yfinance as yf

import data.cache as cache
import data.storage as storage
import data.dividends as dividends

DEFAULT_MIN_MARKET_CAP = 100_000_000  # adapted "adequate size" floor, see module docstring
MIN_CURRENT_RATIO = 2.0
MIN_EPS_GROWTH_PCT = 33.0
MAX_PE = 15.0
MAX_PB = 1.5
MAX_GRAHAM_NUMBER = 22.5

MIN_CRITERIA_FOR_VERDICT = 4  # need at least this many evaluable criteria to render a pass/fail verdict

CRITERIA_LABELS = {
    "adequate_size": "G:Size",
    "strong_financial_condition": "G:FinCond",
    "earnings_stability": "G:EarnStable",
    "dividend_record": "G:DivRecord",
    "earnings_growth": "G:EarnGrowth",
    "moderate_pe": "G:PE<=15",
    "moderate_pb": "G:PB<=1.5",
    "graham_number": "G:PExPB<=22.5",
}


def _first_matching_row(df, candidates):
    """yfinance's statement row names vary by company and library version --
    try a few known variants and return the first that exists."""
    if df is None or df.empty:
        return None
    for name in candidates:
        if name in df.index:
            return df.loc[name]
    return None


def fetch_graham_raw(ticker: str) -> dict:
    """Pulls balance sheet / income statement / dividend history and reduces
    them to plain JSON-serialisable numbers, so this can go through the
    same cache as everything else. Best-effort: missing statements are
    normal for smaller/newer listings, not an error."""
    t = yf.Ticker(ticker)
    raw = {
        "current_ratio": None,
        "long_term_debt": None,
        "working_capital": None,
        "net_income_by_year": {},
        "eps_by_year": {},
        "dividend_years": [],
    }

    try:
        bs = t.balance_sheet
        ca = _first_matching_row(bs, ["Current Assets", "Total Current Assets"])
        cl = _first_matching_row(bs, ["Current Liabilities", "Total Current Liabilities"])
        ltd = _first_matching_row(bs, [
            "Long Term Debt", "Long Term Debt And Capital Lease Obligation",
        ])
        if ca is not None and cl is not None and not ca.empty and not cl.empty:
            ca_val, cl_val = float(ca.iloc[0]), float(cl.iloc[0])
            if cl_val:
                raw["current_ratio"] = ca_val / cl_val
            raw["working_capital"] = ca_val - cl_val
        if ltd is not None and not ltd.empty:
            raw["long_term_debt"] = float(ltd.iloc[0])
    except Exception as e:
        print(f"  [warn] Graham balance sheet data unavailable for {ticker}: {e}")

    try:
        fin = t.financials
        net_income = _first_matching_row(fin, ["Net Income", "Net Income Common Stockholders"])
        if net_income is not None:
            for date, value in net_income.dropna().items():
                raw["net_income_by_year"][str(date.year)] = float(value)
        eps = _first_matching_row(fin, ["Basic EPS", "Diluted EPS"])
        if eps is not None:
            for date, value in eps.dropna().items():
                raw["eps_by_year"][str(date.year)] = float(value)
    except Exception as e:
        print(f"  [warn] Graham income statement data unavailable for {ticker}: {e}")

    try:
        div_records = dividends.get_dividend_history(ticker)
        raw["dividend_years"] = sorted({int(r["date"][:4]) for r in div_records})
    except Exception as e:
        print(f"  [warn] Graham dividend history unavailable for {ticker}: {e}")

    return raw


def evaluate_graham(fundamentals: dict, raw: dict, min_market_cap: float = DEFAULT_MIN_MARKET_CAP) -> dict:
    """Returns per-criterion True/False/None plus an overall score and verdict.
    None means "couldn't be evaluated from available data" and is excluded
    from scoring -- it is never treated as a pass."""
    criteria = {}

    market_cap = fundamentals.get("market_cap")
    criteria["adequate_size"] = None if pd.isna(market_cap) else market_cap >= min_market_cap

    current_ratio = raw.get("current_ratio")
    working_capital = raw.get("working_capital")
    long_term_debt = raw.get("long_term_debt")
    if current_ratio is None:
        criteria["strong_financial_condition"] = None
    else:
        ok = current_ratio >= MIN_CURRENT_RATIO
        if long_term_debt is not None and working_capital is not None:
            ok = ok and (long_term_debt <= working_capital)
        criteria["strong_financial_condition"] = ok

    net_income_by_year = raw.get("net_income_by_year") or {}
    criteria["earnings_stability"] = None if not net_income_by_year else all(
        v > 0 for v in net_income_by_year.values()
    )

    dividend_years = raw.get("dividend_years") or []
    if not dividend_years:
        criteria["dividend_record"] = False  # no dividend history at all = fails this one outright
    else:
        span = max(dividend_years) - min(dividend_years) + 1
        criteria["dividend_record"] = len(dividend_years) >= span

    eps_by_year = raw.get("eps_by_year") or {}
    growth_source = eps_by_year if len(eps_by_year) >= 2 else net_income_by_year
    if len(growth_source) < 2:
        criteria["earnings_growth"] = None
    else:
        years_sorted = sorted(growth_source.keys())
        earliest, latest = growth_source[years_sorted[0]], growth_source[years_sorted[-1]]
        if earliest:
            growth_pct = (latest - earliest) / abs(earliest) * 100
            criteria["earnings_growth"] = growth_pct >= MIN_EPS_GROWTH_PCT
        else:
            criteria["earnings_growth"] = None

    pe = fundamentals.get("trailing_pe")
    criteria["moderate_pe"] = None if pd.isna(pe) else (0 < pe <= MAX_PE)

    pb = fundamentals.get("price_to_book")
    criteria["moderate_pb"] = None if pd.isna(pb) else (0 < pb <= MAX_PB)

    if pd.notna(pe) and pd.notna(pb) and pe > 0 and pb > 0:
        criteria["graham_number"] = (pe * pb) <= MAX_GRAHAM_NUMBER
    else:
        criteria["graham_number"] = None

    evaluated = {k: v for k, v in criteria.items() if v is not None}
    passed = sum(1 for v in evaluated.values() if v)
    total_evaluated = len(evaluated)

    if total_evaluated < MIN_CRITERIA_FOR_VERDICT:
        overall = None  # too little data to render a fair verdict either way
    else:
        overall = (passed == total_evaluated)

    return {
        "criteria": criteria,
        "criteria_passed": passed,
        "criteria_evaluated": total_evaluated,
        "passes_graham": overall,
    }


def graham_screen(df: pd.DataFrame, use_cache: bool = True, min_market_cap: float = DEFAULT_MIN_MARKET_CAP,
                   persist: bool = True) -> pd.DataFrame:
    """Adds Graham criteria columns to a fundamentals dataframe. Only
    evaluated for asset_class == 'company' -- the criteria don't apply to
    ETFs/ETCs/gilt ETFs, which get None in every Graham column."""
    out = df.copy()
    score_col, passed_col = [], []
    detail_cols = {name: [] for name in CRITERIA_LABELS}

    for _, row in out.iterrows():
        ticker = row["ticker"]
        asset_class = row.get("asset_class")

        if asset_class != "company":
            score_col.append(None)
            passed_col.append(None)
            for name in CRITERIA_LABELS:
                detail_cols[name].append(None)
            continue

        cache_key = f"graham:{ticker}"
        raw = cache.get(cache_key) if use_cache else None
        if raw is None:
            print(f"Fetching Graham data for {ticker}...")
            raw = fetch_graham_raw(ticker)
            cache.set(cache_key, raw)
            time.sleep(0.5)  # be polite to the API

        result = evaluate_graham(row.to_dict(), raw, min_market_cap=min_market_cap)

        score_col.append(f"{result['criteria_passed']}/{result['criteria_evaluated']}")
        passed_col.append(result["passes_graham"])
        for name in CRITERIA_LABELS:
            detail_cols[name].append(result["criteria"].get(name))

        if persist and "run_date" in row and pd.notna(row.get("run_date")):
            storage.save_graham_result(
                ticker, row["run_date"], score_col[-1], passed_col[-1]
            )

    out["graham_score"] = score_col
    out["passes_graham"] = passed_col
    for name, label in CRITERIA_LABELS.items():
        out[label] = detail_cols[name]

    return out


def rank_desirable(df: pd.DataFrame, top_n: int = None) -> pd.DataFrame:
    """Ranks full Graham passers by how strongly they satisfy the criteria --
    passing isn't binary in practice, since some passes rest on more of
    Graham's checks than others. Only rows with passes_graham == True are
    considered "desirable" at all; everything else is dropped.

    Ranking, in priority order:
      1. More evaluable criteria first (parsed from graham_score's "X/Y").
         A pass based on 8/8 checks actually applying is more solid than one
         based on only 4/8, where several criteria were simply unavailable.
      2. Graham Number (P/E x P/B), ascending. Lower means more margin of
         safety by Graham's own combined valuation shortcut -- this is the
         same 22.5 threshold from the pass/fail check, just used to order
         passers by how far under it they sit, not just whether they clear it.
      3. Dividend yield, descending, as a final tiebreaker.

    Adds no new columns to the row data itself -- this only re-orders (and
    optionally truncates) rows that already have graham_screen's output.
    """
    if "passes_graham" not in df.columns:
        raise ValueError("rank_desirable() expects a dataframe that's already been through graham_screen()")

    passers = df[df["passes_graham"] == True].copy()  # noqa: E712
    if passers.empty:
        return passers

    criteria_evaluated = passers["graham_score"].astype(str).str.split("/").str[1]
    passers["_criteria_evaluated"] = pd.to_numeric(criteria_evaluated, errors="coerce")
    passers["_graham_number"] = passers["trailing_pe"] * passers["price_to_book"]

    ranked = passers.sort_values(
        by=["_criteria_evaluated", "_graham_number", "dividend_yield"],
        ascending=[False, True, False],
        na_position="last",
    ).drop(columns=["_criteria_evaluated", "_graham_number"])

    if top_n is not None:
        ranked = ranked.head(top_n)
    return ranked
