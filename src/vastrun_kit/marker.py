"""Ownership marker — read / write / check the on-instance /tmp/vastrun_owner.json.

The marker identifies which workstation+label provisioned (or claimed) an
instance. Cross-host check key for `vastrun-destroy`, `vastrun-rename`,
`vastrun-bid`. See SPEC.md → On-instance state and Multi-tenant > Ownership scope.
"""

from __future__ import annotations

import base64
import json
import os
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from . import errors, ssh

MARKER_PATH = "/tmp/vastrun_owner.json"


@dataclass(frozen=True)
class Marker:
    hostname: str
    label: str
    pid: int
    # ISO 8601 second-precision UTC, e.g. "2026-04-29T08:14:00+00:00".
    created_at: str


def make_marker(label: str, *, created_at: str | None = None) -> Marker:
    """Build a Marker for THIS host. `created_at` is overridable so
    `vastrun-rename` can preserve it across relabels."""
    if created_at is None:
        created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return Marker(
        hostname=socket.gethostname(),
        label=label,
        pid=os.getpid(),
        created_at=created_at,
    )


def write_marker(host: str, port: int, marker: Marker) -> None:
    """Serialize the marker to JSON and write it to MARKER_PATH on the remote.
    Uses base64 echo + base64 -d > path to avoid shell quoting headaches."""
    payload = json.dumps(asdict(marker))
    b64 = base64.b64encode(payload.encode()).decode()
    rc, _, err = ssh.ssh_exec(host, port, f"echo '{b64}' | base64 -d > {MARKER_PATH}")
    if rc != 0:
        raise errors.VastaiCliError(
            f"Failed to write ownership marker at {MARKER_PATH} (rc={rc}, stderr={err.strip()!r})."
        )


def read_marker(host: str, port: int) -> Marker | None:
    """`cat MARKER_PATH` over SSH. Return None on missing file, empty stdout,
    or corrupt JSON — those are all "no marker" / UNCLAIMED per spec. Other
    exceptions (SSH unreachable) propagate so the caller can render the
    'cannot verify ownership' hint."""
    rc, out, _ = ssh.ssh_exec(host, port, f"cat {MARKER_PATH}")
    if rc != 0 or not out.strip():
        return None
    try:
        data = json.loads(out)
        return Marker(**data)
    except (json.JSONDecodeError, TypeError):
        return None


def check_owner(marker: Marker | None, my_hostname: str) -> bool:
    """True iff marker is not None and marker.hostname == my_hostname.
    Label disambiguation is the caller's responsibility."""
    return marker is not None and marker.hostname == my_hostname
