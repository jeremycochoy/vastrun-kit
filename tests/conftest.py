"""Shared test fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _stub_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All tests get a deterministic API key + isolated CWD so dotenv reads don't leak."""
    monkeypatch.setenv("VASTAI_API_TOKEN", "test-token-123")
    monkeypatch.delenv("VASTRUN_REGISTRY_USERNAME", raising=False)
    monkeypatch.delenv("VASTRUN_REGISTRY_TOKEN", raising=False)
    monkeypatch.setattr("vastrun_kit.client_config.PACKAGE_ENV", tmp_path / "_no_pkg.env")
    monkeypatch.chdir(tmp_path)
