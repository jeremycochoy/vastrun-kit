"""Tests for `vastrun-exec`.

Behaviour under test (from SPEC > vastrun-exec):
  - Resolve SSH endpoint via `ssh.resolve_ssh_endpoint`.
  - Stream the command via `ssh.ssh_exec(host, port, command, stream=True)`.
  - On rc==255 (SSH transport failure), retry once after a 3s sleep.
  - Best-effort spent-so-far footer on stderr — silent if it raises.
  - Propagate the command's exit code; exit 1 only on missing creds or
    unresolvable SSH endpoint.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from vastrun_kit import errors
from vastrun_kit.cli import exec_cmd

runner = CliRunner()


def _patch_ssh(
    monkeypatch: pytest.MonkeyPatch,
    *,
    endpoint: tuple[str, int] | None = ("h.example", 22),
    rcs: list[int] | None = None,
    find: object = "unset",
) -> dict[str, list]:
    """Stub ssh / instances / time.sleep. Returns a dict capturing call args."""
    seen: dict[str, list] = {"exec": [], "endpoint": [], "sleep": [], "find": []}

    def fake_resolve(inst_id: int):
        seen["endpoint"].append(inst_id)
        return endpoint

    rc_iter = iter(rcs or [0])

    def fake_exec(host, port, command, **kwargs):  # noqa: ANN001
        seen["exec"].append({"host": host, "port": port, "command": command, "kwargs": kwargs})
        try:
            return next(rc_iter), "", ""
        except StopIteration:
            return (rcs[-1] if rcs else 0), "", ""

    def fake_sleep(s):  # noqa: ANN001
        seen["sleep"].append(s)

    def fake_find(inst_id: int):
        seen["find"].append(inst_id)
        if find == "unset":
            return None
        if isinstance(find, BaseException) or (
            isinstance(find, type) and issubclass(find, BaseException)
        ):
            raise find  # type: ignore[misc]
        return find

    monkeypatch.setattr(exec_cmd.ssh, "resolve_ssh_endpoint", fake_resolve)
    monkeypatch.setattr(exec_cmd.ssh, "ssh_exec", fake_exec)
    monkeypatch.setattr(exec_cmd.time, "sleep", fake_sleep)
    monkeypatch.setattr(exec_cmd.instances, "find_instance", fake_find)
    return seen


# ---------- happy path ---------------------------------------------------- #


def test_happy_path_rc_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_ssh(monkeypatch, rcs=[0])
    result = runner.invoke(exec_cmd.app, ["12345", "nvidia-smi"])
    assert result.exit_code == 0
    assert len(seen["exec"]) == 1
    call = seen["exec"][0]
    assert call["host"] == "h.example"
    assert call["port"] == 22
    assert call["command"] == "nvidia-smi"
    assert call["kwargs"].get("stream") is True
    # No SSH retry → no sleep
    assert seen["sleep"] == []


# ---------- non-255 user-command failure (no retry) ----------------------- #


def test_command_failure_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_ssh(monkeypatch, rcs=[2])
    result = runner.invoke(exec_cmd.app, ["12345", "false"])
    assert result.exit_code == 2
    assert len(seen["exec"]) == 1
    assert seen["sleep"] == []


# ---------- 255 retry-once paths ----------------------------------------- #


def test_ssh_255_then_success_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_ssh(monkeypatch, rcs=[255, 0])
    result = runner.invoke(exec_cmd.app, ["12345", "nvidia-smi"])
    assert result.exit_code == 0
    assert len(seen["exec"]) == 2
    # The retry sleep happens once with 3 seconds
    assert seen["sleep"] == [3]


def test_ssh_255_twice_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_ssh(monkeypatch, rcs=[255, 255])
    result = runner.invoke(exec_cmd.app, ["12345", "nvidia-smi"])
    assert result.exit_code == 255
    assert len(seen["exec"]) == 2


# ---------- endpoint missing → exit 1, no ssh_exec ------------------------ #


def test_endpoint_none_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_ssh(monkeypatch, endpoint=None, rcs=[0])
    result = runner.invoke(exec_cmd.app, ["12345", "nvidia-smi"])
    assert result.exit_code == 1
    assert seen["exec"] == []
    # Diagnostic mentions the instance id and is on stderr
    assert "12345" in result.stderr


# ---------- spent-so-far footer ------------------------------------------ #


def test_footer_prints_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    inst = {"id": 12345, "dph_total": 0.50, "start_date": start}
    seen = _patch_ssh(monkeypatch, rcs=[0], find=inst)
    result = runner.invoke(exec_cmd.app, ["12345", "true"])
    assert result.exit_code == 0
    # Footer is on stderr (kept out of captured stdout) and reports spent
    assert "12345" in result.stderr
    assert "spent so far" in result.stderr
    # 2h × $0.50/h = $1.00
    assert "$1.00" in result.stderr


def test_footer_silent_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_ssh(monkeypatch, rcs=[0], find=RuntimeError("API hiccup"))
    result = runner.invoke(exec_cmd.app, ["12345", "true"])
    assert result.exit_code == 0
    # No footer text leaked because the lookup raised
    assert "spent so far" not in result.stderr


def test_footer_prints_once_after_255_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The footer must not double-print across the retry."""
    start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    inst = {"id": 12345, "dph_total": 1.00, "start_date": start}
    seen = _patch_ssh(monkeypatch, rcs=[255, 0], find=inst)
    result = runner.invoke(exec_cmd.app, ["12345", "true"])
    assert result.exit_code == 0
    assert result.stderr.count("spent so far") == 1


# ---------- missing credentials ----------------------------------------- #


def test_missing_credentials_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> str:
        raise errors.MissingCredentialError("VASTAI_API_TOKEN missing")

    monkeypatch.setattr(exec_cmd.client_config, "load_api_key", boom)

    # Make resolve_ssh_endpoint blow up if it gets called — it must not be.
    def must_not_be_called(_inst_id: int) -> tuple[str, int] | None:
        raise AssertionError("resolve_ssh_endpoint should not run when creds are missing")

    monkeypatch.setattr(exec_cmd.ssh, "resolve_ssh_endpoint", must_not_be_called)

    result = runner.invoke(exec_cmd.app, ["12345", "nvidia-smi"])
    assert result.exit_code == 1
    assert "VASTAI_API_TOKEN" in result.stderr
