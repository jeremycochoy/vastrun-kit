"""Tests for `vastrun-rename`.

Behavioural contract from SPEC §"vastrun-rename":
- Resolve SSH endpoint via `ssh.resolve_ssh_endpoint`. None → exit 1, "not found".
- Read marker via `marker.read_marker`.
- UNCLAIMED + no `--force` → exit 1; message names "UNCLAIMED" and "--force".
- UNCLAIMED + `--force` → write fresh marker (hostname=this host) and call
  `vastai label instance <id> <NEW_LABEL>`; print "Claimed UNCLAIMED ...".
- Marker hostname mismatch → exit 1; --force does NOT override another host's
  marker. Do NOT write anything.
- Marker hostname match → rewrite marker preserving `created_at`; print
  `Owner marker label: '<old>' → '<new>'.`; then call `vastai label instance`.
- `vastai label` non-zero → exit 1 with the manual retry command on stderr.
"""

from __future__ import annotations

import os
import socket

import pytest
from typer.testing import CliRunner

from vastrun_kit import marker
from vastrun_kit.cli import rename as rename_cli

runner = CliRunner()


# ----- helpers ------------------------------------------------------------- #


def _make_other_host_marker(label: str = "old-label") -> marker.Marker:
    return marker.Marker(
        hostname="other-host",
        label=label,
        pid=1234,
        created_at="2026-01-01T00:00:00",
    )


def _make_my_marker(label: str = "old-label") -> marker.Marker:
    return marker.Marker(
        hostname=socket.gethostname(),
        label=label,
        pid=os.getpid(),
        created_at="2026-01-01T00:00:00",
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    endpoint: tuple[str, int] | None = ("h.example", 22),
    read: marker.Marker | None | BaseException = None,
    label_rc: int = 0,
    label_err: str = "",
) -> dict[str, list]:
    seen: dict[str, list] = {
        "endpoint": [],
        "read": [],
        "write": [],
        "label_call": [],
    }

    def fake_resolve(inst_id: int):
        seen["endpoint"].append(inst_id)
        return endpoint

    def fake_read(host, port):
        seen["read"].append((host, port))
        if isinstance(read, BaseException):
            raise read
        return read

    def fake_write(host, port, m):
        seen["write"].append({"host": host, "port": port, "marker": m})

    def fake_run(args, **kw):
        seen["label_call"].append(list(args))
        return label_rc, "", label_err

    monkeypatch.setattr(rename_cli.ssh, "resolve_ssh_endpoint", fake_resolve)
    monkeypatch.setattr(rename_cli.marker, "read_marker", fake_read)
    monkeypatch.setattr(rename_cli.marker, "write_marker", fake_write)
    monkeypatch.setattr(rename_cli.vastai_cli, "run_vastai", fake_run)
    return seen


# ----- SSH endpoint missing ----------------------------------------------- #


def test_ssh_endpoint_none_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch, endpoint=None)
    result = runner.invoke(rename_cli.app, ["12345", "new-label"])
    assert result.exit_code == 1
    assert "12345" in result.stderr
    assert "not found" in result.stderr.lower()
    # Must not read marker, must not write, must not call vastai label
    assert seen["read"] == []
    assert seen["write"] == []
    assert seen["label_call"] == []


# ----- UNCLAIMED + no --force --------------------------------------------- #


def test_unclaimed_without_force_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch, read=None)
    result = runner.invoke(rename_cli.app, ["12345", "new-label"])
    assert result.exit_code == 1
    err = result.stderr
    assert "12345" in err
    assert "UNCLAIMED" in err
    assert "--force" in err
    # Must not write the marker; must not call vastai label
    assert seen["write"] == []
    assert seen["label_call"] == []


# ----- UNCLAIMED + --force claims ----------------------------------------- #


def test_unclaimed_with_force_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch, read=None, label_rc=0)
    result = runner.invoke(rename_cli.app, ["12345", "new-label", "--force"])
    assert result.exit_code == 0
    # write_marker called once
    assert len(seen["write"]) == 1
    written = seen["write"][0]
    written_marker = written["marker"]
    assert written_marker.hostname == socket.gethostname()
    assert written_marker.label == "new-label"
    # vastai label was called next with the right argv
    assert len(seen["label_call"]) == 1
    assert seen["label_call"][0] == ["label", "instance", "12345", "new-label"]
    # Output mentions "Claimed UNCLAIMED" and the new label
    assert "Claimed UNCLAIMED" in result.stdout
    assert "12345" in result.stdout
    assert "new-label" in result.stdout


# ----- Marker hostname mismatch (foreign host) ---------------------------- #


def test_marker_other_host_refuses_even_with_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = _make_other_host_marker()
    seen = _patch(monkeypatch, read=other)
    # Even with --force, the cross-host case must refuse.
    result = runner.invoke(
        rename_cli.app, ["12345", "new-label", "--force"]
    )
    assert result.exit_code == 1
    err = result.stderr
    # Names BOTH hosts
    assert "other-host" in err
    assert socket.gethostname() in err
    # Must not write, must not call vastai label
    assert seen["write"] == []
    assert seen["label_call"] == []


def test_marker_other_host_without_force_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = _make_other_host_marker()
    seen = _patch(monkeypatch, read=other)
    result = runner.invoke(rename_cli.app, ["12345", "new-label"])
    assert result.exit_code == 1
    err = result.stderr
    assert "other-host" in err
    assert socket.gethostname() in err
    assert seen["write"] == []
    assert seen["label_call"] == []


# ----- Marker hostname match: relabel preserves created_at ---------------- #


def test_marker_match_preserves_created_at(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = _make_my_marker(label="old-label")
    seen = _patch(monkeypatch, read=mine, label_rc=0)
    result = runner.invoke(rename_cli.app, ["12345", "new-label"])
    assert result.exit_code == 0
    # write_marker was called once with new label and PRESERVED created_at
    assert len(seen["write"]) == 1
    new_marker = seen["write"][0]["marker"]
    assert new_marker.label == "new-label"
    assert new_marker.created_at == mine.created_at  # preserved
    assert new_marker.hostname == socket.gethostname()
    # Then vastai label
    assert len(seen["label_call"]) == 1
    assert seen["label_call"][0] == ["label", "instance", "12345", "new-label"]
    # Output names old → new label
    assert "old-label" in result.stdout
    assert "new-label" in result.stdout
    assert "Vast.ai display label" in result.stdout


# ----- vastai label non-zero failure -------------------------------------- #


def test_vastai_label_failure_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = _make_my_marker(label="old-label")
    seen = _patch(
        monkeypatch,
        read=mine,
        label_rc=1,
        label_err="API quota exceeded",
    )
    result = runner.invoke(rename_cli.app, ["12345", "new-label"])
    assert result.exit_code == 1
    # Marker was rewritten before the vastai call
    assert len(seen["write"]) == 1
    # The manual retry command surfaces in stderr
    err = result.stderr
    assert "vastai label instance 12345 new-label" in err


def test_vastai_label_failure_after_force_claim_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If --force claims the UNCLAIMED instance and the subsequent vastai label
    fails, surface the manual retry command and exit 1."""
    seen = _patch(
        monkeypatch,
        read=None,
        label_rc=2,
        label_err="rate limited",
    )
    result = runner.invoke(rename_cli.app, ["12345", "new-label", "--force"])
    assert result.exit_code == 1
    err = result.stderr
    assert "vastai label instance 12345 new-label" in err
