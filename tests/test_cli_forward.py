from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from vastrun_kit import config
from vastrun_kit.cli import forward as forward_cli

runner = CliRunner()


class _FakeProc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def test_forward_refuses_without_force(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []

    def fake_run(cmd: list[str], **kw: object) -> _FakeProc:
        called.append(cmd)
        return _FakeProc(0)

    monkeypatch.setattr(forward_cli.subprocess, "run", fake_run)
    result = runner.invoke(forward_cli.app, ["show", "offers"])
    assert result.exit_code == 1
    assert config.ISSUE_URL in result.stderr
    assert called == []


def test_forward_with_force_invokes_vastai(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []

    def fake_run(cmd: list[str], **kw: object) -> _FakeProc:
        called.append(cmd)
        return _FakeProc(0)

    monkeypatch.setattr(forward_cli.subprocess, "run", fake_run)
    result = runner.invoke(forward_cli.app, ["--force", "show", "offers"])
    assert result.exit_code == 0
    assert config.ISSUE_URL in result.stderr
    assert len(called) == 1
    cmd = called[0]
    assert cmd[0] == "vastai"
    assert "--api-key" in cmd
    assert "test-token-123" in cmd
    # User args appear verbatim at the tail.
    assert cmd[-2:] == ["show", "offers"]


def test_forward_propagates_returncode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        forward_cli.subprocess, "run", lambda cmd, **kw: _FakeProc(7)
    )
    result = runner.invoke(forward_cli.app, ["--force", "destroy", "instance", "1"])
    assert result.exit_code == 7
