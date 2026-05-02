"""Tests for `vastrun-status` (single-shot, --json, --watch).

Behavioural contract from SPEC §"vastrun-status":
- Default: single-shot, non-blocking. Prints a plain-text (no box-drawing) table.
- Empty list → `No running instances found.`, no table, exit 0.
- Footer: `Total: N instance(s)` (singular when N == 1).
- `--json` → `json.dumps(<list>)` of the raw payload, exit 0.
- `--watch` → clear-and-reprint loop; KeyboardInterrupt exits 0.
- `--interval` < 1 → exit 1.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from vastrun_kit import errors, instances
from vastrun_kit.cli import status as status_cli

runner = CliRunner()


# ----- helpers ------------------------------------------------------------- #


def _row(**overrides: object) -> dict:
    """A fully-populated instance row stub. Override fields per-test."""
    base = {
        "id": 12345,
        "label": "training-v1",
        "actual_status": "running",
        "gpu_name": "RTX 4090",
        "start_date": 1_700_000_000.0,
        "dph_total": 0.5,
        "ssh_host": "ssh5.vast.ai",
        "ssh_port": 22000,
    }
    base.update(overrides)
    return base


# ----- single-shot empty list --------------------------------------------- #


def test_single_shot_empty_list_prints_message_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instances, "list_instances", lambda: [])
    result = runner.invoke(status_cli.app, [])
    assert result.exit_code == 0
    assert "No running instances found." in result.stdout
    # No table → no footer
    assert "Total:" not in result.stdout


# ----- single-shot one row ------------------------------------------------- #


def test_single_shot_one_row_renders_all_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row()]
    # Pin "now" so uptime/spent are deterministic. start_date + 7530s = 2h 5m, $1.0458 spent.
    monkeypatch.setattr(instances, "list_instances", lambda: rows)
    monkeypatch.setattr(status_cli.time, "time", lambda: 1_700_000_000.0 + 7530)

    result = runner.invoke(status_cli.app, [])
    assert result.exit_code == 0
    out = result.stdout

    # Headers present
    for header in (
        "ID",
        "Label",
        "Status",
        "GPU",
        "Uptime",
        "Cost/h",
        "Spent",
        "SSH Address",
    ):
        assert header in out

    # Cell values
    assert "12345" in out
    assert "training-v1" in out
    assert "running" in out
    assert "RTX 4090" in out
    assert "2h 5m" in out
    assert "$0.5000" in out
    assert "$1.05" in out
    assert "ssh5.vast.ai:22000" in out

    # Footer (singular)
    assert "Total: 1 instance(s)" in out

    # NO Unicode box-drawing characters
    for ch in ("│", "┌", "─", "└", "┐", "┘"):
        assert ch not in out, f"unexpected box char {ch!r} in output"


def test_single_shot_missing_fields_render_dashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "id": 99,
            # no label, no gpu_name, no start_date, no dph_total, no ssh info
            "status_msg": "loading docker image",
        }
    ]
    monkeypatch.setattr(instances, "list_instances", lambda: rows)
    result = runner.invoke(status_cli.app, [])
    assert result.exit_code == 0
    out = result.stdout

    # Status falls back to status_msg when actual_status absent
    assert "loading docker image" in out
    # Label, GPU, Uptime, Cost/h, Spent, SSH all `-`
    assert " - " in out  # at least one dash cell
    # Should not crash — the row renders
    assert "99" in out
    assert "Total: 1 instance(s)" in out


# ----- single-shot multi-row ---------------------------------------------- #


def test_single_shot_multi_row_footer_uses_plural(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(id=1), _row(id=2, label="other")]
    monkeypatch.setattr(instances, "list_instances", lambda: rows)
    monkeypatch.setattr(status_cli.time, "time", lambda: 1_700_000_000.0 + 60)

    result = runner.invoke(status_cli.app, [])
    assert result.exit_code == 0
    assert "Total: 2 instance(s)" in result.stdout
    assert "other" in result.stdout


# ----- --json ------------------------------------------------------------- #


def test_json_flag_outputs_raw_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row(id=7), _row(id=8)]
    monkeypatch.setattr(instances, "list_instances", lambda: rows)
    result = runner.invoke(status_cli.app, ["--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed == rows
    # No human table when --json
    assert "Total:" not in result.stdout


def test_json_flag_with_empty_list_outputs_empty_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instances, "list_instances", lambda: [])
    result = runner.invoke(status_cli.app, ["--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


# ----- --watch ------------------------------------------------------------ #


def test_watch_interval_zero_is_rejected() -> None:
    result = runner.invoke(status_cli.app, ["--watch", "--interval", "0"])
    assert result.exit_code == 1
    assert "interval" in result.stdout.lower() or "interval" in (
        result.stderr or ""
    ).lower()


def test_watch_interval_negative_is_rejected() -> None:
    result = runner.invoke(status_cli.app, ["--watch", "--interval", "-3"])
    assert result.exit_code == 1


def test_watch_loops_then_keyboard_interrupt_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instances, "list_instances", lambda: [_row()])
    monkeypatch.setattr(status_cli.time, "time", lambda: 1_700_000_000.0 + 60)

    def fake_sleep(_s: float) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(status_cli.time, "sleep", fake_sleep)

    result = runner.invoke(status_cli.app, ["--watch", "--interval", "1"])
    assert result.exit_code == 0
    # Output should include the table from the first tick
    assert "12345" in result.stdout
    assert "Total: 1 instance(s)" in result.stdout


# ----- error handling ----------------------------------------------------- #


def test_missing_credential_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> list[dict]:
        raise errors.MissingCredentialError("VASTAI_API_TOKEN missing")

    monkeypatch.setattr(instances, "list_instances", boom)
    result = runner.invoke(status_cli.app, [])
    assert result.exit_code == 1
    # Message goes to stderr per CliRunner; mix_stderr=True bundles it into stdout
    combined = result.stdout + (result.stderr or "")
    assert "VASTAI_API_TOKEN" in combined


def test_vastai_cli_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> list[dict]:
        raise errors.VastaiCliError("vastai show instances failed: timeout")

    monkeypatch.setattr(instances, "list_instances", boom)
    result = runner.invoke(status_cli.app, [])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "timeout" in combined


# ----- formatter unit tests ----------------------------------------------- #


def test_format_uptime_hours_minutes() -> None:
    # 2h 15m = 8100s
    assert status_cli._format_uptime(0.0, 8100.0) == "2h 15m"


def test_format_uptime_minutes_only() -> None:
    # 45m = 2700s
    assert status_cli._format_uptime(0.0, 2700.0) == "45m"


def test_format_uptime_zero_minutes() -> None:
    assert status_cli._format_uptime(0.0, 30.0) == "0m"


def test_format_uptime_missing_start_returns_dash() -> None:
    assert status_cli._format_uptime(None, 100.0) == "-"
    assert status_cli._format_uptime(0.0, 100.0) != "-"  # start=0 is still a valid epoch  # noqa: E501


def test_format_uptime_clock_skew_returns_dash() -> None:
    # now < start_date → negative; render as dash, don't blow up
    assert status_cli._format_uptime(100.0, 50.0) == "-"


def test_format_money_per_hour_four_decimals() -> None:
    assert status_cli._format_money_per_hour(0.5) == "$0.5000"
    assert status_cli._format_money_per_hour(1.234567) == "$1.2346"


def test_format_money_per_hour_missing_returns_dash() -> None:
    assert status_cli._format_money_per_hour(None) == "-"


def test_format_spent_two_decimals() -> None:
    # 1 hour at $0.50/h = $0.50
    assert status_cli._format_spent(0.0, 3600.0, 0.5) == "$0.50"


def test_format_spent_missing_returns_dash() -> None:
    assert status_cli._format_spent(None, 100.0, 0.5) == "-"
    assert status_cli._format_spent(0.0, 100.0, None) == "-"


def test_format_spent_negative_uptime_returns_dash() -> None:
    assert status_cli._format_spent(100.0, 50.0, 0.5) == "-"


def test_format_ssh_address_present() -> None:
    inst = {"ssh_host": "h.example", "ssh_port": 22}
    assert status_cli._format_ssh_address(inst) == "h.example:22"


def test_format_ssh_address_missing() -> None:
    assert status_cli._format_ssh_address({}) == "-"


# ----- output is ASCII-only ----------------------------------------------- #


def test_rendered_table_is_ascii_only(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row(), _row(id=2)]
    monkeypatch.setattr(instances, "list_instances", lambda: rows)
    monkeypatch.setattr(status_cli.time, "time", lambda: 1_700_000_000.0 + 60)

    result = runner.invoke(status_cli.app, [])
    assert result.exit_code == 0
    # All rendered characters must be ASCII (< 128)
    for ch in result.stdout:
        assert ord(ch) < 128, f"non-ASCII char {ch!r} (U+{ord(ch):04X}) in output"
