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
python main.py --discover              # scan the whole FTSE 100+250 for Graham passers
python main.py --dividends              # full dividend payment history + summary columns
python main.py --import-portfolio my_isa.csv --portfolio-name ISA  # import a broker export
python main.py --list-delisted          # export Wikipedia's formerly-listed LSE companies
```

Every run prints a comparison table and saves a full CSV snapshot to
`output/`, timestamped.

### Choosing where outputs get saved

By default, every CSV this tool produces (comparison snapshots, dividend
history exports, the delisted companies list) goes to `./output`. Change
that either in `.env`:

```bash
OUTPUT_DIR=/home/pi/lse-reports
```

or per-run on the command line, which takes priority over `.env`:

```bash
python main.py --output-dir /home/pi/lse-reports
python main.py --dividends --output-dir D:\Reports\LSE
```

Either way, the directory is created automatically if it doesn't exist yet
(including any missing parent folders). `run_monthly.sh`'s cron logs have
the same override available via `REPORTS_DIR` in `.env` (no command-line
flag for that one, since the cron script itself isn't interactive).

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

## Finding Graham passers without a pre-set watchlist (`--discover`)

`--discover` scans the whole FTSE 100 + FTSE 250 (~350 companies) for Graham
passers, instead of relying on `watchlist.json`:

```bash
python main.py --discover                        # full FTSE 100+250 scan, shows only passers
python main.py --discover --discover-show-all     # show every company scanned, not just passers
python main.py --discover --discover-limit 30     # quick test run on the first 30 companies
python main.py --discover --discover-ftse100-only # FTSE 100 only (faster, ~100 companies)
python main.py --discover --discover-refresh-universe  # force re-fetch the constituent list
```

**Where the ticker list comes from:** yfinance has no "list every LSE ticker"
endpoint, so `--discover` needs a separate source just to know what to scan.
By default it scrapes the FTSE 100 and FTSE 250 constituent tables from
Wikipedia (free, no API key) and caches the result locally for 30 days
(`data/universe_cache.json`) — Wikipedia is only hit once a month, not every
run. If you have an `FMP_API_KEY` set, `--discover-source fmp` uses Financial
Modeling Prep's stock screener instead, which can cover more of the LSE than
just the FTSE 350.

**Why there's a pre-filter:** the full Graham check needs 3 extra API calls
per company (balance sheet, income statement, dividend history) on top of
the 2 already needed for basic fundamentals. Run that on 350 companies and
it adds up. So `--discover` first filters on P/E ≤ 20 and P/B ≤ 2.5 (using
data already fetched for the ratio table) and only runs the full, slower
Graham check on survivors. Companies excluded this way show up as
`excluded by pre-filter` rather than a real fail — widen the thresholds with
`--prefilter-max-pe` / `--prefilter-max-pb` (or set them very high) if you'd
rather run the full check on everything.

**A first full run will be slow** — expect several minutes for the FTSE 350,
mostly the fundamentals pass. Cached results (24h TTL by default) make
repeat runs the same day much faster. This is well suited to the monthly
cron setup below: let it run overnight once a month rather than interactively.

**Ticker conversion caveat:** LSE tickers are converted to Yahoo's format
with a simple rule (e.g. `BT.A` → `BT-A.L`), which covers the vast majority
of cases but isn't a verified lookup table. A handful of tickers may not
resolve correctly; these will just show up as "no data returned" rather than
break the run.

## Companies formerly listed on the LSE (`--list-delisted`)

```bash
python main.py --list-delisted                # export to output/delisted_companies_<timestamp>.csv
python main.py --list-delisted --no-save      # print to console instead
python main.py --list-delisted --list-delisted-refresh  # force re-fetch, ignoring the ~30 day cache
```

Scrapes Wikipedia's "Companies formerly listed on the London Stock Exchange"
category and exports `name, wikipedia_url` for each one.

**This is deliberately a separate, standalone feature from `--discover` and
`--graham`, not another `--discover-source` option.** Category pages on
Wikipedia carry no ticker or ISIN data at all — just article titles — and
even if they did, most companies delisted years or decades ago have no
fetchable price data via yfinance anyway. Piping this into the ratio/Graham
pipeline would produce a table that looks like real market data but mostly
isn't. So `--list-delisted` just exports names and links for research, and
stops there.

**On completeness — there genuinely isn't a free comprehensive answer here.**
No free source (this one included) gives you every company ever traded on
the LSE, including obscure delisted ones:
- Wikipedia's category only includes companies notable enough to have an
  article — a few hundred, not the many thousands that have delisted since
  the LSE's founding in 1801.
- The FCA maintains the live Official List and publishes individual removal
  notices as they happen, but no single downloadable historical archive of
  every removal.
- Companies House's free bulk data covers currently active UK companies
  (plus a ~6-year rolling window of dissolved ones) — and that's company
  *registration* status, not LSE *listing* status; a company can delist
  from the LSE while remaining a registered (private) company for years.

A genuinely complete, survivorship-bias-free history is really the domain
of paid institutional data: Refinitiv/LSEG Datastream (which has an
"include dead securities" option built for exactly this), Bloomberg, Orbis,
or — the standard academic reference for UK equities specifically — the
London Share Price Database (LSPD) at London Business School, which goes
back to the 1950s. If you need rigor rather than a research starting point,
that's where to look.

## Full dividend payment history (`--dividends`)

```bash
python main.py --dividends                       # your watchlist, with dividend summary columns
python main.py --asset-class etf --dividends       # works for ETFs/ETCs/gilts too, not just companies
python main.py --discover --dividends              # combine with discovery/screening
```

Adds four summary columns to the comparison table:

| Column | Meaning |
|---|---|
| `Div Yrs` | Number of distinct calendar years with at least one payment, in whatever history is available |
| `TTM Div` | Total dividend/distribution amount paid in the trailing 12 months |
| `Last Div Date` | Date of the most recent payment |
| `Last Div Amt` | Amount of the most recent payment |

**Every individual payment is also saved**, not just the summary: each run
writes `output/dividend_history_<timestamp>.csv` with one row per payment
(`ticker, date, amount, source`), and the same data is persisted to
`data/market_data.db`'s `dividends` table so it accumulates across scheduled
runs rather than being overwritten.

**Where the data comes from:** yfinance's dividend history is the default
source, but it's sometimes thin for LSE-listed instruments -- a handful of
years, or occasionally empty for one that clearly does pay a regular
dividend. If `FMP_API_KEY` is set and yfinance's history covers fewer than
5 distinct years, `--dividends` also tries Financial Modeling Prep's
historical dividend endpoint and keeps whichever source actually covers
more years. Without an FMP key, you get yfinance's data as-is.

This also improves the `--graham` "dividend record" criterion for free:
`analysis/graham.py` uses the same fallback-aware fetch, so a company that
looked like it had no dividend history via yfinance alone may now show a
real (if still shorter-than-Graham's-20-years) record if FMP has more.

## Importing your own portfolio (`--import-portfolio`)

Imports a broker transaction history export, tracks it in its own set of
database tables, and calculates ratios for whatever you actually hold —
currently supports **Freetrade's** CSV export format.

```bash
# Export your transaction history from the Freetrade app first
# (Account -> Statements & documents -> Transaction history)

python main.py --import-portfolio freetrade_export.csv --portfolio-name ISA
python main.py --import-portfolio jan.csv feb.csv mar.csv --portfolio-name ISA  # several files at once
python main.py --import-portfolio my_gia_export.csv --portfolio-name GIA        # a separate portfolio
python main.py --import-portfolio ~/Downloads/freetrade --portfolio-name ISA    # a folder: imports every CSV inside
python main.py --import-portfolio --portfolio-name ISA                          # scans BROKER_EXPORTS_DIR/ISA/
python main.py --import-portfolio                                                # no name: imports every portfolio subfolder
python main.py --list-portfolios                                                 # see what's been imported
```

### Setting a default export folder (one subfolder per portfolio)

Rather than typing a file path every time, point `BROKER_EXPORTS_DIR` at a
base folder laid out with one subfolder per portfolio:

```
C:\Users\yourname\Data\Financials\raw\ISA\freetrade_export.csv
C:\Users\yourname\Data\Financials\raw\GIA\freetrade_export.csv
```

```bash
# in .env -- this is the BASE folder ("raw"), not including the portfolio name
BROKER_EXPORTS_DIR=C:\Users\yourname\Data\Financials\raw
```

Then:

```bash
python main.py --import-portfolio --portfolio-name ISA
# -> scans C:\Users\yourname\Data\Financials\raw\ISA\*.csv

python main.py --import-portfolio
# -> no name given: finds every subfolder under BROKER_EXPORTS_DIR
#    (ISA, GIA, ...) and imports each one as its own portfolio in a
#    single run -- handy for a periodic "import whatever's new" pass
```

Or override the base folder for a single run without touching `.env`:

```bash
python main.py --import-portfolio --portfolio-name ISA --broker-exports-dir D:\OtherExports
```

With no `BROKER_EXPORTS_DIR` set, it defaults to `./broker_exports` inside
the project (created automatically) — so `./broker_exports/ISA/*.csv` works
out of the box with no configuration. You can also still point
`--import-portfolio` directly at a specific file, or any single folder
(not necessarily under the configured base), and it behaves the same as
before — the base-folder-with-subfolders resolution only kicks in when you
give it a portfolio name (or nothing at all) with no explicit path.

**This folder holds real financial data, so it's excluded from git** —
`.gitignore` covers `broker_exports/*.csv` specifically (the folder itself
stays tracked via a `.gitkeep`, so the project ships ready to use). If you
set `BROKER_EXPORTS_DIR` to somewhere outside the project entirely (as in
the Windows example above), that's never a git concern regardless.

Each run:

1. **Parses the CSV** and validates it actually looks like a Freetrade export
   (checks for the expected columns) before touching anything.
2. **Deduplicates automatically.** Every transaction row is fingerprinted by
   hashing every column's value together; that fingerprint is the row's
   primary key in the database, so re-importing a file you've already
   loaded — or a newer export whose date range overlaps an old one, which
   is the normal case for "last 12 months" broker exports — only inserts
   the transactions that are actually new. You'll never get duplicate rows
   from importing multiple overlapping files, and it's safe to just
   re-import your latest export every so often rather than tracking exactly
   what's new yourself.
3. **Extracts every distinct ticker** ever seen in the portfolio's history
   (buys, sells, and dividend payments all carry a ticker) and adds any that
   aren't already in `watchlist.json`, without touching entries already there.
4. **Fetches ratios and saves them** to a table scoped to that portfolio.

**Multiple portfolios stay completely separate.** Each portfolio gets its
own pair of tables in `data/market_data.db`:

```
portfolio_<name>_transactions   -- every transaction row, deduplicated
portfolio_<name>_ratios         -- latest ratios for tickers held
```

e.g. `--portfolio-name ISA` and `--portfolio-name GIA` produce
`portfolio_isa_transactions` / `portfolio_isa_ratios` and
`portfolio_gia_transactions` / `portfolio_gia_ratios` — importing into one
never reads from or writes to the other.

**A caveat on the dedup mechanism:** `TOP_UP` and `INTEREST_FROM_CASH` rows
in Freetrade's export don't carry a broker-assigned order ID the way trades
do, so their fingerprint is based on timestamp + amount + type instead. Two
genuinely separate interest payments of the same amount at the exact same
timestamp would be indistinguishable — not realistically possible for real
broker data (which timestamps to the second), but worth knowing about if
you ever see a payment count that looks one short.

**Adapting this to a different broker:** `data/portfolio.py`'s dedup,
per-portfolio tables, watchlist sync, and ratio calculation are all broker-
agnostic — only `read_freetrade_csv()` (and the `FREETRADE_COLUMNS` list it
checks against) is specific to Freetrade's export format. Swapping in
another broker's CSV mainly means writing an equivalent parsing function
for its column layout.

## Project structure

```
lse_analyzer/
├── main.py                 # CLI entry point
├── config.py                # settings, env vars, and the asset-class ticker lists
├── watchlist.json            # your list of tickers to track by default
├── run_monthly.sh            # cron wrapper for scheduled (e.g. Pi) runs
├── broker_exports/            # default drop folder for broker CSVs (see BROKER_EXPORTS_DIR)
├── data/
│   ├── cache.py             # short-term TTL cache (avoids re-hitting APIs same-day)
│   ├── storage.py           # long-term SQLite store: price history, dated snapshots, dividends
│   ├── fetchers.py          # yfinance + FMP fallback fetching, cache-aware
│   ├── universe.py          # discovers LSE tickers to scan for --discover (Wikipedia/FMP)
│   ├── dividends.py         # dividend payment history, yfinance + FMP fallback
│   └── portfolio.py         # imports broker transaction CSVs into per-portfolio tables
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
