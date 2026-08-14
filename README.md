# LSE Analyzer

A Python toolkit for pulling and screening financial data on London Stock
Exchange (LSE) listed **companies, ETFs, ETCs, and gilt ETFs**, with
valuation ratios (P/E, P/B, dividend yield, ROE, debt/equity, etc.) and
trailing price returns.

This merges two earlier prototypes: a caching + screening CLI, and a
broader instrument tracker built for scheduled (monthly, Raspberry Pi) runs.

## How it works

- **Primary source:** [yfinance](https://github.com/ranaroussi/yfinance) (unofficial
  Yahoo Finance wrapper). Free, no API key, decent LSE price + basic ratio coverage.
- **Fallback source:** [Financial Modeling Prep](https://financialmodelingprep.com/)
  (optional, free-tier API key). Fills in gaps when Yahoo data is missing —
  UK fundamentals on Yahoo are sometimes thin or stale.
- **Two storage layers:**
  - `cache.db` — short-term TTL cache (default 24h) so repeated runs the
    same day don't re-hit either API.
  - `data/market_data.db` — long-term store. Every run appends a dated
    fundamentals snapshot and upserts price history, so scheduled runs
    build an actual time series you can screen or chart historically.

## What it covers

| Asset class | Notes |
|---|---|
| Companies | FTSE 100 sample in `config.py` — edit freely |
| ETFs | Broad market + sector funds |
| ETCs | Physical gold/silver commodities |
| GILTs | Individual gilts don't trade on retail tickers, so **gilt ETFs** (e.g. iShares Core UK Gilts) are used as a proxy for government bond exposure |

LSE tickers use the `.L` suffix, e.g. `VOD.L`, `ULVR.L`, `IGLT.L`.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # optional: add an FMP_API_KEY if you have one
```

Get a free Financial Modeling Prep key at https://site.financialmodelingprep.com/developer/docs
(not required — the tool works with yfinance alone, FMP just fills gaps).

## Usage

Edit `watchlist.json` with the tickers you want to track, or use one of the
built-in asset-class universes from `config.py`:

```bash
python main.py                        # your watchlist.json, sorted by P/E
python main.py --asset-class etf      # every configured ETF
python main.py --asset-class gilt     # every configured gilt ETF
python main.py --all                  # everything: companies + ETFs + ETCs + gilts
python main.py --history              # add trailing 1m/3m/6m/1y price returns
python main.py --sort div_yield       # sort by dividend yield, descending
python main.py --refresh              # ignore cache, force fresh fetch
python main.py --screen --max-pe 15 --min-div-yield 3
python main.py --asset-class company --graham
python main.py --asset-class company --graham --graham-only
```

Every run prints a comparison table and saves a full CSV snapshot to
`output/`, timestamped.

## Benjamin Graham "defensive investor" screen (`--graham`)

Adds columns evaluating each company against Graham's classic criteria from
*The Intelligent Investor* (ch. 14). Only applies to `asset_class: company`
rows — ETFs, ETCs, and gilt ETFs get `None`/blank in every Graham column,
since the criteria (P/E, dividend record, earnings stability, etc.) don't
make sense for a fund.

| Column | Graham's original criterion | How it's computed here |
|---|---|---|
| `G:Size` | Adequate size of enterprise | Market cap ≥ £100M (adjustable via `--graham-min-market-cap`) — a rough modern proxy for Graham's original sales-based test |
| `G:FinCond` | Strong financial condition | Current ratio ≥ 2, and long-term debt ≤ working capital |
| `G:EarnStable` | Earnings stability | Positive net income in every year of available history |
| `G:DivRecord` | Uninterrupted dividend record | Dividend paid in every year across the available dividend history |
| `G:EarnGrowth` | ≥33% earnings growth | EPS (or net income, if EPS isn't reported) grew ≥33% from the earliest to latest available year |
| `G:PE<=15` | Moderate P/E ratio | Trailing P/E ≤ 15 |
| `G:PB<=1.5` | Moderate price-to-assets | Price-to-book ≤ 1.5 |
| `G:PExPB<=22.5` | Combined shortcut ("Graham Number") | P/E × P/B ≤ 22.5 |

`graham_score` shows criteria passed / criteria evaluable (e.g. `6/8`).
`passes_graham` is `True` only if *every evaluable* criterion passed, `False`
if any evaluable criterion failed, and blank/`None` if fewer than 4 criteria
could be evaluated at all (too little data for a fair verdict either way).

**Important caveat on data depth:** Graham's original test wants 10 years of
earnings history and 20 years of uninterrupted dividends. Free data via
yfinance typically only covers ~4 years of annual financial statements, so
`G:EarnStable`, `G:DivRecord`, and `G:EarnGrowth` are evaluated over a much
shorter window than Graham intended. Treat `--graham` as a useful first-pass
screen, not a faithful reproduction of the original test — always sanity-check
a company's actual 10-year record (e.g. via its annual reports) before acting
on a "pass".

Graham results are also written back into `data/market_data.db` alongside
each snapshot, so pass/fail is tracked historically across scheduled runs,
not just shown for the current run.

## Project structure

```
lse_analyzer/
├── main.py                 # CLI entry point
├── config.py                # settings, env vars, and the asset-class ticker lists
├── watchlist.json            # your list of tickers to track by default
├── run_monthly.sh            # cron wrapper for scheduled (e.g. Pi) runs
├── data/
│   ├── cache.py             # short-term TTL cache (avoids re-hitting APIs same-day)
│   ├── storage.py           # long-term SQLite store: price history + dated snapshots
│   └── fetchers.py          # yfinance + FMP fallback fetching, cache-aware
├── analysis/
│   ├── ratios.py              # ratio calcs, screening, sorting, trailing returns
│   └── graham.py              # Benjamin Graham defensive-investor screen (--graham)
├── output/                  # timestamped CSV snapshots from each run
├── reports/                 # cron run logs (created by run_monthly.sh)
├── requirements.txt
└── .env.example
```

## Deploying to a Raspberry Pi with a monthly cron job

1. Copy the project to the Pi and set it up the same way as above (venv + install).
2. Make sure `run_monthly.sh` is executable: `chmod +x run_monthly.sh`
3. Open the crontab editor: `crontab -e`
4. Add a line to run on the 1st of every month at 07:00:
   ```
   0 7 1 * * /home/pi/lse_analyzer/run_monthly.sh
   ```
   (adjust the path to wherever you place the project)
5. Each run forces a fresh fetch (`--refresh`) across the full instrument
   universe (`--all`) with trailing returns (`--history`), and logs to
   `reports/`. The long-term database in `data/market_data.db` accumulates
   across runs — that's what you'll want for month-over-month analysis.

## Extending it

- Add more ratios in `analysis/ratios.py` (e.g. PEG ratio, interest cover).
- Add real gilt yield-curve data from the UK Debt Management Office or Bank
  of England, separate from the ETF proxy, for genuine fixed-income analysis.
- Add charting (matplotlib) over `data/storage.py`'s price/snapshot history.
- Add alerting (email/Slack) when a screened instrument's ratio crosses a threshold.
- Swap in a different fallback source in `data/fetchers.py` if you get an
  Alpha Vantage or Finnhub key instead of FMP.

## Notes on data quality

Free financial data APIs vary in coverage quality for LSE-listed companies
specifically (most are US-centric), and ETF/ETC fundamentals coverage is
thinner than for individual companies (e.g. no P/E for an ETF). Cross-check
anything you're about to act on against the instrument's own factsheet or
the company's investor relations page / RNS filings before making
investment decisions — this tool is for screening, not a source of truth.
