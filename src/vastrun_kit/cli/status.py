"""`vastrun-status` — list every instance on the account.

Default: single-shot, non-blocking. `--watch` clears and reprints every
`--interval` seconds. `--json` dumps the raw payload. ASCII output only —
agent consumers must read the table without paying tokens for box drawing.
"""

from __future__ import annotations

import json
import sys
import time

import typer

from .. import errors, instances, ssh

app = typer.Typer(add_completion=False)

_HEADERS = ("ID", "Label", "Status", "GPU", "Uptime", "Cost/h", "Spent", "SSH Address")


def _format_uptime(start: float | None, now: float) -> str:
    if start is None:
        return "-"
    mins = int((now - start) // 60)
    if mins < 0:
        return "-"
    return f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"


def _format_money_per_hour(dph: float | None) -> str:
    return f"${dph:.4f}" if dph is not None else "-"


def _format_spent(start: float | None, now: float, dph: float | None) -> str:
    if start is None or dph is None or now < start:
        return "-"
    return f"${(now - start) / 3600 * dph:.2f}"


def _format_ssh_address(inst: dict) -> str:
    ep = ssh.parse_ssh_endpoint(inst)
    return f"{ep[0]}:{ep[1]}" if ep else "-"


def _row_cells(inst: dict, now: float) -> tuple[str, ...]:
    status = inst.get("actual_status") or inst.get("status_msg") or "unknown"
    return (
        str(inst.get("id", "-")),
        inst.get("label") or "-",
        status,
        inst.get("gpu_name") or "-",
        _format_uptime(inst.get("start_date"), now),
        _format_money_per_hour(inst.get("dph_total")),
        _format_spent(inst.get("start_date"), now, inst.get("dph_total")),
        _format_ssh_address(inst),
    )


def _render_table(rows: list[dict], now: float) -> str:
    body = [_row_cells(r, now) for r in rows]
    widths = [max(len(c) for c in col) for col in zip(_HEADERS, *body)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*_HEADERS), *(fmt.format(*r) for r in body)]
    n = len(rows)
    lines.append(f"Total: {n} instance(s)")
    return "\n".join(lines)


def _print_snapshot() -> None:
    rows = instances.list_instances()
    if not rows:
        typer.echo("No running instances found.")
        return
    typer.echo(_render_table(rows, time.time()))


@app.command()
def main(
    json_out: bool = typer.Option(False, "--json", help="Emit raw show-instances JSON."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Block and reprint every --interval seconds."),
    interval: int = typer.Option(5, "--interval", help="Refresh interval in seconds (≥ 1, used with --watch)."),
) -> None:
    """List every instance on the account."""
    if watch and interval < 1:
        typer.echo("Error: --interval must be ≥ 1.", err=True)
        raise typer.Exit(1)
    try:
        if json_out:
            typer.echo(json.dumps(instances.list_instances()))
            return
        if not watch:
            _print_snapshot()
            return
        try:
            while True:
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
                _print_snapshot()
                time.sleep(interval)
        except KeyboardInterrupt:
            return
    except (errors.MissingCredentialError, errors.VastaiCliError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e
