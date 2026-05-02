"""vastrun-init: scaffold a `.vastrun.toml` template in CWD."""

from __future__ import annotations

from pathlib import Path

import typer

_TEMPLATE = """# .vastrun.toml — per-project Vast.ai config for vastrun-kit.
# Uncomment and edit the keys you want to set; defaults live in the package.
# Stripping these `#` lines must yield valid TOML — keep that invariant.
#
# [vast]
# image             = "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04"
# min_vram_gb       = 24
# min_tflops        = 50.0
# max_bid           = 1.50
# gpu_name          = ["A100", "H100"]
# min_reliability   = 0.95
# min_upload_mbps   = 500
# min_download_mbps = 1000
# min_disk_gb       = 100
# min_disk_bw       = 500
# country           = "US"
# region            = "EU"
# ssh_key           = "~/.ssh/id_ed25519.pub"
"""

app = typer.Typer(add_completion=False)


@app.command()
def main() -> None:
    """Scaffold a .vastrun.toml template in the current directory."""
    target = Path.cwd() / ".vastrun.toml"
    if target.exists():
        typer.echo("Error: .vastrun.toml already exists. Refusing to overwrite.", err=True)
        raise typer.Exit(code=1)
    target.write_text(_TEMPLATE)
    typer.echo("Created .vastrun.toml")
    if not (Path.cwd() / ".env").exists():
        typer.echo("Tip: create a .env with VASTAI_API_TOKEN=<your key>")
    if not (Path.cwd() / "pyproject.toml").exists():
        typer.echo("Warning: vastrun-kit expects to run from a Python project root.")
