"""`vastrun-bid` — change the hourly bid on a spot instance.

Wraps `vastai change bid <id> --price <new_bid>`. Lets a user who chose `--spot`
raise their bid mid-run to avoid preemption. No-op against on-demand instances
(warning printed; underlying API accepts it as a no-op). Refuses to act on an
UNCLAIMED instance or one owned by another host.
"""

from __future__ import annotations

import socket
import sys

import typer

from .. import instances, marker, ssh, vastai_cli

app = typer.Typer(add_completion=False)


@app.command()
def main(
    instance_id: int = typer.Argument(..., help="Instance to bid on."),
    new_bid: str = typer.Argument(..., help="New hourly bid in $/h. Must be > 0."),
) -> None:
    """Change the bid on a spot instance."""
    try:
        bid_value = float(new_bid)
    except ValueError:
        print(f"Error: NEW_BID '{new_bid}' is not a number.", file=sys.stderr)
        raise typer.Exit(code=1)
    if bid_value <= 0:
        print("Error: NEW_BID must be > 0.", file=sys.stderr)
        raise typer.Exit(code=1)

    inst = instances.find_instance(instance_id)
    if inst is None:
        print(f"Error: Instance {instance_id} not found.", file=sys.stderr)
        raise typer.Exit(code=1)

    if not inst.get("is_bid") or inst.get("min_bid") is None:
        print(
            f"Warning: instance {instance_id} is on-demand, not spot — "
            "bid change has no effect."
        )

    endpoint = ssh.resolve_ssh_endpoint(instance_id)
    if endpoint is None:
        print(
            f"Error: Instance {instance_id} SSH unreachable — "
            "cannot verify ownership.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)
    host, port = endpoint

    me = socket.gethostname()
    m = marker.read_marker(host, port)
    if m is None:
        print(
            f"Error: Instance {instance_id} has no marker — "
            "refusing to act on UNCLAIMED instance.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)
    if m.hostname != me:
        print(
            f"Error: Instance {instance_id} is owned by host '{m.hostname}', "
            f"not '{me}'. Refusing to change bid.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    rc, _, err = vastai_cli.run_vastai(
        ["change", "bid", str(instance_id), "--price", str(bid_value)]
    )
    if rc != 0:
        detail = err.strip() or "non-zero exit"
        print(
            f"Error: vastai change bid {instance_id} --price {bid_value} failed: {detail}",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    print(f"Bid for instance {instance_id} set to ${bid_value}/h.")
