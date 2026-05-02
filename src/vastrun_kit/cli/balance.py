"""vastrun-balance: print account credit / balance / total."""

from __future__ import annotations

import typer

from .. import errors, vastai_cli

app = typer.Typer(add_completion=False)


@app.command()
def main() -> None:
    """Show account credit, balance, and total."""
    try:
        user = vastai_cli.run_vastai_raw(["show", "user"])
    except (errors.VastaiCliError, errors.MissingCredentialError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    credit = float(user.get("credit") or 0)
    balance = float(user.get("balance") or 0)
    typer.echo(f"Credit:  ${credit:.2f}")
    if balance != 0:
        typer.echo(f"Balance: ${balance:.2f}")
    typer.echo(f"Total:   ${credit + balance:.2f}")
