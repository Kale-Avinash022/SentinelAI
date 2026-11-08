"""Smoke tests for SentinelAI foundation."""

import sys
import os
from pathlib import Path


def test_python_version() -> None:
    """Ensure the runtime Python version meets the >= 3.12 specification."""
    major, minor = sys.version_info.major, sys.version_info.minor
    assert (major, minor) >= (3, 12), f"Expected Python >= 3.12, got {major}.{minor}"


def test_package_import() -> None:
    """Ensure app package imports cleanly with valid version."""
    import app
    assert hasattr(app, "__version__")
    assert app.__version__ == "0.1.0"


def test_env_example_has_no_secrets() -> None:
    """Ensure .env.example contains only safe placeholders, not live API keys."""
    env_example_path = Path(__file__).resolve().parents[3] / ".env.example"
    assert env_example_path.exists(), ".env.example must exist in repository root"

    content = env_example_path.read_text(encoding="utf-8")
    forbidden_tokens = ["sk-proj-", "sk-ant-", "ghp_", "AKIA"]
    for token in forbidden_tokens:
        assert token not in content, f"Forbidden real credential prefix '{token}' found in .env.example"


def test_repository_layout() -> None:
    """Ensure core directories exist as required by Day 002."""
    root = Path(__file__).resolve().parents[3]
    assert (root / "backend" / "app").is_dir()
    assert (root / "backend" / "tests").is_dir()
    assert (root / "docs").is_dir()
    assert (root / ".gitignore").is_file()
    assert (root / "pyproject.toml").is_file()

