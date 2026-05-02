from __future__ import annotations

import pytest
from typer.testing import CliRunner

from vastrun_kit import client_config, errors
from vastrun_kit.cli import balance as balance_cli

runner = CliRunner()


def _stub_show_user(monkeypatch: pytest.MonkeyPatch, payload: dict | Exception) -> None:
    def fake(args: list[str], **kw: object) -> dict:
        assert args == ["show", "user"]
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(balance_cli.vastai_cli, "run_vastai_raw", fake)


def test_balance_credit_only_when_balance_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_show_user(monkeypatch, {"credit": 6.45, "balance": 0})
    result = runner.invoke(balance_cli.app, [])
    assert result.exit_code == 0
    assert "Credit:  $6.45" in result.stdout
    assert "Balance:" not in result.stdout
    assert "Total:   $6.45" in result.stdout


def test_balance_all_three_when_balance_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_show_user(monkeypatch, {"credit": 1.5, "balance": 2.0})
    result = runner.invoke(balance_cli.app, [])
    assert result.exit_code == 0
    assert "Credit:  $1.50" in result.stdout
    assert "Balance: $2.00" in result.stdout
    assert "Total:   $3.50" in result.stdout


def test_balance_default_balance_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # `balance` field absent -> default 0, only Credit + Total printed.
    _stub_show_user(monkeypatch, {"credit": 4.0})
    result = runner.invoke(balance_cli.app, [])
    assert result.exit_code == 0
    assert "Credit:  $4.00" in result.stdout
    assert "Balance:" not in result.stdout
    assert "Total:   $4.00" in result.stdout


def test_balance_vastai_error_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_show_user(monkeypatch, errors.VastaiCliError("boom"))
    result = runner.invoke(balance_cli.app, [])
    assert result.exit_code == 1
    assert "boom" in result.stderr


def test_balance_missing_credentials_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> str:
        raise errors.MissingCredentialError("no token")

    monkeypatch.setattr(client_config, "load_api_key", boom)
    # The error may surface either via the eager load_api_key check or the underlying
    # vastai call — wire run_vastai_raw to also raise so either path exits 1.
    _stub_show_user(monkeypatch, errors.MissingCredentialError("no token"))
    result = runner.invoke(balance_cli.app, [])
    assert result.exit_code == 1
    assert "no token" in result.stderr or "VASTAI_API_TOKEN" in result.stderr
