from __future__ import annotations

import base64
import dataclasses
import json
import os
import socket
from datetime import datetime

import pytest

from vastrun_kit import errors, marker


# ----- make_marker --------------------------------------------------------- #


def test_make_marker_uses_real_hostname_and_pid() -> None:
    m = marker.make_marker("training-v1")
    assert m.hostname == socket.gethostname()
    assert m.pid == os.getpid()
    assert m.label == "training-v1"
    # second precision: no microseconds in the rendered string
    assert "." not in m.created_at
    # ISO 8601 round-trips through fromisoformat without exception
    datetime.fromisoformat(m.created_at)


def test_make_marker_respects_created_at_override() -> None:
    m = marker.make_marker("v2", created_at="2026-01-15T12:34:56")
    assert m.created_at == "2026-01-15T12:34:56"
    assert m.label == "v2"
    assert m.hostname == socket.gethostname()


def test_marker_is_frozen_dataclass() -> None:
    m = marker.make_marker("x", created_at="2026-01-01T00:00:00")
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.label = "other"  # type: ignore[misc]


# ----- write_marker -------------------------------------------------------- #


def test_write_marker_base64_encodes_json_and_redirects_to_marker_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_exec(host: str, port: int, command: str, **kw: object) -> tuple[int, str, str]:
        seen["host"] = host
        seen["port"] = port
        seen["command"] = command
        return 0, "", ""

    monkeypatch.setattr(marker.ssh, "ssh_exec", fake_exec)
    m = marker.Marker(
        hostname="my-host",
        label="lbl",
        pid=4242,
        created_at="2026-04-29T08:14:00",
    )
    marker.write_marker("h.example", 22, m)

    assert seen["host"] == "h.example"
    assert seen["port"] == 22
    cmd = seen["command"]
    assert isinstance(cmd, str)
    # Spec: echo '<b64>' | base64 -d > /tmp/vastrun_owner.json
    assert cmd.startswith("echo '")
    assert cmd.endswith(f"' | base64 -d > {marker.MARKER_PATH}")
    # Decode the base64 payload and confirm it round-trips to the marker JSON
    b64 = cmd.split("'", 2)[1]
    payload = json.loads(base64.b64decode(b64).decode())
    assert payload == {
        "hostname": "my-host",
        "label": "lbl",
        "pid": 4242,
        "created_at": "2026-04-29T08:14:00",
    }


def test_write_marker_raises_vastai_cli_error_on_nonzero_rc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_exec(host: str, port: int, command: str, **kw: object) -> tuple[int, str, str]:
        return 1, "", "permission denied"

    monkeypatch.setattr(marker.ssh, "ssh_exec", fake_exec)
    m = marker.make_marker("v1", created_at="2026-04-29T08:14:00")
    with pytest.raises(errors.VastaiCliError) as exc:
        marker.write_marker("h", 22, m)
    msg = str(exc.value)
    assert marker.MARKER_PATH in msg


# ----- read_marker --------------------------------------------------------- #


def test_read_marker_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "hostname": "his-host",
        "label": "training-v1",
        "pid": 99,
        "created_at": "2026-04-29T08:14:00",
    }

    def fake_exec(host: str, port: int, command: str, **kw: object) -> tuple[int, str, str]:
        assert command == f"cat {marker.MARKER_PATH}"
        return 0, json.dumps(payload), ""

    monkeypatch.setattr(marker.ssh, "ssh_exec", fake_exec)
    m = marker.read_marker("h", 22)
    assert m == marker.Marker(
        hostname="his-host",
        label="training-v1",
        pid=99,
        created_at="2026-04-29T08:14:00",
    )


def test_read_marker_missing_file_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_exec(host: str, port: int, command: str, **kw: object) -> tuple[int, str, str]:
        return 1, "", "cat: /tmp/vastrun_owner.json: No such file or directory"

    monkeypatch.setattr(marker.ssh, "ssh_exec", fake_exec)
    assert marker.read_marker("h", 22) is None


def test_read_marker_empty_stdout_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_exec(host: str, port: int, command: str, **kw: object) -> tuple[int, str, str]:
        return 0, "", ""

    monkeypatch.setattr(marker.ssh, "ssh_exec", fake_exec)
    assert marker.read_marker("h", 22) is None


def test_read_marker_corrupt_json_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_exec(host: str, port: int, command: str, **kw: object) -> tuple[int, str, str]:
        return 0, "not json", ""

    monkeypatch.setattr(marker.ssh, "ssh_exec", fake_exec)
    assert marker.read_marker("h", 22) is None


def test_read_marker_round_trip_through_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """write_marker's b64 payload, decoded, must reconstruct the same Marker via read_marker."""
    captured: dict[str, str] = {}

    def fake_write(host: str, port: int, command: str, **kw: object) -> tuple[int, str, str]:
        b64 = command.split("'", 2)[1]
        captured["payload"] = base64.b64decode(b64).decode()
        return 0, "", ""

    monkeypatch.setattr(marker.ssh, "ssh_exec", fake_write)
    original = marker.make_marker("trip", created_at="2026-04-29T08:14:00")
    marker.write_marker("h", 22, original)

    def fake_read(host: str, port: int, command: str, **kw: object) -> tuple[int, str, str]:
        return 0, captured["payload"], ""

    monkeypatch.setattr(marker.ssh, "ssh_exec", fake_read)
    assert marker.read_marker("h", 22) == original


def test_read_marker_propagates_ssh_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSH connection failures must propagate so the caller can render the
    'SSH unreachable — cannot verify ownership' hint."""
    def fake_exec(host: str, port: int, command: str, **kw: object) -> tuple[int, str, str]:
        raise OSError("connection refused")

    monkeypatch.setattr(marker.ssh, "ssh_exec", fake_exec)
    with pytest.raises(OSError, match="connection refused"):
        marker.read_marker("h", 22)


# ----- check_owner --------------------------------------------------------- #


def test_check_owner_none_returns_false() -> None:
    assert marker.check_owner(None, "any-host") is False


def test_check_owner_different_host_returns_false() -> None:
    m = marker.Marker(hostname="other", label="x", pid=1, created_at="2026-01-01T00:00:00")
    assert marker.check_owner(m, "me") is False


def test_check_owner_matching_host_returns_true() -> None:
    m = marker.Marker(hostname="me", label="x", pid=1, created_at="2026-01-01T00:00:00")
    assert marker.check_owner(m, "me") is True


# ----- module surface ------------------------------------------------------ #


def test_marker_path_constant() -> None:
    assert marker.MARKER_PATH == "/tmp/vastrun_owner.json"
