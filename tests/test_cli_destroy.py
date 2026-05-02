"""Tests for `vastrun-destroy`.

Behavioural contract (from SPEC §"vastrun-destroy"):

- No args / no `--all` → exit 1 with the spec'd error.
- Single ID without LABEL nor `--force` → exit 1 with the
  "requires both arguments" error.
- Single ID + LABEL: SSH unreachable / missing-marker / hostname mismatch /
  label mismatch → exit 1 with the spec'd messages.
- Single ID + LABEL with marker match → destroy + verify; verified = exit 0
  and summary printed; unverified = exit 1 + warning to stderr.
- Single ID + `--force` (no LABEL) → no marker check; destroy + verify.
- `--all` without `--force` → cross-tenant warning, exit 1.
- `--all` + `--force` → enumerate all instances, destroy each, exit 0 if
  every destroy verified, 1 if any didn't.
"""

from __future__ import annotations

import socket

import pytest
from typer.testing import CliRunner

from vastrun_kit import marker
from vastrun_kit.cli import destroy as destroy_cli

runner = CliRunner()


# ----- helpers ----------------------------------------------------------- #


def _snap(**overrides: object) -> dict:
    base: dict = {
        "id": 12345,
        "label": "training-v1",
        "actual_status": "running",
        "gpu_name": "RTX 4090",
        "num_gpus": 2,
        "start_date": 1_700_000_000.0,
        "dph_total": 0.5,
    }
    base.update(overrides)
    return base


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    snap: dict | None = None,
    endpoint: tuple[str, int] | None = ("h.example", 22),
    marker_obj: object = "unset",
    marker_raises: BaseException | None = None,
    verify: bool = True,
) -> dict:
    """Stub the destroy CLI's collaborators."""
    seen: dict = {
        "find": [],
        "endpoint": [],
        "read_marker": [],
        "destroy_argv": [],
        "verify": [],
        "list": 0,
    }

    def fake_find(inst_id: int) -> dict | None:
        seen["find"].append(inst_id)
        return snap

    def fake_resolve(inst_id: int) -> tuple[str, int] | None:
        seen["endpoint"].append(inst_id)
        return endpoint

    def fake_read_marker(host: str, port: int) -> object:
        seen["read_marker"].append((host, port))
        if marker_raises is not None:
            raise marker_raises
        return None if marker_obj == "unset" else marker_obj

    def fake_run_vastai(args: list[str], **kw: object) -> tuple[int, str, str]:
        seen["destroy_argv"].append(list(args))
        return 0, "", ""

    def fake_verify(inst_id: int) -> bool:
        seen["verify"].append(inst_id)
        return verify

    monkeypatch.setattr(destroy_cli.instances, "find_instance", fake_find)
    monkeypatch.setattr(destroy_cli.ssh, "resolve_ssh_endpoint", fake_resolve)
    monkeypatch.setattr(destroy_cli.marker, "read_marker", fake_read_marker)
    monkeypatch.setattr(destroy_cli.vastai_cli, "run_vastai", fake_run_vastai)
    monkeypatch.setattr(destroy_cli.destroy, "verify_destroyed", fake_verify)
    return seen


def _me() -> str:
    return socket.gethostname()


# ----- helptext --------------------------------------------------------- #


def test_help_does_not_show_force(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hidden `--force` flag must NOT appear in `--help`."""
    result = runner.invoke(destroy_cli.app, ["--help"])
    assert result.exit_code == 0
    assert "--force" not in result.stdout


def test_help_shows_positionals_and_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both INSTANCE_ID and LABEL appear as optional positionals; --all is listed."""
    result = runner.invoke(destroy_cli.app, ["--help"])
    assert result.exit_code == 0
    # Both INSTANCE_ID and LABEL appear (even if optional, due to --all form)
    assert "INSTANCE_ID" in result.stdout.upper()
    assert "LABEL" in result.stdout.upper()
    assert "--all" in result.stdout
    # No phantom subcommand slot
    assert "COMMAND [ARGS]" not in result.stdout


# ----- no-args refusal -------------------------------------------------- #


def test_no_args_no_all_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_common(monkeypatch, snap=None)
    result = runner.invoke(destroy_cli.app, [])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "INSTANCE_ID" in combined
    assert "LABEL" in combined
    assert "--all" in combined
    # Nothing destroyed
    assert seen["destroy_argv"] == []


# ----- single ID, no label, no force ----------------------------------- #


def test_single_id_no_label_no_force_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_common(monkeypatch, snap=_snap())
    result = runner.invoke(destroy_cli.app, ["12345"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "requires both arguments" in combined
    assert "--force" in combined
    assert seen["destroy_argv"] == []


# ----- single ID + label, marker missing ------------------------------- #


def test_marker_missing_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Marker is None → 'no marker — provisioned outside vastrun-kit'."""
    seen = _patch_common(monkeypatch, snap=_snap(), marker_obj=None)
    result = runner.invoke(destroy_cli.app, ["12345", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "12345" in combined
    assert "no marker" in combined
    assert "--force" in combined
    assert seen["destroy_argv"] == []


# ----- single ID + label, hostname mismatch ----------------------------- #


def test_marker_hostname_mismatch_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    other_marker = marker.Marker(
        hostname="other-host", label="training-v1", pid=1, created_at="2026-01-01T00:00:00"
    )
    seen = _patch_common(monkeypatch, snap=_snap(), marker_obj=other_marker)
    result = runner.invoke(destroy_cli.app, ["12345", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "other-host" in combined  # found
    assert _me() in combined  # expected
    assert "--force" in combined
    assert seen["destroy_argv"] == []


# ----- single ID + label, label mismatch ------------------------------- #


def test_marker_label_mismatch_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    my_marker = marker.Marker(
        hostname=_me(), label="other-label", pid=1, created_at="2026-01-01T00:00:00"
    )
    seen = _patch_common(monkeypatch, snap=_snap(), marker_obj=my_marker)
    result = runner.invoke(destroy_cli.app, ["12345", "expected-label"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "other-label" in combined  # found
    assert "expected-label" in combined  # provided
    assert "--force" in combined
    assert seen["destroy_argv"] == []


# ----- single ID + label, SSH unreachable ------------------------------ #


def test_ssh_endpoint_none_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """`resolve_ssh_endpoint` → None means we cannot verify ownership."""
    seen = _patch_common(monkeypatch, snap=_snap(), endpoint=None)
    result = runner.invoke(destroy_cli.app, ["12345", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "12345" in combined
    assert "SSH unreachable" in combined
    assert "--force" in combined
    assert seen["destroy_argv"] == []


def test_read_marker_raises_treated_as_ssh_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `read_marker` raises (e.g. OSError), surface as SSH unreachable."""
    seen = _patch_common(
        monkeypatch, snap=_snap(), marker_raises=OSError("connection refused")
    )
    result = runner.invoke(destroy_cli.app, ["12345", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "SSH unreachable" in combined
    assert seen["destroy_argv"] == []


# ----- single ID + label, instance not found --------------------------- #


def test_instance_not_found_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_common(monkeypatch, snap=None)
    result = runner.invoke(destroy_cli.app, ["77777", "v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "77777" in combined
    assert "not found" in combined.lower()
    assert seen["destroy_argv"] == []


# ----- single ID + label, marker matches → destroy --------------------- #


def test_marker_matches_destroys_and_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    my_marker = marker.Marker(
        hostname=_me(), label="training-v1", pid=1, created_at="2026-01-01T00:00:00"
    )
    seen = _patch_common(monkeypatch, snap=_snap(), marker_obj=my_marker, verify=True)
    result = runner.invoke(destroy_cli.app, ["12345", "training-v1"])
    assert result.exit_code == 0
    # Destroy invoked exactly once with the right argv
    assert seen["destroy_argv"] == [["destroy", "instance", "12345"]]
    # Verification ran for this id
    assert seen["verify"] == [12345]
    # Summary in stdout names the id
    assert "Destroyed instance 12345" in result.stdout


def test_marker_matches_unverified_warning_exits_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    my_marker = marker.Marker(
        hostname=_me(), label="training-v1", pid=1, created_at="2026-01-01T00:00:00"
    )
    seen = _patch_common(monkeypatch, snap=_snap(), marker_obj=my_marker, verify=False)
    result = runner.invoke(destroy_cli.app, ["12345", "training-v1"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "WARNING" in combined
    assert "12345" in combined
    assert seen["destroy_argv"] == [["destroy", "instance", "12345"]]


# ----- single ID + --force (no label) ---------------------------------- #


def test_force_skips_marker_check_and_destroys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_common(monkeypatch, snap=_snap(), marker_obj=None, verify=True)
    result = runner.invoke(destroy_cli.app, ["12345", "--force"])
    assert result.exit_code == 0
    # No SSH / marker reads
    assert seen["read_marker"] == []
    # Destroy invoked
    assert seen["destroy_argv"] == [["destroy", "instance", "12345"]]
    assert "Destroyed instance 12345" in result.stdout


def test_force_with_unknown_id_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--force` is exactly for the case where snap is unavailable; we still
    issue a destroy and verify."""
    seen = _patch_common(monkeypatch, snap=None, verify=True)
    result = runner.invoke(destroy_cli.app, ["12345", "--force"])
    assert result.exit_code == 0
    assert seen["destroy_argv"] == [["destroy", "instance", "12345"]]


def test_force_with_label_still_skips_marker_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with LABEL passed, --force skips marker verification."""
    other_marker = marker.Marker(
        hostname="other-host", label="zzz", pid=1, created_at="2026-01-01T00:00:00"
    )
    seen = _patch_common(monkeypatch, snap=_snap(), marker_obj=other_marker, verify=True)
    result = runner.invoke(destroy_cli.app, ["12345", "any-label", "--force"])
    assert result.exit_code == 0
    # No marker call
    assert seen["read_marker"] == []
    assert seen["destroy_argv"] == [["destroy", "instance", "12345"]]


# ----- --all without --force ------------------------------------------- #


def test_all_without_force_prints_warning_and_exits_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--all` alone always errors — never destroys."""
    seen = _patch_common(monkeypatch, snap=_snap())

    def must_not_be_called() -> list:
        raise AssertionError("list_instances should not run for --all without --force")

    monkeypatch.setattr(destroy_cli.instances, "list_instances", must_not_be_called)
    result = runner.invoke(destroy_cli.app, ["--all"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "disabled by default" in combined
    assert "vastrun-destroy <ID> <LABEL>" in combined
    assert "--force" in combined
    assert seen["destroy_argv"] == []


# ----- --all + --force ------------------------------------------------- #


def test_all_force_destroys_each_and_exits_0_if_all_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_snap(id=1, label="a"), _snap(id=2, label="b")]

    seen: dict = {"destroy_argv": [], "verify_ids": []}

    monkeypatch.setattr(destroy_cli.instances, "list_instances", lambda: rows)

    def fake_run_vastai(args: list[str], **kw: object) -> tuple[int, str, str]:
        seen["destroy_argv"].append(list(args))
        return 0, "", ""

    def fake_verify(inst_id: int) -> bool:
        seen["verify_ids"].append(inst_id)
        return True

    monkeypatch.setattr(destroy_cli.vastai_cli, "run_vastai", fake_run_vastai)
    monkeypatch.setattr(destroy_cli.destroy, "verify_destroyed", fake_verify)

    result = runner.invoke(destroy_cli.app, ["--all", "--force"])
    assert result.exit_code == 0
    # Each instance destroyed
    assert seen["destroy_argv"] == [
        ["destroy", "instance", "1"],
        ["destroy", "instance", "2"],
    ]
    assert seen["verify_ids"] == [1, 2]
    # Each row's heads-up line shows up
    out = result.stdout + (result.stderr or "")
    assert "1" in out and "2" in out
    assert out.count("destroyed") >= 2  # status confirmations


def test_all_force_one_unverified_exits_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_snap(id=1, label="a"), _snap(id=2, label="b")]

    monkeypatch.setattr(destroy_cli.instances, "list_instances", lambda: rows)
    monkeypatch.setattr(destroy_cli.vastai_cli, "run_vastai", lambda *a, **kw: (0, "", ""))
    # First verifies, second doesn't
    verify_results = iter([True, False])
    monkeypatch.setattr(
        destroy_cli.destroy, "verify_destroyed", lambda _id: next(verify_results)
    )

    result = runner.invoke(destroy_cli.app, ["--all", "--force"])
    assert result.exit_code == 1
    out = result.stdout + (result.stderr or "")
    assert "WARNING" in out or "not confirmed" in out


def test_all_force_empty_list_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(destroy_cli.instances, "list_instances", lambda: [])

    def must_not_be_called(*a: object, **kw: object) -> object:
        raise AssertionError("destroy must not run when list is empty")

    monkeypatch.setattr(destroy_cli.vastai_cli, "run_vastai", must_not_be_called)
    monkeypatch.setattr(destroy_cli.destroy, "verify_destroyed", must_not_be_called)
    result = runner.invoke(destroy_cli.app, ["--all", "--force"])
    assert result.exit_code == 0
