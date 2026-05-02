"""`vastrun-search` — list Vast.ai offers matching the given filters as an
ASCII table the agent reads to pick one. Provisioning is a separate command.

Resolution priority for each filter: CLI flag > .vastrun.toml [vast] > package
default. Output columns and the no-match diagnostic come from `offers.py`;
this module just glues the CLI to the offer-search core.
"""

from __future__ import annotations

import sys

import typer

from .. import _table, client_config, config, errors, offers, vastai_cli

app = typer.Typer(add_completion=False)

# Default columns + extra `--hardware` columns. ASCII Up/Down (not arrows) per
# the status CLI's `assert ord(c) < 128` invariant — agents read these tables
# without paying tokens for non-ASCII glyphs.
_DEFAULT_HEADERS = (
    "Offer ID", "Machine ID", "GPU", "Num", "VRAM/GPU", "TFLOPS/GPU",
    "$/h", "DLPerf/$", "Region", "Reliability",
)
_HARDWARE_HEADERS = (
    "CPU", "RAM", "Bus", "Disk GB", "Disk BW", "Up", "Down", "Driver", "CUDA",
)


def _f(row: dict, key: str, default: float = 0.0) -> float:
    """Coerce row[key] to float, treating None as `default`."""
    v = row.get(key)
    return default if v is None else float(v)


def _build_row(row: dict, *, spot: bool, hardware: bool) -> tuple[str, ...]:
    n = max(1, int(row.get("num_gpus") or 1))
    region = (row.get("geolocation") or "").rsplit(",", 1)[-1].strip() or "-"
    dlperf = row.get("dlperf_per_dphtotal")
    rel = row.get("reliability2")
    base = (
        str(row.get("id", "-")),
        str(row.get("machine_id") or row.get("host_id") or "-"),
        str(row.get("gpu_name") or "-"),
        str(n),
        f"{_f(row, 'gpu_ram') / n / 1024:.0f}GB",
        f"{_f(row, 'total_flops') / n:.1f}",
        f"${_f(row, 'min_bid' if spot else 'dph_total'):.4f}",
        f"{float(dlperf):.1f}" if dlperf else "-",
        region,
        f"{float(rel):.3f}" if rel is not None else "-",
    )
    if not hardware:
        return base
    bus = f"PCIe {row.get('pci_gen', '?')} x{row.get('gpu_lanes', '?')}"
    if (row.get("bw_nvlink") or 0) > 0:
        bus += " +NVLink"
    return (
        *base,
        f"{row.get('cpu_name') or '-'} ({row.get('cpu_cores') or '?'}c)",
        f"{int(_f(row, 'cpu_ram') // 1024)}GB",
        bus,
        f"{_f(row, 'disk_space'):.0f}",
        f"{_f(row, 'disk_bw'):.0f}",
        f"{_f(row, 'inet_up'):.0f}",
        f"{_f(row, 'inet_down'):.0f}",
        str(row.get("driver_version") or "-"),
        str(row.get("cuda_max_good") or "-"),
    )


def _resolve(cli_value, toml_section: dict, key: str, default):  # type: ignore[no-untyped-def]
    """CLI flag > .vastrun.toml [vast] value > default."""
    if cli_value is not None:
        return cli_value
    v = toml_section.get(key)
    return default if v is None else v


def _gpu_list(cli: str | None, toml_v: object) -> list[str] | None:
    """CLI: comma-split. TOML: str→[str] or pre-listified. Either may be None."""
    if cli is not None:
        parts = [p.strip() for p in cli.split(",") if p.strip()]
        return parts or None
    if toml_v is None:
        return None
    if isinstance(toml_v, str):
        return [toml_v]
    return list(toml_v) or None


@app.command()
def main(
    gpu_model: str = typer.Option(
        None, "--gpu-model",
        help="Comma-separated GPU model substrings (case-insensitive, underscore/space interchangeable).",
    ),
    min_vram: int = typer.Option(None, "--min-vram", help="Per-GPU VRAM floor in GB."),
    num_gpus: int = typer.Option(1, "--num-gpus", help="Number of GPUs (default 1)."),
    max_bid: float = typer.Option(None, "--max-bid", help="Max hourly price in $/h."),
    min_tflops: float = typer.Option(None, "--min-tflops", help="Per-GPU TFLOPS floor."),
    min_reliability: float = typer.Option(
        None, "--min-reliability",
        help="Min reliability in [0,1] (silently floored at safety floor).",
    ),
    country: str = typer.Option(None, "--country", help="ISO-2 country code."),
    region: str = typer.Option(None, "--region", help="EU / US / APAC / NA."),
    prosumer: bool = typer.Option(False, "--prosumer", help="Allow non-datacenter machines."),
    spot: bool = typer.Option(False, "--spot", help="Show spot prices (min_bid) instead of on-demand."),
    hardware: bool = typer.Option(False, "--hardware", help="Append CPU/RAM/Bus/Disk/Inet/Driver/CUDA columns."),
    limit: int = typer.Option(20, "--limit", help="Cap rows printed (default 20)."),
) -> None:
    """List Vast.ai offers matching the given filters."""
    try:
        toml = client_config.load_vastrun_toml()
    except FileNotFoundError:
        toml = {}
    vast = client_config.vast_section(toml)

    f = offers.OfferFilters(
        gpu_name=_gpu_list(gpu_model, vast.get("gpu_name")),
        min_vram_gb=_resolve(min_vram, vast, "min_vram_gb", None),
        num_gpus=num_gpus,
        max_bid=_resolve(max_bid, vast, "max_bid", config.MAX_BID),
        min_tflops=_resolve(min_tflops, vast, "min_tflops", config.TFLOPS_PER_GPU_MIN),
        min_reliability=_resolve(min_reliability, vast, "min_reliability", config.RELIABILITY_MIN),
        country=_resolve(country, vast, "country", None),
        region=_resolve(region, vast, "region", None),
        prosumer=prosumer,
        spot=spot,
    )

    try:
        query = offers.build_offer_query(f)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)

    try:
        rows = vastai_cli.run_vastai_raw(["search", "offers", query])
    except (errors.VastaiCliError, errors.MissingCredentialError) as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)

    total = len(rows)
    survivors, exclusion_log = offers.filter_offers(rows, f)
    survivors = offers.sort_offers(survivors)[:limit]

    if not survivors:
        typer.echo(offers.render_no_match_diagnostic(f, total, exclusion_log))
        raise typer.Exit(code=1)

    headers = _DEFAULT_HEADERS + (_HARDWARE_HEADERS if hardware else ())
    body = [_build_row(r, spot=spot, hardware=hardware) for r in survivors]
    typer.echo(_table.render_table(headers, body))
    typer.echo(f"Total: {total} offers (showing top {min(total, limit)}).")
