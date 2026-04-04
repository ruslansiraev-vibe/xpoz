#!/bin/bash
# run.sh — запуск batch анализа в фоне с логом
set -e

LOG="batch_$(date +%Y%m%d_%H%M%S).log"
XPOZ_ACCOUNTS_CSV="${XPOZ_ACCOUNTS_CSV:-$(pwd)/accounts.csv}"
XPOZ_DB_PATH="${XPOZ_DB_PATH:-$(cd .. && pwd)/data.db}"

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY is not set"
    echo "Run: export ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

if [ ! -f "$XPOZ_ACCOUNTS_CSV" ]; then
    echo "ERROR: accounts CSV not found: $XPOZ_ACCOUNTS_CSV"
    echo "Set XPOZ_ACCOUNTS_CSV=/path/to/accounts.csv or place accounts.csv next to run.sh"
    exit 1
fi

echo "=== Starting batch analysis ==="
echo "Log file: $LOG"
echo "Workers: 20"
echo "Filter: email-qualified (7,533 accounts)"
echo "SQLite DB: $XPOZ_DB_PATH"
echo "Accounts CSV: $XPOZ_ACCOUNTS_CSV"
echo ""
echo "Running in background with nohup..."
echo "Check progress: tail -f $LOG"
echo ""

XPOZ_DB_PATH="$XPOZ_DB_PATH" XPOZ_ACCOUNTS_CSV="$XPOZ_ACCOUNTS_CSV" nohup python3 batch_analyze.py \
    --preset email-qualified \
    --workers 20 \
    > "$LOG" 2>&1 &

PID=$!
echo "PID: $PID"
echo "To monitor: tail -f $LOG"
echo "To stop:    kill $PID"
echo ""
echo "Expected time: ~1.5–2 hours"
echo "Results are written to SQLite in real-time:"
echo "$XPOZ_DB_PATH"
