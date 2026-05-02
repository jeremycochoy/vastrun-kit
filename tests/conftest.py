"""Shared test fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _stub_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All tests get a deterministic API key + isolated CWD so dotenv reads don't leak."""
    monkeypatch.setenv("VASTAI_API_TOKEN", "test-token-123")
    monkeypatch.chdir(tmp_path)
