from __future__ import annotations

import base64
from pathlib import Path

import pytest

from vastrun_kit import config, errors, ssh


# ----- parse_ssh_endpoint -------------------------------------------------- #


def test_parse_ssh_endpoint_prefers_ssh_host_port() -> None:
    inst = {
        "ssh_host": "ssh5.vast.ai",
        "ssh_port": 12345,
        "public_ipaddr": "1.2.3.4",
        "ports": {"22/tcp": [{"HostPort": "22000"}]},
    }
    assert ssh.parse_ssh_endpoint(inst) == ("ssh5.vast.ai", 12345)


def test_parse_ssh_endpoint_falls_back_to_public_ip_and_port_map() -> None:
    inst = {
        "public_ipaddr": "1.2.3.4",
        "ports": {"22/tcp": [{"HostPort": "22000"}]},
    }
    assert ssh.parse_ssh_endpoint(inst) == ("1.2.3.4", 22000)


def test_parse_ssh_endpoint_returns_none_when_both_missing() -> None:
    assert ssh.parse_ssh_endpoint({}) is None
    assert ssh.parse_ssh_endpoint({"ssh_host": "x"}) is None  # no port
    assert ssh.parse_ssh_endpoint({"ssh_port": 1}) is None  # no host
    assert ssh.parse_ssh_endpoint({"public_ipaddr": "1.2.3.4"}) is None  # no port map
    assert ssh.parse_ssh_endpoint({"public_ipaddr": "1.2.3.4", "ports": {}}) is None
    assert (
        ssh.parse_ssh_endpoint({"public_ipaddr": "1.2.3.4", "ports": {"22/tcp": []}})
        is None
    )


# ----- get_ssh_info / get_ssh_url_fallback / resolve_ssh_endpoint ---------- #


def test_get_ssh_info_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def fake_raw(args: list[str], **kw: object) -> object:
        captured.append(args)
        return {"ssh_host": "h", "ssh_port": 9}

    monkeypatch.setattr(ssh.vastai_cli, "run_vastai_raw", fake_raw)
    assert ssh.get_ssh_info(42) == ("h", 9)
    assert captured == [["show", "instance", "42"]]


def test_get_ssh_info_returns_none_on_missing_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh.vastai_cli, "run_vastai_raw", lambda args, **kw: {})
    assert ssh.get_ssh_info(42) is None


def test_get_ssh_url_fallback_parses_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def fake(args: list[str], **kw: object) -> tuple[int, str, str]:
        captured.append(args)
        return 0, "ssh://root@ssh5.vast.ai:22000\n", ""

    monkeypatch.setattr(ssh.vastai_cli, "run_vastai", fake)
    assert ssh.get_ssh_url_fallback(7) == ("ssh5.vast.ai", 22000)
    assert captured == [["ssh-url", "7"]]


def test_get_ssh_url_fallback_returns_none_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ssh.vastai_cli, "run_vastai", lambda args, **kw: (1, "", "boom")
    )
    assert ssh.get_ssh_url_fallback(7) is None


def test_get_ssh_url_fallback_returns_none_on_garbled_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ssh.vastai_cli, "run_vastai", lambda args, **kw: (0, "not a url\n", "")
    )
    assert ssh.get_ssh_url_fallback(7) is None


def test_resolve_ssh_endpoint_chains_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh, "get_ssh_info", lambda i: None)
    monkeypatch.setattr(ssh, "get_ssh_url_fallback", lambda i: ("h", 1))
    assert ssh.resolve_ssh_endpoint(1) == ("h", 1)


def test_resolve_ssh_endpoint_returns_none_when_both_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh, "get_ssh_info", lambda i: None)
    monkeypatch.setattr(ssh, "get_ssh_url_fallback", lambda i: None)
    assert ssh.resolve_ssh_endpoint(1) is None


def test_resolve_ssh_endpoint_short_circuits_on_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh, "get_ssh_info", lambda i: ("a", 1))

    def boom(_: int) -> tuple[str, int] | None:
        raise AssertionError("fallback should not be called")

    monkeypatch.setattr(ssh, "get_ssh_url_fallback", boom)
    assert ssh.resolve_ssh_endpoint(1) == ("a", 1)


# ----- autodiscover_ssh_pubkey --------------------------------------------- #


def test_autodiscover_explicit_path(tmp_path: Path) -> None:
    p = tmp_path / "key.pub"
    p.write_text("ssh-ed25519 AAAA explicit\n")
    assert ssh.autodiscover_ssh_pubkey(str(p)) == "ssh-ed25519 AAAA explicit"


def test_autodiscover_explicit_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="missing.pub"):
        ssh.autodiscover_ssh_pubkey(str(tmp_path / "missing.pub"))


def test_autodiscover_explicit_empty_rejected(tmp_path: Path) -> None:
    p = tmp_path / "key.pub"
    p.write_text("   \n")
    with pytest.raises(ValueError, match="empty"):
        ssh.autodiscover_ssh_pubkey(str(p))


def test_autodiscover_fallback_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    # only id_rsa.pub exists — id_ed25519.pub is missing, so the chain skips to it
    (home / ".ssh" / "id_rsa.pub").write_text("ssh-rsa AAAA fallback\n")
    assert ssh.autodiscover_ssh_pubkey() == "ssh-rsa AAAA fallback"


def test_autodiscover_fallback_chain_first_hit_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (home / ".ssh" / "id_ed25519.pub").write_text("ed25519-content\n")
    (home / ".ssh" / "id_rsa.pub").write_text("rsa-content\n")
    assert ssh.autodiscover_ssh_pubkey() == "ed25519-content"


def test_autodiscover_no_candidate_found_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(FileNotFoundError) as exc:
        ssh.autodiscover_ssh_pubkey()
    msg = str(exc.value)
    for cand in config.SSH_PUBKEY_CANDIDATES:
        assert cand in msg


def test_autodiscover_fallback_empty_file_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (home / ".ssh" / "id_ed25519.pub").write_text("\n  \n")
    with pytest.raises(ValueError, match="empty"):
        ssh.autodiscover_ssh_pubkey()


# ----- ssh_exec ------------------------------------------------------------ #


class FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_ssh_exec_wraps_command_in_base64_and_uses_strict_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return FakeProc(0, "hello", "")

    monkeypatch.setattr(ssh.subprocess, "run", fake_run)

    rc, out, err = ssh.ssh_exec("h.example", 22, "echo hi && uptime")
    assert (rc, out, err) == (0, "hello", "")

    cmd = seen["cmd"]
    assert isinstance(cmd, list) and cmd[0] == "ssh"
    # User@host appears
    assert "root@h.example" in cmd
    # Port flag carries the integer
    assert "-p" in cmd and "22" in cmd
    # StrictHostKeyChecking + UserKnownHostsFile + ConnectTimeout flags wired
    joined = " ".join(cmd)
    assert "StrictHostKeyChecking=no" in joined
    assert "UserKnownHostsFile=/dev/null" in joined
    assert f"ConnectTimeout={config.SSH_CONNECT_TIMEOUT_SECONDS}" in joined
    # Last token: the wrapped shell payload
    payload = cmd[-1]
    assert payload.startswith("echo '")
    assert payload.endswith("' | base64 -d | bash")
    # The payload's base64 chunk decodes back to the user command
    b64 = payload.split("'", 2)[1]
    assert base64.b64decode(b64).decode() == "echo hi && uptime"
    # Capture mode: capture_output True, text True
    kw = seen["kwargs"]
    assert kw.get("text") is True
    assert kw.get("capture_output") is True


def test_ssh_exec_stream_mode_does_not_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        seen["kwargs"] = kwargs
        return FakeProc(7, "", "")

    monkeypatch.setattr(ssh.subprocess, "run", fake_run)
    rc, out, err = ssh.ssh_exec("h", 22, "echo", stream=True)
    assert (rc, out, err) == (7, "", "")
    kw = seen["kwargs"]
    assert kw.get("capture_output") is not True


# ----- wait_for_ssh -------------------------------------------------------- #


def test_wait_for_ssh_succeeds_after_a_few_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = {"n": 0}

    def fake_exec(host, port, command, **kw):  # noqa: ANN001
        counter["n"] += 1
        if counter["n"] < 4:
            return 255, "", ""
        return 0, "", ""

    monkeypatch.setattr(ssh, "ssh_exec", fake_exec)
    monkeypatch.setattr(ssh.time, "sleep", lambda _s: None)

    assert ssh.wait_for_ssh("h", 22) is True
    assert counter["n"] == 4


def test_wait_for_ssh_returns_false_after_all_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = {"n": 0}

    def fake_exec(host, port, command, **kw):  # noqa: ANN001
        counter["n"] += 1
        return 255, "", "no route"

    monkeypatch.setattr(ssh, "ssh_exec", fake_exec)
    monkeypatch.setattr(ssh.time, "sleep", lambda _s: None)

    assert ssh.wait_for_ssh("h", 22) is False
    assert counter["n"] == config.SSH_READY_RETRIES


# ----- attach_ssh_key ------------------------------------------------------ #


def test_attach_ssh_key_success_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake(args: list[str], **kw: object) -> tuple[int, str, str]:
        calls.append(args)
        return 0, '{"success": true}', ""

    monkeypatch.setattr(ssh.vastai_cli, "run_vastai", fake)
    monkeypatch.setattr(ssh.time, "sleep", lambda _s: None)

    ssh.attach_ssh_key(123, "ssh-ed25519 AAA")
    assert calls == [["attach", "ssh", "123", "ssh-ed25519 AAA"]]


def test_attach_ssh_key_retries_on_success_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = {"n": 0}

    def fake(args: list[str], **kw: object) -> tuple[int, str, str]:
        counter["n"] += 1
        if counter["n"] == 1:
            return 0, "{'success': False, 'msg': 'try again'}", ""
        return 0, "{'success': true}", ""

    monkeypatch.setattr(ssh.vastai_cli, "run_vastai", fake)
    sleeps: list[float] = []
    monkeypatch.setattr(ssh.time, "sleep", lambda s: sleeps.append(s))

    ssh.attach_ssh_key(123, "PUB")
    assert counter["n"] == 2
    assert sleeps == [config.ATTACH_SSH_BACKOFF_SECONDS]


def test_attach_ssh_key_exhaustion_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = {"n": 0}

    def fake(args: list[str], **kw: object) -> tuple[int, str, str]:
        counter["n"] += 1
        return 0, "{'success': False, 'msg': 'permanent'}", "stderr-blob"

    monkeypatch.setattr(ssh.vastai_cli, "run_vastai", fake)
    monkeypatch.setattr(ssh.time, "sleep", lambda _s: None)

    with pytest.raises(errors.VastaiCliError) as exc:
        ssh.attach_ssh_key(123, "PUB")
    msg = str(exc.value)
    assert "123" in msg
    assert "permanent" in msg
    assert counter["n"] == config.ATTACH_SSH_RETRIES


def test_attach_ssh_key_retries_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = {"n": 0}

    def fake(args: list[str], **kw: object) -> tuple[int, str, str]:
        counter["n"] += 1
        if counter["n"] == 1:
            return 2, "", "transient API hiccup"
        return 0, "{'success': true}", ""

    monkeypatch.setattr(ssh.vastai_cli, "run_vastai", fake)
    monkeypatch.setattr(ssh.time, "sleep", lambda _s: None)

    ssh.attach_ssh_key(123, "PUB")
    assert counter["n"] == 2


def test_attach_ssh_key_already_associated_is_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vast.ai returns `success: False` with msg `SSH key already associated
    with instance.` when the host pre-attaches the user's key. The key IS
    on the instance; we treat this as success — no retry, no raise."""
    calls: list[list[str]] = []

    def fake(args: list[str], **kw: object) -> tuple[int, str, str]:
        calls.append(args)
        return 0, "{'success': False, 'msg': 'SSH key already associated with instance.'}", ""

    monkeypatch.setattr(ssh.vastai_cli, "run_vastai", fake)
    monkeypatch.setattr(ssh.time, "sleep", lambda _s: None)
    ssh.attach_ssh_key(123, "PUB")
    assert len(calls) == 1  # no retry
