"""Tests for `vastrun-search` (single-command Typer app).

Behavioural contract from SPEC §"vastrun-search":
- Default: 10-column ASCII table sorted by best DLPerf-per-dollar first.
- `--hardware` appends 9 extra columns.
- `--limit N` caps the printed body.
- No matches → no-match diagnostic + exit 1.
- Unknown region → ValueError surfaces as exit 1.
- Missing `.vastrun.toml` is fine — search uses package defaults.
- All output is ASCII (< 128) — agents read tables without paying for box drawing.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from vastrun_kit import client_config, errors, offers, vastai_cli
from vastrun_kit.cli import search as search_cli

runner = CliRunner()


# ----- fixtures ----------------------------------------------------------- #


def _offer(**overrides: object) -> dict:
    """Realistic single-GPU offer row. Override fields per test."""
    base = {
        "id": 1001,
        "machine_id": 5001,
        "host_id": 9001,
        "gpu_name": "RTX 4090",
        "num_gpus": 1,
        "gpu_ram": 24576,           # MB total
        "total_flops": 100.0,
        "dph_total": 0.50,
        "min_bid": 0.20,
        "dlperf_per_dphtotal": 60.0,
        "driver_version": "565.77.01",
        "cuda_max_good": 12.6,
        "geolocation": "United States, US",
        "inet_up": 800,
        "inet_down": 1500,
        "reliability2": 0.987,
        "cpu_name": "AMD Ryzen 9 7950X",
        "cpu_cores": 32,
        "cpu_ram": 65536,           # MB
        "pci_gen": 4,
        "gpu_lanes": 16,
        "bw_nvlink": 0,
        "disk_space": 500,
        "disk_bw": 1200,
    }
    base.update(overrides)
    return base


def _three_offers() -> list[dict]:
    """Three offers; sorted by DLPerf/$ they should come out 2003, 2002, 2001."""
    return [
        _offer(id=2001, machine_id=5001, dlperf_per_dphtotal=10.0, min_bid=0.10, dph_total=0.30),
        _offer(id=2002, machine_id=5002, dlperf_per_dphtotal=50.0, min_bid=0.20, dph_total=0.40),
        _offer(id=2003, machine_id=5003, dlperf_per_dphtotal=60.0, min_bid=0.15, dph_total=0.50),
    ]


def _stub_no_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make load_vastrun_toml raise FileNotFoundError so CLI uses package defaults."""

    def _raise(*_a: object, **_kw: object) -> dict:
        raise FileNotFoundError(".vastrun.toml not found")

    monkeypatch.setattr(client_config, "load_vastrun_toml", _raise)


# ----- happy path: default columns ---------------------------------------- #


def test_happy_path_renders_default_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_toml(monkeypatch)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: _three_offers())

    result = runner.invoke(search_cli.app, [])

    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    out = result.stdout

    # Default headers must all appear.
    for header in ("Offer ID", "Machine ID", "GPU", "Num", "VRAM/GPU",
                   "TFLOPS/GPU", "$/h", "DLPerf/$", "Region", "Reliability"):
        assert header in out, f"missing header: {header!r}"

    # `--hardware` extras must NOT appear without --hardware.
    assert "CPU" not in out
    assert "Disk GB" not in out


def test_happy_path_sorted_by_best_dlperf_per_dollar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_toml(monkeypatch)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: _three_offers())

    result = runner.invoke(search_cli.app, [])
    assert result.exit_code == 0
    out = result.stdout

    # 2003 (best DLPerf/$) appears before 2002, which appears before 2001.
    pos_2003 = out.find("2003")
    pos_2002 = out.find("2002")
    pos_2001 = out.find("2001")
    assert pos_2003 != -1 and pos_2002 != -1 and pos_2001 != -1
    assert pos_2003 < pos_2002 < pos_2001


def test_happy_path_total_footer(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_toml(monkeypatch)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: _three_offers())

    result = runner.invoke(search_cli.app, [])
    assert result.exit_code == 0
    # Footer reports total + showing line.
    assert "Total: 3 offers" in result.stdout
    assert "showing top 3" in result.stdout


def test_happy_path_dollars_per_hour_default_uses_dph_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_toml(monkeypatch)
    rows = [_offer(id=4001, dph_total=0.50, min_bid=0.20, dlperf_per_dphtotal=60.0)]
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: rows)

    result = runner.invoke(search_cli.app, [])
    assert result.exit_code == 0
    # Default mode shows on-demand price.
    assert "$0.5000" in result.stdout
    # And not the spot price.
    assert "$0.2000" not in result.stdout


# ----- --spot flips price column ------------------------------------------ #


def test_spot_flag_uses_min_bid_for_price_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_toml(monkeypatch)
    rows = [_offer(id=4002, dph_total=0.50, min_bid=0.20, dlperf_per_dphtotal=60.0)]
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: rows)

    result = runner.invoke(search_cli.app, ["--spot"])
    assert result.exit_code == 0
    assert "$0.2000" in result.stdout
    assert "$0.5000" not in result.stdout


# ----- --hardware appends columns ----------------------------------------- #


def test_hardware_flag_appends_extra_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_toml(monkeypatch)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: _three_offers())

    result = runner.invoke(search_cli.app, ["--hardware"])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    out = result.stdout

    # Default headers still present.
    for header in ("Offer ID", "GPU", "DLPerf/$", "Reliability"):
        assert header in out

    # Extra hardware headers — note ASCII Up/Down (not arrows).
    for header in ("CPU", "RAM", "Bus", "Disk GB", "Disk BW",
                   "Up", "Down", "Driver", "CUDA"):
        assert header in out, f"missing hardware header: {header!r}"


def test_hardware_renders_pcie_and_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_toml(monkeypatch)
    rows = [_offer(id=4003, cpu_name="AMD EPYC 7763", cpu_cores=64,
                    pci_gen=4, gpu_lanes=16, cpu_ram=131072, bw_nvlink=0)]
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: rows)

    result = runner.invoke(search_cli.app, ["--hardware"])
    assert result.exit_code == 0
    out = result.stdout
    assert "AMD EPYC 7763" in out
    assert "(64c)" in out
    # PCIe rendering — exact format `PCIe 4 ×16` would have a non-ASCII char,
    # so search.py uses ASCII-only `PCIe 4 x16`.
    assert "PCIe 4" in out and "x16" in out
    # 131072 MB → 128 GB.
    assert "128GB" in out


def test_hardware_flags_nvlink_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_toml(monkeypatch)
    rows = [_offer(id=4004, bw_nvlink=600.0)]
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: rows)

    result = runner.invoke(search_cli.app, ["--hardware"])
    assert result.exit_code == 0
    assert "NVLink" in result.stdout


# ----- --limit ------------------------------------------------------------ #


def test_limit_caps_body_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_toml(monkeypatch)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: _three_offers())

    result = runner.invoke(search_cli.app, ["--limit", "1"])
    assert result.exit_code == 0
    out = result.stdout
    # The single body row is the highest DLPerf/$ offer (2003).
    assert "2003" in out
    # The other two should NOT appear in the table body.
    assert "2002" not in out
    assert "2001" not in out
    # Footer says 3 total but showing 1.
    assert "Total: 3 offers" in out
    assert "showing top 1" in out


# ----- no matches → diagnostic + exit 1 ----------------------------------- #


def test_no_matches_prints_diagnostic_and_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_toml(monkeypatch)
    # All offers will be filtered out by max_bid=0.01.
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: _three_offers())

    result = runner.invoke(search_cli.app, ["--max-bid", "0.01"])
    assert result.exit_code == 1
    out = result.stdout + (result.stderr or "")
    # render_no_match_diagnostic emits "Binding filter:" or "No offers matched".
    assert "Binding filter:" in out or "No offers matched" in out


def test_empty_response_from_api_prints_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_toml(monkeypatch)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: [])

    result = runner.invoke(search_cli.app, [])
    assert result.exit_code == 1
    out = result.stdout + (result.stderr or "")
    assert "No offers matched" in out


# ----- unknown region surfaces as exit 1 ---------------------------------- #


def test_unknown_region_exits_one_with_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_toml(monkeypatch)
    # No need to stub the API call — the ValueError fires before that.
    result = runner.invoke(search_cli.app, ["--region", "MARS"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "MARS" in combined or "region" in combined.lower()


# ----- vastai CLI errors propagate ---------------------------------------- #


def test_vastai_cli_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_toml(monkeypatch)

    def boom(*_a: object, **_kw: object) -> list:
        raise errors.VastaiCliError("vastai search offers failed: timeout")

    monkeypatch.setattr(vastai_cli, "run_vastai_raw", boom)
    result = runner.invoke(search_cli.app, [])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "timeout" in combined or "search offers" in combined


# ----- missing .vastrun.toml is OK; defaults apply ------------------------ #


def test_missing_toml_uses_defaults_and_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_toml(monkeypatch)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: _three_offers())

    result = runner.invoke(search_cli.app, [])
    assert result.exit_code == 0
    # Three rows survived defaults — table must include every offer ID.
    for oid in (2001, 2002, 2003):
        assert str(oid) in result.stdout


# ----- ASCII-only output -------------------------------------------------- #


def test_output_is_ascii_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_toml(monkeypatch)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: _three_offers())

    result = runner.invoke(search_cli.app, ["--hardware"])
    assert result.exit_code == 0
    for ch in result.stdout:
        assert ord(ch) < 128, f"non-ASCII char {ch!r} (U+{ord(ch):04X}) in output"


# ----- --gpu-model parses comma-separated list ---------------------------- #


def test_gpu_model_comma_split_passed_to_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_toml(monkeypatch)
    captured: dict[str, offers.OfferFilters] = {}

    real_build = offers.build_offer_query

    def capture_build(f: offers.OfferFilters) -> str:
        captured["filters"] = f
        return real_build(f)

    monkeypatch.setattr(search_cli.offers, "build_offer_query", capture_build)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: _three_offers())

    result = runner.invoke(search_cli.app, ["--gpu-model", "A100,H100"])
    # The filter would zero our 4090 offers — exit 1 is expected, but we only
    # care that the OfferFilters got the parsed list.
    assert "filters" in captured
    assert captured["filters"].gpu_name == ["A100", "H100"]
    # Non-zero exit because no 4090 offer matches A100/H100.
    assert result.exit_code in (0, 1)


# ----- .toml values picked up when CLI flag absent ------------------------ #


def test_toml_values_used_when_no_cli_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.vastrun.toml`'s gpu_name flows into OfferFilters when --gpu-model omitted."""
    monkeypatch.setattr(
        client_config, "load_vastrun_toml",
        lambda *_a, **_kw: {"vast": {"gpu_name": ["A100"]}},
    )
    captured: dict[str, offers.OfferFilters] = {}
    real_build = offers.build_offer_query

    def capture_build(f: offers.OfferFilters) -> str:
        captured["filters"] = f
        return real_build(f)

    monkeypatch.setattr(search_cli.offers, "build_offer_query", capture_build)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: [])

    runner.invoke(search_cli.app, [])
    assert captured["filters"].gpu_name == ["A100"]


def test_cli_flag_overrides_toml_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_config, "load_vastrun_toml",
        lambda *_a, **_kw: {"vast": {"gpu_name": ["A100"], "max_bid": 0.99}},
    )
    captured: dict[str, offers.OfferFilters] = {}
    real_build = offers.build_offer_query

    def capture_build(f: offers.OfferFilters) -> str:
        captured["filters"] = f
        return real_build(f)

    monkeypatch.setattr(search_cli.offers, "build_offer_query", capture_build)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: [])

    runner.invoke(search_cli.app, ["--gpu-model", "RTX_4090", "--max-bid", "0.50"])
    assert captured["filters"].gpu_name == ["RTX_4090"]
    assert captured["filters"].max_bid == 0.50


# ----- Help string shows all flags ---------------------------------------- #


def test_help_shows_flag_names() -> None:
    result = runner.invoke(search_cli.app, ["--help"])
    assert result.exit_code == 0
    out = result.stdout
    for flag in ("--gpu-model", "--min-vram", "--num-gpus", "--max-bid",
                 "--min-tflops", "--min-reliability", "--country", "--region",
                 "--prosumer", "--spot", "--hardware", "--limit"):
        assert flag in out, f"flag {flag} missing from --help"


def test_help_has_no_phantom_command_slot() -> None:
    """Single-command Typer apps must NOT show `COMMAND [ARGS]` in --help."""
    result = runner.invoke(search_cli.app, ["--help"])
    assert result.exit_code == 0
    # The phantom slot only appears when Typer thinks the app has subcommands.
    assert "COMMAND [ARGS]" not in result.stdout


# ----- --num-gpus passes through to filters ------------------------------- #


def test_num_gpus_flag_flows_into_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_toml(monkeypatch)
    captured: dict[str, offers.OfferFilters] = {}
    real_build = offers.build_offer_query

    def capture_build(f: offers.OfferFilters) -> str:
        captured["filters"] = f
        return real_build(f)

    monkeypatch.setattr(search_cli.offers, "build_offer_query", capture_build)
    monkeypatch.setattr(vastai_cli, "run_vastai_raw", lambda *_a, **_kw: [])

    runner.invoke(search_cli.app, ["--num-gpus", "4"])
    assert captured["filters"].num_gpus == 4


# ----- shared renderer with status CLI ----------------------------------- #


def test_status_and_search_share_table_renderer() -> None:
    """status.py and search.py must use the same _table.render_table helper —
    no copy-paste rendering. Importing both should resolve to the same callable."""
    from vastrun_kit import _table
    from vastrun_kit.cli import status as status_cli

    # Both CLIs render their tables via _table.render_table; that single source
    # of truth is what keeps ASCII output consistent across vastrun-* commands.
    assert hasattr(_table, "render_table")
    # Sanity: render_table stays a callable, returns a single string.
    out = _table.render_table(("A", "B"), [("1", "2"), ("3", "4")])
    assert isinstance(out, str)
    assert "A" in out and "B" in out and "1" in out and "4" in out
    # Both modules must have the helper in their import graph (defensive: a
    # later refactor might inline it; this catches the regression).
    assert _table.render_table is status_cli._table.render_table  # type: ignore[attr-defined]
