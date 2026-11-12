"""Shared pytest setup: load the ignored repo-root .env so tests can resolve local DSNs."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
