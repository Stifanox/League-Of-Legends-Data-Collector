"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from crawler.config import DEFAULTS

FIXTURES_DIR = Path(__file__).parent / "fixtures"

FAKE_KEY = "RGAPI-12345678-abcd-ef01-2345-6789abcdef01"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real environment (e.g. RIOT_API_KEY) out of tests."""
    for name in DEFAULTS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def write_env(tmp_path: Path) -> Callable[..., Path]:
    """Write a `.env` file in a temp dir from keyword args and return its path."""
    path = tmp_path / ".env"

    def _write(**values: str) -> Path:
        path.write_text("".join(f"{name}={value}\n" for name, value in values.items()), encoding="utf-8")
        return path

    return _write
