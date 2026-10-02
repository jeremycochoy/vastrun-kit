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


# ---------- registry credentials (private images) ----------

FAKE_SECRET = "dckr_pat_FAKE-secret-0000"


def test_load_registry_credentials_none_when_unset() -> None:
    assert client_config.load_registry_credentials() is None


def test_load_registry_credentials_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(client_config.REGISTRY_USERNAME, "bob")
    monkeypatch.setenv(client_config.REGISTRY_TOKEN, FAKE_SECRET)
    assert client_config.load_registry_credentials() == ("bob", FAKE_SECRET)


def test_load_registry_credentials_from_cwd_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        f'VASTRUN_REGISTRY_USERNAME=bob\nVASTRUN_REGISTRY_TOKEN="{FAKE_SECRET}"\n'
    )
    assert client_config.load_registry_credentials() == ("bob", FAKE_SECRET)


def test_load_registry_credentials_half_set_raises_without_the_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(client_config.REGISTRY_TOKEN, FAKE_SECRET)
    with pytest.raises(errors.MissingCredentialError) as exc:
        client_config.load_registry_credentials()
    assert client_config.REGISTRY_USERNAME in str(exc.value)
    assert FAKE_SECRET not in str(exc.value)
