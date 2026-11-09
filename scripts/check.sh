#!/usr/bin/env bash
# SentinelAI — Local Quality & Security Gate
# Fails fast on formatting, linting, typing, unit testing, or security findings.

set -euo pipefail

echo "=== SentinelAI Quality & Security Gate ==="

PYTHON=".venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    PYTHON=".venv/Scripts/python"
fi

if [ ! -f "$PYTHON" ]; then
    echo "ERROR: Virtual environment not found at .venv. Please run Day 002 setup first."
    exit 1
fi

echo "[1/7] Checking code format with Ruff..."
"$PYTHON" -m ruff format --check .

echo "[2/7] Linting code with Ruff..."
"$PYTHON" -m ruff check .

echo "[3/7] Type-checking with Mypy..."
"$PYTHON" -m mypy backend

echo "[4/7] Running unit tests with Pytest..."
"$PYTHON" -m pytest backend/tests/unit -q

echo "[5/7] Scanning code security with Bandit..."
"$PYTHON" -m bandit -r backend/app -q

echo "[6/7] Checking dependency vulnerabilities with pip-audit..."
"$PYTHON" -m pip_audit -r backend/requirements.lock --desc

echo "[7/7] Scanning for leaked credentials with detect-secrets..."
"$PYTHON" -m detect_secrets scan --baseline .secrets.baseline

echo "=== All Quality & Security Gates Passed Successfully! ==="

