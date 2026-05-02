"""`vastrun-provision` — provision one fresh GPU instance from a specific offer.

Wraps the Provision flow from SPEC.md: load TOML, resolve offer, validate
SSH key + image, check balance, create, wait_for_boot, attach SSH, resolve
endpoint, wait_for_ssh, write marker, hello-world probe, success summary.
Every billing-side leak names `vastrun-destroy <id> --force`.
"""

from __future__ import annotations

import sys
import time

import typer

from .. import (
    boot, client_config, config, errors, instances, marker, provision, ssh, vastai_cli,
)

app = typer.Typer(add_completion=False)
_BILLING_URL = "https://console.vast.ai/billing/"
_BALANCE_FLOOR = 0.10
_DESTROY = "Run vastrun-destroy {} --force to clean up."


def _exit1(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    raise typer.Exit(code=1)


def _decorate(name: str, fn) -> None:  # type: ignore[no-untyped-def]
    """Run `fn()`; on any exception, log to stderr and continue (decorative steps)."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print(f"({name} raised {e!r}; continuing)", file=sys.stderr)


def _print_success(inst_id: int, host: str, port: int, label: str, spot_bid: float | None, dph: float | None) -> None:
    cost = (
        f"${spot_bid:.4f}/h (spot)" if spot_bid is not None
        else f"${dph:.4f}/h (on-demand)" if dph is not None else "unknown"
    )
    typer.echo("")
    typer.echo(f"Instance {inst_id} ready")
    if hw := provision.hardware_summary(inst_id):
        typer.echo(f"  Hardware: {hw}")
    typer.echo(f"  SSH: ssh -p {port} root@{host}")
    typer.echo(f"  Cost: {cost}")
    typer.echo(f"  Label: {label}")
    typer.echo("\nNext:")
    typer.echo(f'  vastrun-exec {inst_id} "<command>"  - run a command')
    typer.echo(f"  vastrun-destroy {inst_id} {label}   - tear it down")


@app.command()
def main(
    offer_id: int = typer.Argument(..., help="Offer ID from `vastrun-search`."),
    label: str = typer.Option(..., "--label", help="Ownership label."),
    spot: bool = typer.Option(False, "--spot", help="Bid as interruptible (min_bid * BID_MULTIPLIER, capped)."),
    image: str = typer.Option(None, "--image", help="Docker image override (must contain ':')."),
    ssh_key: str = typer.Option(None, "--ssh-key", help="Local SSH public key path."),
    prosumer: bool = typer.Option(
        False, "--prosumer",
        help="Allow non-datacenter offers. Match the flag you passed to vastrun-search.",
    ),
) -> None:
    """Provision a fresh GPU instance from OFFER_ID."""
    try:
        vast = client_config.vast_section(client_config.load_vastrun_toml())
    except FileNotFoundError as e:
        _exit1(str(e))
    try:
        offer = provision.resolve_offer(offer_id, prosumer=prosumer)
    except errors.OfferUnavailableError as e:
        msg = str(e)
        if "vastrun-search" not in msg:
            msg = f"{msg} Run `vastrun-search` to list current offers."
        _exit1(msg)
    except (errors.VastaiCliError, errors.MissingCredentialError) as e:
        _exit1(str(e))
    try:
        pub = ssh.autodiscover_ssh_pubkey(ssh_key or vast.get("ssh_key"))
        eff_image = provision.resolve_image(offer, override=image or vast.get("image"))
        credit = provision.check_balance()
    except (FileNotFoundError, ValueError, errors.VastaiCliError, errors.MissingCredentialError) as e:
        _exit1(str(e))
    if credit < _BALANCE_FLOOR:
        _exit1(f"Account credit ${credit:.2f} is below ${_BALANCE_FLOOR:.2f}. Top up at {_BILLING_URL}.")

    spot_bid = provision.compute_spot_bid(offer) if spot else None
    try:
        inst_id = provision.create_instance(
            offer_id, image=eff_image, label=label, ssh_pubkey=pub, spot_bid=spot_bid,
        )
    except (errors.VastaiCliError, errors.MissingCredentialError) as e:
        _exit1(str(e))
    try:
        boot.wait_for_boot(inst_id)
    except boot.BootFailure as e:
        _exit1(
            f"Instance {inst_id} did not reach 'running' (reason: {e.reason}). "
            f"Check with vastrun-status; clean up with vastrun-destroy {inst_id} --force."
        )

    time.sleep(config.POST_BOOT_GRACE_SECONDS)
    try:
        ssh.attach_ssh_key(inst_id, pub)
    except errors.VastaiCliError as e:
        _exit1(f"Instance {inst_id} SSH key attach failed: {e}. " + _DESTROY.format(inst_id))
    time.sleep(config.POST_ATTACH_KEY_GRACE_SECONDS)

    ep = ssh.resolve_ssh_endpoint(inst_id)
    if ep is None:
        _exit1(f"Instance {inst_id} created but SSH info missing from API. " + _DESTROY.format(inst_id))
    host, port = ep
    if not ssh.wait_for_ssh(host, port):
        _exit1(f"Instance {inst_id} created but SSH unreachable at {host}:{port}. " + _DESTROY.format(inst_id))

    try:
        marker.write_marker(host, port, marker.make_marker(label))
    except (errors.VastaiCliError, RuntimeError, OSError) as e:
        _exit1(
            f"Instance {inst_id} ownership marker write failed ({e}) - instance is unmanageable. "
            + _DESTROY.format(inst_id)
        )

    _decorate("hello-world probe", lambda: ssh.ssh_exec(host, port, "echo ok"))
    _decorate("vastai label", lambda: vastai_cli.run_vastai(["label", "instance", str(inst_id), label]))
    _print_success(inst_id, host, port, label, spot_bid, offer.get("dph_total"))
