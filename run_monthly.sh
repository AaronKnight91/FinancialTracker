#!/bin/bash
# Wrapper for cron. Cron runs with a minimal environment, so we cd into
# the project and use its venv explicitly rather than relying on PATH.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1

# Load .env if present, so REPORTS_DIR (and anything else) can override
# the default below -- same override this project's Python side respects.
if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  source "$PROJECT_DIR/.env"
  set +a
fi

REPORTS_DIR="${REPORTS_DIR:-$PROJECT_DIR/reports}"
mkdir -p "$REPORTS_DIR"
LOG_FILE="$REPORTS_DIR/run_$(date +%Y-%m-%d_%H-%M-%S).log"

# Runs the full instrument universe with history included, forcing a fresh
# fetch (cache is meant for same-day re-runs, not month-old data).
"$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/main.py" --all --history --refresh >> "$LOG_FILE" 2>&1
