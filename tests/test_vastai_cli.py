from __future__ import annotations

import json
import subprocess

import pytest

from vastrun_kit import errors, vastai_cli


class FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _patch_run(monkeypatch: pytest.MonkeyPatch, proc: FakeProc) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake(cmd, capture_output, text, timeout):  # noqa: ANN001
        seen.append(cmd)
        return proc

    monkeypatch.setattr(vastai_cli.subprocess, "run", fake)
    return seen


def test_run_vastai_injects_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_run(monkeypatch, FakeProc(0, "ok"))
    rc, out, err = vastai_cli.run_vastai(["show", "user"])
    assert rc == 0 and out == "ok"
    assert seen[0][:4] == ["vastai", "--api-key", "test-token-123", "show"]


def test_run_vastai_raw_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"id": 1, "name": "x"}]
    _patch_run(monkeypatch, FakeProc(0, json.dumps(payload)))
    assert vastai_cli.run_vastai_raw(["show", "instances"]) == payload


def test_run_vastai_raw_empty_stdout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, FakeProc(0, ""))
    with pytest.raises(errors.VastaiCliError, match="empty stdout"):
        vastai_cli.run_vastai_raw(["create", "instance"])


def test_run_vastai_raw_bad_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, FakeProc(0, "<<<not json>>>"))
    with pytest.raises(errors.VastaiCliError, match="non-JSON"):
        vastai_cli.run_vastai_raw(["search", "offers"])


def test_run_vastai_raw_nonzero_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, FakeProc(2, "", "boom"))
    with pytest.raises(errors.VastaiCliError, match="boom"):
        vastai_cli.run_vastai_raw(["show", "user"])


def test_run_vastai_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cmd, capture_output, text, timeout):  # noqa: ANN001
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(vastai_cli.subprocess, "run", boom)
    with pytest.raises(errors.VastaiCliError, match="timed out"):
        vastai_cli.run_vastai(["show", "user"])
