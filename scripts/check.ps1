# SentinelAI — Local Quality & Security Gate (PowerShell)
# Fails fast on formatting, linting, typing, unit testing, or security findings.

$ErrorActionPreference = "Stop"

Write-Host "=== SentinelAI Quality & Security Gate ===" -ForegroundColor Cyan

$Python = ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "Virtual environment not found at .venv. Please run Day 002 setup first."
    exit 1
}

Write-Host "[1/7] Checking code format with Ruff..." -ForegroundColor Yellow
& $Python -m ruff format --check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/7] Linting code with Ruff..." -ForegroundColor Yellow
& $Python -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[3/7] Type-checking with Mypy..." -ForegroundColor Yellow
& $Python -m mypy backend
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[4/7] Running unit tests with Pytest..." -ForegroundColor Yellow
& $Python -m pytest backend\tests\unit -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[5/7] Scanning code security with Bandit..." -ForegroundColor Yellow
& $Python -m bandit -r backend\app -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[6/7] Checking dependency vulnerabilities with pip-audit..." -ForegroundColor Yellow
& $Python -m pip_audit -r backend\requirements.lock --desc
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[7/7] Scanning for leaked credentials with detect-secrets..." -ForegroundColor Yellow
& $Python -m detect_secrets scan --baseline .secrets.baseline
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== All Quality & Security Gates Passed Successfully! ===" -ForegroundColor Green

