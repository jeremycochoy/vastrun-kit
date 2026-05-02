from __future__ import annotations

from pathlib import Path

import pytest

from vastrun_kit import client_config, errors


def test_load_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VASTAI_API_TOKEN", "from-env")
    assert client_config.load_api_key() == "from-env"


def test_load_api_key_from_cwd_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("VASTAI_API_TOKEN", raising=False)
    monkeypatch.setattr(client_config, "PACKAGE_ENV", tmp_path / "_no_pkg.env")
    (tmp_path / ".env").write_text('VASTAI_API_TOKEN="dotenv-key"\n# comment\n')
    monkeypatch.chdir(tmp_path)
    assert client_config.load_api_key() == "dotenv-key"


def test_load_api_key_missing_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("VASTAI_API_TOKEN", raising=False)
    monkeypatch.setattr(client_config, "PACKAGE_ENV", tmp_path / "_no_pkg.env")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(errors.MissingCredentialError):
        client_config.load_api_key()


def test_load_vastrun_toml_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="vastai-kit"):
        client_config.load_vastrun_toml(tmp_path / ".vastrun.toml")


def test_load_vastrun_toml_parses(tmp_path: Path) -> None:
    p = tmp_path / ".vastrun.toml"
    p.write_text('[vast]\nmin_vram_gb = 24\ngpu_name = ["A100", "H100"]\n')
    data = client_config.load_vastrun_toml(p)
    assert client_config.vast_section(data) == {"min_vram_gb": 24, "gpu_name": ["A100", "H100"]}
