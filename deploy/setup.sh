#!/bin/bash
# setup.sh — первоначальная настройка на сервере
set -e

echo "=== Instagram Batch Analyzer: Setup ==="

# 1. Python check
python3 --version || { echo "ERROR: python3 not found. Install: apt install python3 python3-pip"; exit 1; }

# 2. Install deps
echo "Installing Python dependencies..."
pip3 install -r requirements.txt

# 3. Check ANTHROPIC_API_KEY
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo "⚠  ANTHROPIC_API_KEY not set!"
    echo "   Export it before running:"
    echo "   export ANTHROPIC_API_KEY=sk-ant-..."
    echo ""
fi

echo ""
echo "=== Setup complete ==="
echo "Next step: run ./run.sh"
