"""vastrun-forward: friction-y escape hatch to raw `vastai`."""

from __future__ import annotations

import subprocess

import typer

from .. import client_config, config, errors

app = typer.Typer(add_completion=False)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def main(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", hidden=True),
) -> None:
    """Escape hatch: forward arbitrary args to the raw `vastai` CLI."""
    args = list(ctx.args)
    if not force:
        typer.echo(
            f"""vastrun-forward exists for features not yet covered by another vastrun-* command.

First, check whether another `vastrun-*` command already does what you need.

If this really is a missing feature, open an issue at {config.ISSUE_URL} describing it,
then re-run with `--force` to actually forward the call.""",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(
        f"Forwarding to vastai (you should have an issue tracked at {config.ISSUE_URL} for this gap).",
        err=True,
    )
    try:
        api_key = client_config.load_api_key()
    except errors.MissingCredentialError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    proc = subprocess.run(["vastai", "--api-key", api_key, *args])
    raise typer.Exit(code=proc.returncode)
