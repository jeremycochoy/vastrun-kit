"""`vastrun-exec` — run a one-off command on an instance, streaming output.

The command is wrapped in base64 by `ssh.ssh_exec`; we delegate transport,
retry SSH transport failures (rc==255) once after a 3s sleep, and propagate
the user's command exit code. After the run we attempt a best-effort
"spent so far" footer on stderr — silent on any failure so a flaky API
never overrides the command's own exit code.
"""

from __future__ import annotations

import sys
import time

import typer

from .. import client_config, errors, instances, ssh

app = typer.Typer(add_completion=False, help="Run a one-off command on an instance over SSH.")

_SSH_TRANSPORT_RC = 255
_SSH_RETRY_SLEEP_SECONDS = 3


def _print_spent_footer(inst_id: int) -> None:
    """Best-effort cost-so-far line on stderr; silent on any exception.

    `start_date` is a Unix epoch float in `vastai show instances` payloads.
    """
    try:
        inst = instances.find_instance(inst_id)
        if not inst:
            return
        dph, start = inst.get("dph_total"), inst.get("start_date")
        if dph is None or start is None:
            return
        spent = max(0.0, time.time() - float(start)) / 3600.0 * float(dph)
        print(f"Instance {inst_id}: ${spent:.2f} spent so far", file=sys.stderr)
    except Exception:
        return


@app.command()
def main(instance_id: int, command: str) -> None:
    """Run COMMAND on INSTANCE_ID via SSH and stream output."""
    try:
        client_config.load_api_key()
    except errors.MissingCredentialError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)

    endpoint = ssh.resolve_ssh_endpoint(instance_id)
    if endpoint is None:
        print(
            f"Error: instance {instance_id} not found or SSH endpoint unavailable.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    host, port = endpoint
    rc, _, _ = ssh.ssh_exec(host, port, command, stream=True)
    if rc == _SSH_TRANSPORT_RC:
        time.sleep(_SSH_RETRY_SLEEP_SECONDS)
        rc, _, _ = ssh.ssh_exec(host, port, command, stream=True)

    _print_spent_footer(instance_id)
    raise typer.Exit(code=rc)
