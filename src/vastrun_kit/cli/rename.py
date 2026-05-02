"""`vastrun-rename` — relabel an instance, keeping marker + display label in sync.

Marker rewrite preserves `created_at`. `--force` only claims an UNCLAIMED
instance — it never overrides another host's marker (cross-host ownership is
the safety boundary).
"""

from __future__ import annotations

import socket
import sys

import typer

from .. import marker, ssh, vastai_cli

app = typer.Typer(add_completion=False)


def _emit_label_failure(inst_id: int, new_label: str, err: str) -> None:
    """Print the manual retry command on stderr after a failed `vastai label`."""
    detail = err.strip()
    suffix = f": {detail}" if detail else ""
    print(
        f"Error: vastai label instance {inst_id} {new_label} failed{suffix}. "
        f"Retry manually with: vastai label instance {inst_id} {new_label}",
        file=sys.stderr,
    )


def _set_display_label(inst_id: int, new_label: str) -> bool:
    rc, _, err = vastai_cli.run_vastai(
        ["label", "instance", str(inst_id), new_label]
    )
    if rc != 0:
        _emit_label_failure(inst_id, new_label, err)
        return False
    print(f"Vast.ai display label set to '{new_label}'.")
    return True


@app.command()
def main(
    instance_id: int = typer.Argument(..., help="Instance to relabel."),
    new_label: str = typer.Argument(..., help="New label."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Claim an UNCLAIMED instance (no marker on remote). Does NOT override another host's marker.",
    ),
) -> None:
    """Relabel an instance and keep its on-instance marker + Vast.ai display label in sync."""
    endpoint = ssh.resolve_ssh_endpoint(instance_id)
    if endpoint is None:
        print(f"Error: Instance {instance_id} not found.", file=sys.stderr)
        raise typer.Exit(code=1)
    host, port = endpoint

    me = socket.gethostname()
    m = marker.read_marker(host, port)

    if m is None and not force:
        print(
            f"Error: Instance {instance_id} is UNCLAIMED. "
            "Re-run with --force to claim it.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    if m is None:
        # --force claim of an UNCLAIMED instance
        fresh = marker.make_marker(new_label)
        marker.write_marker(host, port, fresh)
        print(f"Claimed UNCLAIMED instance {instance_id} as '{me}'.")
        if not _set_display_label(instance_id, new_label):
            raise typer.Exit(code=1)
        return

    if m.hostname != me:
        print(
            f"Error: Instance {instance_id} is owned by host '{m.hostname}', "
            f"not '{me}'. --force does not override another host's marker "
            "(cross-host ownership is the safety boundary).",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    # Hostname matches: rewrite marker preserving created_at.
    rewritten = marker.make_marker(new_label, created_at=m.created_at)
    marker.write_marker(host, port, rewritten)
    print(f"Owner marker label: '{m.label}' → '{new_label}'.")
    if not _set_display_label(instance_id, new_label):
        raise typer.Exit(code=1)
