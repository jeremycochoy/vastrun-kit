"""SSH helpers — endpoint resolution, exec, wait-for-ssh, key attach, autodiscover.

All vastai calls go through `vastai_cli` (single chokepoint). Local SSH probes go
through `subprocess.run`; the constructed argv always carries StrictHostKeyChecking=no
and a short ConnectTimeout so we never hang on a dead host or get blocked on a
known_hosts mismatch when the SSH proxy IP rotates.
"""

from __future__ import annotations

import base64
import subprocess
import time
from pathlib import Path

from . import config, errors, vastai_cli

_SSH_OPTS: tuple[str, ...] = (
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", f"ConnectTimeout={config.SSH_CONNECT_TIMEOUT_SECONDS}",
)


def parse_ssh_endpoint(inst: dict) -> tuple[str, int] | None:
    """Resolve (host, port). Try `ssh_host` + `ssh_port`, then `public_ipaddr` +
    `ports['22/tcp'][0]['HostPort']`. Return None if both routes fail."""
    host, port = inst.get("ssh_host"), inst.get("ssh_port")
    if host and port:
        return str(host), int(port)
    ip = inst.get("public_ipaddr")
    mapping = (inst.get("ports") or {}).get("22/tcp") or []
    if ip and mapping:
        hp = mapping[0].get("HostPort")
        if hp:
            return str(ip), int(hp)
    return None


def get_ssh_info(inst_id: int) -> tuple[str, int] | None:
    """`vastai show instance <id> --raw` → parse_ssh_endpoint."""
    inst = vastai_cli.run_vastai_raw(["show", "instance", str(inst_id)])
    return parse_ssh_endpoint(inst) if isinstance(inst, dict) else None


def get_ssh_url_fallback(inst_id: int) -> tuple[str, int] | None:
    """`vastai ssh-url <id>` (NOT --raw — it prints `ssh://root@host:port`)."""
    rc, out, _ = vastai_cli.run_vastai(["ssh-url", str(inst_id)])
    if rc != 0:
        return None
    s = out.strip()
    prefix = "ssh://root@"
    if not s.startswith(prefix):
        return None
    rest = s[len(prefix):]
    host, _, port = rest.partition(":")
    if not host or not port.isdigit():
        return None
    return host, int(port)


def resolve_ssh_endpoint(inst_id: int) -> tuple[str, int] | None:
    """get_ssh_info → get_ssh_url_fallback. None if both fail."""
    return get_ssh_info(inst_id) or get_ssh_url_fallback(inst_id)


def _read_pubkey(p: Path) -> str:
    content = p.read_text().strip()
    if not content:
        raise ValueError(f"SSH pubkey file is empty: {p}")
    return content


def autodiscover_ssh_pubkey(explicit: str | None = None) -> str:
    """Return contents of the chosen SSH public key (stripped).

    Order: explicit path → SSH_PUBKEY_CANDIDATES. Empty file → ValueError.
    Nothing found → FileNotFoundError naming what was tried."""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"SSH pubkey not found: {p}")
        return _read_pubkey(p)
    for cand in config.SSH_PUBKEY_CANDIDATES:
        p = Path(cand).expanduser()
        if p.is_file():
            return _read_pubkey(p)
    raise FileNotFoundError(
        "No SSH public key found. Tried: " + ", ".join(config.SSH_PUBKEY_CANDIDATES)
    )


def ssh_exec(
    host: str,
    port: int,
    command: str,
    *,
    timeout: int = config.SSH_PROBE_TIMEOUT_SECONDS,
    stream: bool = False,
) -> tuple[int, str, str]:
    """Run a shell command over SSH, base64-wrapped so Vast.ai's SSH proxy doesn't
    mangle compound commands. Returns (rc, stdout, stderr); stream=True inherits the
    parent terminal and returns empty captured strings."""
    b64 = base64.b64encode(command.encode()).decode()
    payload = f"echo '{b64}' | base64 -d | bash"
    cmd = ["ssh", *_SSH_OPTS, "-p", str(port), f"root@{host}", payload]
    kwargs: dict[str, object] = {"text": True, "timeout": timeout}
    if not stream:
        kwargs["capture_output"] = True
    p = subprocess.run(cmd, **kwargs)
    if stream:
        return p.returncode, "", ""
    return p.returncode, p.stdout or "", p.stderr or ""


def wait_for_ssh(host: str, port: int) -> bool:
    """SSH_READY_RETRIES × SSH_READY_DELAY_SECONDS attempts of `ssh_exec(host, port, 'true')`."""
    for _ in range(config.SSH_READY_RETRIES):
        rc, _, _ = ssh_exec(host, port, "true")
        if rc == 0:
            return True
        time.sleep(config.SSH_READY_DELAY_SECONDS)
    return False


def attach_ssh_key(inst_id: int, ssh_pubkey: str) -> None:
    """`vastai attach ssh <id> <pubkey>`. Retry up to ATTACH_SSH_RETRIES on:
      - non-zero exit, OR
      - stdout containing case-insensitive `'success': false`."""
    last_out, last_err = "", ""
    for attempt in range(config.ATTACH_SSH_RETRIES):
        rc, out, err = vastai_cli.run_vastai(["attach", "ssh", str(inst_id), ssh_pubkey])
        last_out, last_err = out, err
        failed = rc != 0 or "'success': false" in out.lower()
        if not failed:
            return
        if attempt < config.ATTACH_SSH_RETRIES - 1:
            time.sleep(config.ATTACH_SSH_BACKOFF_SECONDS)
    raise errors.VastaiCliError(
        f"vastai attach ssh {inst_id} failed after {config.ATTACH_SSH_RETRIES} attempts. "
        f"stdout={last_out.strip()!r} stderr={last_err.strip()!r}"
    )
