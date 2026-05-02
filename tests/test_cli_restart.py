"""Tests for `vastrun-restart`.

Covers SPEC §"vastrun-restart":
- Resolve the instance via `find_instance`. Not found → exit 1.
- Already running/loading → no-op + exit 0.
- Without `--force`, missing/mismatched label → refuse + exit 1.
- `--force` skips the label check.
- `vastai start instance <id>` failure → exit 1.
- `wait_for_boot` BootFailure → exit 1 with inst id + recovery commands.
- `resolve_ssh_endpoint` None → exit 1 with the SSH-info-missing message.
- `wait_for_ssh` False → exit 1 with SSH-unreachable message.
- Happy path prints "Instance <id> restarted".
- Marker is NEVER rewritten by restart (assert via stub).
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from vastrun_kit import boot, errors
from vastrun_kit.cli import restart as restart_cli

runner = CliRunner()


@pytest.fixture
def patch_restart(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {
        "find": [],
        "start_argv": None,
        "boot": [],
        "wait_ssh": [],
        "marker_writes": [],
    }

    inst = {"id": 555, "actual_status": "exited", "label": "training-v1"}

    def fake_find(inst_id):
        seen["find"].append(inst_id)
        return inst

    monkeypatch.setattr(restart_cli.instances, "find_instance", fake_find)

    def fake_run_vastai(args, **kw):
        seen["start_argv"] = args
        return 0, "starting instance...\n", ""

    monkeypatch.setattr(restart_cli.vastai_cli, "run_vastai", fake_run_vastai)

    def fake_wait_boot(inst_id):
        seen["boot"].append(inst_id)
        return {"id": inst_id, "actual_status": "running"}

    monkeypatch.setattr(restart_cli.boot, "wait_for_boot", fake_wait_boot)

    monkeypatch.setattr(restart_cli.ssh, "resolve_ssh_endpoint",
                        lambda iid: ("ssh5.vast.ai", 22000))

    def fake_wait_ssh(host, port):
        seen["wait_ssh"].append((host, port))
        return True

    monkeypatch.setattr(restart_cli.ssh, "wait_for_ssh", fake_wait_ssh)

    # Marker MUST NOT be rewritten by restart.
    def must_not_write(host, port, mk):
        seen["marker_writes"].append((host, port, mk))

    monkeypatch.setattr(restart_cli.marker, "write_marker", must_not_write)

    return seen


# ----- not found ---------------------------------------------------------- #

def test_not_found_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restart_cli.instances, "find_instance", lambda iid: None)
    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "555" in combined
    assert "not found" in combined.lower()


# ----- already running --------------------------------------------------- #

def test_already_running_no_op_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    inst = {"id": 555, "actual_status": "running", "label": "training-v1"}
    monkeypatch.setattr(restart_cli.instances, "find_instance", lambda iid: inst)

    # If the start CLI got called, fail loudly.
    def must_not_call(args, **kw):
        raise AssertionError("vastai start should not be called when already running")

    monkeypatch.setattr(restart_cli.vastai_cli, "run_vastai", must_not_call)

    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 0
    assert "555" in result.stdout
    assert "already running" in result.stdout.lower()


def test_already_loading_no_op_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    inst = {"id": 555, "actual_status": "loading", "label": "training-v1"}
    monkeypatch.setattr(restart_cli.instances, "find_instance", lambda iid: inst)

    def must_not_call(args, **kw):
        raise AssertionError("vastai start should not be called when already loading")

    monkeypatch.setattr(restart_cli.vastai_cli, "run_vastai", must_not_call)

    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 0
    assert "already loading" in result.stdout.lower()


# ----- label mismatch ---------------------------------------------------- #

def test_label_mismatch_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    inst = {"id": 555, "actual_status": "exited", "label": "other-label"}
    monkeypatch.setattr(restart_cli.instances, "find_instance", lambda iid: inst)

    def must_not_call(args, **kw):
        raise AssertionError("vastai start should not be called on label mismatch")

    monkeypatch.setattr(restart_cli.vastai_cli, "run_vastai", must_not_call)

    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "other-label" in combined
    assert "training-v1" in combined
    assert "--force" in combined


def test_missing_label_field_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    inst = {"id": 555, "actual_status": "exited"}
    monkeypatch.setattr(restart_cli.instances, "find_instance", lambda iid: inst)
    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "--force" in combined


# ----- --force skips label check ----------------------------------------- #

def test_force_skips_label_check(patch_restart: dict[str, Any]) -> None:
    # The fixture's instance has label "training-v1" but we'll pass a different label
    # under --force; behaviour is to start anyway.
    result = runner.invoke(restart_cli.app, ["555", "any-label", "--force"])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    assert "Instance 555 restarted" in result.stdout
    assert patch_restart["start_argv"] == ["start", "instance", "555"]


# ----- start failure ----------------------------------------------------- #

def test_start_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, patch_restart: dict[str, Any]
) -> None:
    def fake_run_vastai(args, **kw):
        return 1, "", "start failed: instance offline"

    monkeypatch.setattr(restart_cli.vastai_cli, "run_vastai", fake_run_vastai)
    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "start" in combined.lower()


def test_start_empty_stdout_exits_1(
    monkeypatch: pytest.MonkeyPatch, patch_restart: dict[str, Any]
) -> None:
    def fake_run_vastai(args, **kw):
        return 0, "", ""

    monkeypatch.setattr(restart_cli.vastai_cli, "run_vastai", fake_run_vastai)
    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 1


# ----- boot timeout ------------------------------------------------------ #

def test_boot_timeout_exits_1(
    monkeypatch: pytest.MonkeyPatch, patch_restart: dict[str, Any]
) -> None:
    def boom(inst_id):
        raise boot.BootFailure(inst_id, "timeout")

    monkeypatch.setattr(restart_cli.boot, "wait_for_boot", boom)
    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "555" in combined
    assert "vastrun-status" in combined or "vastrun-destroy" in combined


# ----- ssh endpoint None ------------------------------------------------- #

def test_ssh_endpoint_none_exits_1(
    monkeypatch: pytest.MonkeyPatch, patch_restart: dict[str, Any]
) -> None:
    monkeypatch.setattr(restart_cli.ssh, "resolve_ssh_endpoint", lambda iid: None)
    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "555" in combined
    assert "SSH info missing" in combined or "SSH info" in combined


# ----- wait_for_ssh False ------------------------------------------------ #

def test_wait_for_ssh_false_exits_1(
    monkeypatch: pytest.MonkeyPatch, patch_restart: dict[str, Any]
) -> None:
    monkeypatch.setattr(restart_cli.ssh, "wait_for_ssh", lambda h, p: False)
    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "555" in combined
    assert "SSH unreachable" in combined
    assert "ssh5.vast.ai:22000" in combined


# ----- happy path -------------------------------------------------------- #

def test_happy_path_prints_restarted(patch_restart: dict[str, Any]) -> None:
    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    out = result.stdout
    assert "Instance 555 restarted" in out
    assert "SSH: ssh -p 22000 root@ssh5.vast.ai" in out
    assert "Label: training-v1" in out
    # vastai start was called.
    assert patch_restart["start_argv"] == ["start", "instance", "555"]


# ----- marker is NEVER rewritten by restart ------------------------------ #

def test_marker_not_rewritten_in_happy_path(patch_restart: dict[str, Any]) -> None:
    result = runner.invoke(restart_cli.app, ["555", "training-v1"])
    assert result.exit_code == 0
    assert patch_restart["marker_writes"] == []


def test_marker_not_rewritten_under_force(patch_restart: dict[str, Any]) -> None:
    result = runner.invoke(restart_cli.app, ["555", "any-label", "--force"])
    assert result.exit_code == 0
    assert patch_restart["marker_writes"] == []
