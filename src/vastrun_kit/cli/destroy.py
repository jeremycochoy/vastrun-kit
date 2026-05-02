"""`vastrun-destroy` — single-instance + bulk destroy with verification.

Single-command Typer app. The hidden `--force` flag is only mentioned in
refusal messages: discovering it should require an active decision, never
appearing in `--help` reduces accidental use as the path of least resistance.
"""

from __future__ import annotations

import socket

import typer

from .. import destroy, instances, marker, ssh, vastai_cli

app = typer.Typer(add_completion=False)

_BULK_WARNING = """Error: vastrun-destroy --all is disabled by default — running it would destroy
every instance the API returns, including instances belonging to other agents
or co-resident sessions on this account.

Destroy each of your own instances explicitly with:
    vastrun-destroy <ID> <LABEL>
for the instances you provisioned (use vastrun-status to list them — the Label
column shows each instance's owner-marker label).

If you really mean "destroy everything on this account, regardless of who owns
it", re-run with --force."""


def _err(msg: str) -> None:
    typer.echo(msg, err=True)


def _fail(msg: str) -> typer.Exit:
    _err(msg)
    return typer.Exit(code=1)


def _destroy_one(inst_id: int) -> bool:
    """Issue destroy + verify. Returns True if verified."""
    vastai_cli.run_vastai(["destroy", "instance", str(inst_id)])
    return destroy.verify_destroyed(inst_id)


def _check_marker(inst_id: int, label: str) -> typer.Exit | None:
    """Run the SPEC §destroy single-ID + LABEL ownership checks. None on pass."""
    endpoint = ssh.resolve_ssh_endpoint(inst_id)
    if endpoint is None:
        return _fail(
            f"Instance {inst_id} SSH unreachable — cannot verify ownership. "
            f"Re-run with --force to destroy anyway."
        )
    try:
        m = marker.read_marker(*endpoint)
    except Exception:
        return _fail(
            f"Instance {inst_id} SSH unreachable — cannot verify ownership. "
            f"Re-run with --force to destroy anyway."
        )
    if m is None:
        return _fail(
            f"Instance {inst_id} has no marker — it was provisioned outside "
            f"vastrun-kit. Re-run with --force to destroy anyway."
        )
    me = socket.gethostname()
    if m.hostname != me:
        return _fail(
            f"Instance {inst_id} is owned by host '{m.hostname}', not '{me}'. "
            f"Re-run with --force to destroy anyway."
        )
    if m.label != label:
        return _fail(
            f"Instance {inst_id} marker label is '{m.label}', not '{label}'. "
            f"Re-run with --force to destroy anyway."
        )
    return None


def _do_single(inst_id: int, snap: dict | None) -> None:
    verified = _destroy_one(inst_id)
    if verified:
        typer.echo(destroy.render_destroyed_summary(snap or {"id": inst_id}))
        return
    _err(destroy.render_unverified_warning(snap, inst_id))
    raise typer.Exit(code=1)


def _do_bulk_force() -> None:
    rows = instances.list_instances()
    all_ok = True
    for row in rows:
        line = destroy.bulk_heads_up_line(row)
        rid = row.get("id")
        if rid is None:
            continue
        ok = _destroy_one(int(rid))
        suffix = "  → destroyed" if ok else "  → WARNING: not confirmed"
        typer.echo(line + suffix)
        all_ok = all_ok and ok
    if not all_ok:
        raise typer.Exit(code=1)


@app.command()
def main(
    instance_id: int | None = typer.Argument(None),
    label: str | None = typer.Argument(None),
    all_: bool = typer.Option(False, "--all", help="Bulk path. Always refuses by default."),
    force: bool = typer.Option(False, "--force", hidden=True),
) -> None:
    """Destroy a Vast.ai instance using INSTANCE_ID + LABEL as a confirmation
    token, or use --all to tear down every instance on the account."""
    if all_:
        if not force:
            raise _fail(_BULK_WARNING)
        _do_bulk_force()
        return

    if instance_id is None:
        raise _fail("Error: provide an INSTANCE_ID + LABEL, or use --all.")

    if force:
        snap = instances.find_instance(instance_id)
        _do_single(instance_id, snap)
        return

    if label is None:
        raise _fail(
            "Error: vastrun-destroy <ID> <LABEL> requires both arguments. "
            "Pass --force to destroy by ID alone (e.g. when you don't know the label)."
        )

    snap = instances.find_instance(instance_id)
    if snap is None:
        raise _fail(f"Instance {instance_id} not found.")

    err = _check_marker(instance_id, label)
    if err is not None:
        raise err
    _do_single(instance_id, snap)
