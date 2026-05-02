"""`vastrun-restart` — start a Vast.ai instance that is in `exited` state.

Wraps `vastai start instance <id>` so the user never falls back to raw `vastai`
for state recovery. Reuses the boot-wait and SSH-probe logic from
`vastrun-provision`. The marker is **never** rewritten — the original owner
stays the owner; restarting under `--force` does not transfer ownership.
"""

from __future__ import annotations

import sys

import typer

from .. import boot, errors, instances, marker, ssh, vastai_cli  # noqa: F401  marker re-exported for tests

app = typer.Typer(add_completion=False)
_DESTROY_HINT = "Run vastrun-destroy {} --force to clean up."


def _exit1(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    raise typer.Exit(code=1)


@app.command()
def main(
    instance_id: int = typer.Argument(..., help="Instance to start."),
    label: str = typer.Argument(..., help="Display-label confirmation token."),
    force: bool = typer.Option(False, "--force", hidden=True),
) -> None:
    """Restart an exited Vast.ai instance."""
    try:
        inst = instances.find_instance(instance_id)
    except (errors.VastaiCliError, errors.MissingCredentialError) as e:
        _exit1(str(e))
    if inst is None:
        _exit1(f"Instance {instance_id} not found.")

    status = inst.get("actual_status")
    if status in ("running", "loading"):
        typer.echo(f"Instance {instance_id} already {status}.")
        raise typer.Exit(0)

    inst_label = inst.get("label")
    if not force and inst_label != label:
        found = f"'{inst_label}'" if inst_label else "missing"
        _exit1(
            f"Instance {instance_id} display label is {found}, not '{label}'. "
            "Re-run with --force to skip the label check."
        )

    rc, out, err = vastai_cli.run_vastai(["start", "instance", str(instance_id)])
    if rc != 0 or not out.strip():
        msg = (err.strip() or out.strip() or "non-zero exit").splitlines()[0]
        _exit1(f"`vastai start instance {instance_id}` failed: {msg}")

    try:
        boot.wait_for_boot(instance_id)
    except boot.BootFailure as e:
        _exit1(
            f"Instance {instance_id} did not reach 'running' after restart "
            f"(reason: {e.reason}). Check with vastrun-status; clean up with "
            f"vastrun-destroy {instance_id} --force."
        )

    ep = ssh.resolve_ssh_endpoint(instance_id)
    if ep is None:
        _exit1(f"Instance {instance_id} restarted but SSH info missing from API. " + _DESTROY_HINT.format(instance_id))
    host, port = ep
    if not ssh.wait_for_ssh(host, port):
        _exit1(f"Instance {instance_id} restarted but SSH unreachable at {host}:{port}. " + _DESTROY_HINT.format(instance_id))

    typer.echo("")
    typer.echo(f"Instance {instance_id} restarted")
    typer.echo(f"  SSH: ssh -p {port} root@{host}")
    typer.echo(f"  Label: {inst_label or '-'}")
    typer.echo("")
