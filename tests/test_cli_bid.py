"""Tests for `vastrun-bid`.

Behavioural contract from SPEC §"vastrun-bid":
- NEW_BID must be > 0; otherwise exit 1.
- Look up the instance via `instances.find_instance`. None → exit 1.
- Warn (but proceed) if the instance is on-demand (not spot).
- Resolve SSH; None → exit 1 with "SSH unreachable — cannot verify ownership".
- Read marker; None → exit 1 with "no marker — refusing".
- Marker hostname mismatch → exit 1 naming both hosts.
- Call `vastai change bid <id> --price <new_bid>`. Non-zero → exit 1.
- On success: print `Bid for instance <id> set to $<new_bid>/h.`.
"""

from __future__ import annotations

import os
import socket

import pytest
from typer.testing import CliRunner

from vastrun_kit import marker
from vastrun_kit.cli import bid as bid_cli

runner = CliRunner()


# ----- helpers ------------------------------------------------------------- #


def _make_other_host_marker() -> marker.Marker:
    return marker.Marker(
        hostname="other-host",
        label="x",
        pid=1,
        created_at="2026-01-01T00:00:00",
    )


def _make_my_marker() -> marker.Marker:
    return marker.Marker(
        hostname=socket.gethostname(),
        label="x",
        pid=os.getpid(),
        created_at="2026-01-01T00:00:00",
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    inst: dict | None = None,
    endpoint: tuple[str, int] | None = ("h.example", 22),
    read: marker.Marker | None | BaseException = None,
    bid_rc: int = 0,
    bid_err: str = "",
) -> dict[str, list]:
    seen: dict[str, list] = {
        "find": [],
        "endpoint": [],
        "read": [],
        "bid_call": [],
    }

    def fake_find(inst_id: int):
        seen["find"].append(inst_id)
        return inst

    def fake_resolve(inst_id: int):
        seen["endpoint"].append(inst_id)
        return endpoint

    def fake_read(host, port):
        seen["read"].append((host, port))
        if isinstance(read, BaseException):
            raise read
        return read

    def fake_run(args, **kw):
        seen["bid_call"].append(list(args))
        return bid_rc, "", bid_err

    monkeypatch.setattr(bid_cli.instances, "find_instance", fake_find)
    monkeypatch.setattr(bid_cli.ssh, "resolve_ssh_endpoint", fake_resolve)
    monkeypatch.setattr(bid_cli.marker, "read_marker", fake_read)
    monkeypatch.setattr(bid_cli.vastai_cli, "run_vastai", fake_run)
    return seen


# ----- NEW_BID validation ------------------------------------------------- #


def test_new_bid_zero_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch)
    result = runner.invoke(bid_cli.app, ["12345", "0"])
    assert result.exit_code == 1
    assert "NEW_BID" in result.stderr or "> 0" in result.stderr
    # Nothing else got called
    assert seen["find"] == []
    assert seen["bid_call"] == []


def test_new_bid_negative_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch)
    # `--` ends option parsing so Click doesn't treat -0.5 as a flag.
    result = runner.invoke(bid_cli.app, ["12345", "--", "-0.5"])
    assert result.exit_code == 1
    assert seen["find"] == []
    assert seen["bid_call"] == []


# ----- instance not found ------------------------------------------------- #


def test_instance_not_found_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch, inst=None)
    result = runner.invoke(bid_cli.app, ["12345", "0.85"])
    assert result.exit_code == 1
    assert "12345" in result.stderr
    assert "not found" in result.stderr.lower()
    # No SSH probe, no bid call
    assert seen["endpoint"] == []
    assert seen["bid_call"] == []


# ----- on-demand warning then proceeds ----------------------------------- #


def test_on_demand_warns_but_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock find_instance to return {"is_bid": False}; warning must print but the
    code must continue (marker check still applies)."""
    inst = {"id": 12345, "is_bid": False}
    seen = _patch(
        monkeypatch,
        inst=inst,
        read=_make_my_marker(),
        bid_rc=0,
    )
    result = runner.invoke(bid_cli.app, ["12345", "0.85"])
    assert result.exit_code == 0
    assert "on-demand" in result.stdout.lower() or "on-demand" in (
        result.stderr or ""
    ).lower()
    # Despite warning, vastai change bid was issued
    assert seen["bid_call"] == [
        ["change", "bid", "12345", "--price", "0.85"]
    ]


def test_on_demand_marker_check_still_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    """On-demand warning must NOT skip the marker check."""
    seen = _patch(
        monkeypatch,
        inst={"id": 12345, "is_bid": False},
        read=None,  # marker missing
    )
    result = runner.invoke(bid_cli.app, ["12345", "0.85"])
    assert result.exit_code == 1
    # Marker missing → refuse
    assert "marker" in result.stderr.lower()
    assert seen["bid_call"] == []


# ----- SSH endpoint missing ----------------------------------------------- #


def test_ssh_endpoint_none_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(
        monkeypatch,
        inst={"id": 12345, "is_bid": True, "min_bid": 0.5},
        endpoint=None,
    )
    result = runner.invoke(bid_cli.app, ["12345", "0.85"])
    assert result.exit_code == 1
    assert "SSH" in result.stderr
    assert "ownership" in result.stderr.lower()
    assert seen["bid_call"] == []


# ----- marker missing ----------------------------------------------------- #


def test_marker_missing_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(
        monkeypatch,
        inst={"id": 12345, "is_bid": True, "min_bid": 0.5},
        read=None,
    )
    result = runner.invoke(bid_cli.app, ["12345", "0.85"])
    assert result.exit_code == 1
    assert "12345" in result.stderr
    assert "marker" in result.stderr.lower()
    assert "UNCLAIMED" in result.stderr
    # No bid call
    assert seen["bid_call"] == []


# ----- marker hostname mismatch ------------------------------------------ #


def test_marker_other_host_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(
        monkeypatch,
        inst={"id": 12345, "is_bid": True, "min_bid": 0.5},
        read=_make_other_host_marker(),
    )
    result = runner.invoke(bid_cli.app, ["12345", "0.85"])
    assert result.exit_code == 1
    err = result.stderr
    assert "other-host" in err
    assert socket.gethostname() in err
    assert seen["bid_call"] == []


# ----- happy path: spot, marker matches ----------------------------------- #


def test_happy_path_spot_change_bid(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(
        monkeypatch,
        inst={"id": 12345, "is_bid": True, "min_bid": 0.5},
        read=_make_my_marker(),
        bid_rc=0,
    )
    result = runner.invoke(bid_cli.app, ["12345", "0.85"])
    assert result.exit_code == 0
    # Right argv
    assert seen["bid_call"] == [
        ["change", "bid", "12345", "--price", "0.85"]
    ]
    # Output contains the new bid in $/h
    assert "12345" in result.stdout
    assert "0.85" in result.stdout
    assert "$" in result.stdout
    assert "/h" in result.stdout
    # No on-demand warning
    assert "on-demand" not in result.stdout.lower()


# ----- vastai change bid non-zero ---------------------------------------- #


def test_vastai_change_bid_non_zero_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(
        monkeypatch,
        inst={"id": 12345, "is_bid": True, "min_bid": 0.5},
        read=_make_my_marker(),
        bid_rc=2,
        bid_err="API rejected the new price",
    )
    result = runner.invoke(bid_cli.app, ["12345", "0.85"])
    assert result.exit_code == 1
    # The bid call did happen, then we surfaced the error
    assert len(seen["bid_call"]) == 1
    assert "API rejected" in result.stderr or "rejected" in result.stderr
