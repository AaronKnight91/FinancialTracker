"""
Central configuration for the LSE Analyzer project.

Combines:
  - env-based settings (API keys, cache TTL) from the FMP-fallback version
  - asset-class ticker lists (companies / ETFs / ETCs / GILT ETFs) from
    the broader UK-market-tracker version
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# Where CSV outputs (comparison snapshots, dividend history exports, delisted
# companies list) get saved. Override in .env, or per-run with --output-dir.
_output_dir_env = os.getenv("OUTPUT_DIR", "").strip()
OUTPUT_DIR = Path(_output_dir_env) if _output_dir_env else (BASE_DIR / "output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Where run_monthly.sh writes cron logs. Same override pattern.
_reports_dir_env = os.getenv("REPORTS_DIR", "").strip()
REPORTS_DIR = Path(_reports_dir_env) if _reports_dir_env else (BASE_DIR / "reports")
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# --- Caching ---
# Short-term cache: avoids re-hitting APIs for repeated runs within CACHE_TTL_HOURS.
CACHE_DB_PATH = BASE_DIR / "cache.db"
CACHE_TTL_HOURS = float(os.getenv("CACHE_TTL_HOURS", "24"))

# Long-term store: accumulates a snapshot + price history every run, forever.
# This is what makes monthly Pi runs build a track record over time.
HISTORY_DB_PATH = BASE_DIR / "data" / "market_data.db"
PRICE_HISTORY_PERIOD = "5y"  # yfinance period string, used on first fetch of a ticker

# --- Fallback data source (fills gaps when Yahoo data is thin, common for UK stocks) ---
FMP_API_KEY = os.getenv("FMP_API_KEY", "").strip()
FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"

# --- Watchlist (used by default unless --asset-class or --watchlist overrides it) ---
DEFAULT_WATCHLIST_PATH = BASE_DIR / "watchlist.json"

# --- Broker export files (for --import-portfolio) ---
# Base directory holding one subfolder per portfolio, e.g.:
#   BROKER_EXPORTS_DIR/ISA/*.csv
#   BROKER_EXPORTS_DIR/GIA/*.csv
# `--import-portfolio --portfolio-name ISA` scans BROKER_EXPORTS_DIR/ISA/ for
# every CSV in it; `--import-portfolio` with no name at all imports every
# subfolder found (batch mode). Override in .env or per-run with
# --broker-exports-dir. Contains real financial data, so it's gitignored.
_broker_exports_env = os.getenv("BROKER_EXPORTS_DIR", "").strip()
BROKER_EXPORTS_DIR = Path(_broker_exports_env) if _broker_exports_env else (BASE_DIR / "broker_exports")
BROKER_EXPORTS_DIR.mkdir(exist_ok=True)

# --- Asset universe ---
# Yahoo Finance tickers for the London Stock Exchange use a ".L" suffix.
# Individual UK Gilts (government bonds) don't trade on retail-accessible
# tickers -- they trade OTC via gilt-edged market makers. The practical
# workaround (used here) is to track GILT ETFs, which hold baskets of real
# gilts and are LSE-listed like any other ETF.

COMPANIES = {
    "AZN.L": "AstraZeneca",
    "SHEL.L": "Shell",
    "HSBA.L": "HSBC Holdings",
    "ULVR.L": "Unilever",
    "BP.L": "BP",
    "GSK.L": "GSK",
    "DGE.L": "Diageo",
    "RIO.L": "Rio Tinto",
    "BATS.L": "British American Tobacco",
    "VOD.L": "Vodafone Group",
    "LLOY.L": "Lloyds Banking Group",
    "BARC.L": "Barclays",
    "NG.L": "National Grid",
    "TSCO.L": "Tesco",
    "RR.L": "Rolls-Royce Holdings",
}

ETFS = {
    "VUKE.L": "Vanguard FTSE 100 UCITS ETF",
    "ISF.L": "iShares Core FTSE 100 UCITS ETF",
    "VWRL.L": "Vanguard FTSE All-World UCITS ETF",
    "VMID.L": "Vanguard FTSE 250 UCITS ETF",
    "CSPX.L": "iShares Core S&P 500 UCITS ETF",
}

ETCS = {
    "PHAU.L": "WisdomTree Physical Gold",
    "PHAG.L": "WisdomTree Physical Silver",
    "SGLN.L": "iShares Physical Gold",
}

GILTS = {
    "IGLT.L": "iShares Core UK Gilts UCITS ETF",
    "VGOV.L": "Vanguard UK Gilt UCITS ETF",
    "GLTA.L": "iShares UK Gilts All Stocks UCITS ETF",
}

ALL_INSTRUMENTS = {**COMPANIES, **ETFS, **ETCS, **GILTS}

ASSET_CLASSES = {
    "company": COMPANIES,
    "etf": ETFS,
    "etc": ETCS,
    "gilt": GILTS,
}


def asset_class_for(ticker: str) -> str:
    for asset_class, members in ASSET_CLASSES.items():
        if ticker in members:
            return asset_class
    return "unknown"
