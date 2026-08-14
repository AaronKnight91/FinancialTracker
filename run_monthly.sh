#!/bin/bash
# Wrapper for cron. Cron runs with a minimal environment, so we cd into
# the project and use its venv explicitly rather than relying on PATH.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1

LOG_FILE="$PROJECT_DIR/reports/run_$(date +%Y-%m-%d_%H-%M-%S).log"
mkdir -p "$PROJECT_DIR/reports"

# Runs the full instrument universe with history included, forcing a fresh
# fetch (cache is meant for same-day re-runs, not month-old data).
"$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/main.py" --all --history --refresh >> "$LOG_FILE" 2>&1
