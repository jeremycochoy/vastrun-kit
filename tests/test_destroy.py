"""Tests for `vastrun_kit.destroy` — verification + reporting helpers.

Behavioural contract from SPEC §"vastrun-destroy":

- `verify_destroyed`: poll `find_instance` for up to 30s (6 × 5s). True if the
  row is gone OR `actual_status` ∈ {exited, offline, destroyed, expired}.
  Otherwise fire ONE more `vastai destroy` and poll another 30s. Still alive
  → False.
- `render_destroyed_summary`: spec-formatted multi-line summary, missing
  fields render as `-` / `<none>`.
- `render_unverified_warning`: one-line spec'd warning.
- `bulk_heads_up_line`: `<id>  <label or '-'>  <num>× <gpu_name>  uptime <Hh Mm>
  spent $X.XX  (<status>)`.
"""

from __future__ import annotations

import time

import pytest

from vastrun_kit import destroy


# ----- helpers ----------------------------------------------------------- #


def _snap(**overrides: object) -> dict:
    """A populated instance snapshot. Override fields per-test."""
    base: dict = {
        "id": 12345,
        "label": "training-v1",
        "actual_status": "running",
        "gpu_name": "RTX 4090",
        "num_gpus": 2,
        "start_date": 1_700_000_000.0,
        "dph_total": 0.5,
    }
    base.update(overrides)
    return base


# ----- verify_destroyed -------------------------------------------------- #


def test_verify_destroyed_row_gone_first_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Row absent on the first poll → True, no second-destroy attempt."""
    calls = {"find": 0, "destroy": 0, "sleep": []}

    def fake_find(_id: int) -> dict | None:
        calls["find"] += 1
        return None

    def fake_destroy(args: list[str], **kw: object) -> tuple[int, str, str]:
        calls["destroy"] += 1
        return 0, "", ""

    def fake_sleep(s: float) -> None:
        calls["sleep"].append(s)

    monkeypatch.setattr(destroy.instances, "find_instance", fake_find)
    monkeypatch.setattr(destroy.vastai_cli, "run_vastai", fake_destroy)
    monkeypatch.setattr(destroy.time, "sleep", fake_sleep)

    assert destroy.verify_destroyed(12345) is True
    assert calls["find"] == 1
    assert calls["destroy"] == 0  # no second destroy issued
    assert calls["sleep"] == []  # exited before any sleep


def test_verify_destroyed_terminal_state_on_third_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row reaches a terminal status mid-poll → True without re-destroy."""
    calls = {"find": 0, "destroy": 0}
    states = ["running", "running", "exited"]

    def fake_find(_id: int) -> dict | None:
        idx = calls["find"]
        calls["find"] += 1
        return {"id": 12345, "actual_status": states[idx]}

    def fake_destroy(args: list[str], **kw: object) -> tuple[int, str, str]:
        calls["destroy"] += 1
        return 0, "", ""

    monkeypatch.setattr(destroy.instances, "find_instance", fake_find)
    monkeypatch.setattr(destroy.vastai_cli, "run_vastai", fake_destroy)
    monkeypatch.setattr(destroy.time, "sleep", lambda s: None)

    assert destroy.verify_destroyed(12345) is True
    assert calls["find"] == 3
    assert calls["destroy"] == 0


def test_verify_destroyed_offline_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        destroy.instances,
        "find_instance",
        lambda _id: {"id": 1, "actual_status": "offline"},
    )
    monkeypatch.setattr(destroy.vastai_cli, "run_vastai", lambda *a, **kw: (0, "", ""))
    monkeypatch.setattr(destroy.time, "sleep", lambda s: None)
    assert destroy.verify_destroyed(1) is True


def test_verify_destroyed_destroyed_status_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        destroy.instances,
        "find_instance",
        lambda _id: {"id": 1, "actual_status": "destroyed"},
    )
    monkeypatch.setattr(destroy.vastai_cli, "run_vastai", lambda *a, **kw: (0, "", ""))
    monkeypatch.setattr(destroy.time, "sleep", lambda s: None)
    assert destroy.verify_destroyed(1) is True


def test_verify_destroyed_expired_status_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        destroy.instances,
        "find_instance",
        lambda _id: {"id": 1, "actual_status": "expired"},
    )
    monkeypatch.setattr(destroy.vastai_cli, "run_vastai", lambda *a, **kw: (0, "", ""))
    monkeypatch.setattr(destroy.time, "sleep", lambda s: None)
    assert destroy.verify_destroyed(1) is True


def test_verify_destroyed_still_running_after_two_rounds_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six "running" polls → second destroy fires once; six more "running"
    polls → return False."""
    calls = {"find": 0, "destroy_argv": []}

    def fake_find(_id: int) -> dict | None:
        calls["find"] += 1
        return {"id": 12345, "actual_status": "running"}

    def fake_destroy(args: list[str], **kw: object) -> tuple[int, str, str]:
        calls["destroy_argv"].append(list(args))
        return 0, "", ""

    monkeypatch.setattr(destroy.instances, "find_instance", fake_find)
    monkeypatch.setattr(destroy.vastai_cli, "run_vastai", fake_destroy)
    monkeypatch.setattr(destroy.time, "sleep", lambda s: None)

    assert destroy.verify_destroyed(12345) is False
    # 6 polls in round 1 + 6 polls in round 2 = 12 polls
    assert calls["find"] == 12
    # Second destroy invoked exactly once between rounds
    assert calls["destroy_argv"] == [["destroy", "instance", "12345", "-y"]]


def test_verify_destroyed_recovers_in_second_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First 6 polls running → second destroy → next poll terminal → True.
    Confirms the second-round path verifies correctly."""
    calls = {"find": 0, "destroy": 0}

    def fake_find(_id: int) -> dict | None:
        calls["find"] += 1
        # 6 "running" then "exited"
        if calls["find"] <= 6:
            return {"id": 1, "actual_status": "running"}
        return {"id": 1, "actual_status": "exited"}

    def fake_destroy(args: list[str], **kw: object) -> tuple[int, str, str]:
        calls["destroy"] += 1
        return 0, "", ""

    monkeypatch.setattr(destroy.instances, "find_instance", fake_find)
    monkeypatch.setattr(destroy.vastai_cli, "run_vastai", fake_destroy)
    monkeypatch.setattr(destroy.time, "sleep", lambda s: None)

    assert destroy.verify_destroyed(1) is True
    assert calls["destroy"] == 1
    assert calls["find"] == 7


# ----- render_destroyed_summary ----------------------------------------- #


def test_render_destroyed_summary_full_snapshot() -> None:
    snap = _snap(start_date=time.time() - 7530, dph_total=0.5)
    out = destroy.render_destroyed_summary(snap)
    # Header
    assert "Destroyed instance 12345" in out
    # Label
    assert "Label:  training-v1" in out
    # GPU: count× name
    assert "GPU:    2× RTX 4090" in out
    # Uptime: 7530s = 2h 5m
    assert "Uptime: 2h 5m" in out
    # Spent: 7530s/3600 * 0.5 = 1.0458 → $1.05
    assert "Spent:  $1.05" in out


def test_render_destroyed_summary_none_snapshot_all_dashes() -> None:
    out = destroy.render_destroyed_summary(None)
    # Header still names <none> id? Spec just says "Destroyed instance <id>" —
    # but with no snap there's no id to render. The CLI passes the id, so
    # render_destroyed_summary itself shouldn't be called with None for
    # unknown id; still, spec says missing fields render as `-`/`<none>`.
    assert "<none>" in out or "-" in out
    # All four data lines present (Label / GPU / Uptime / Spent)
    assert "Label:" in out
    assert "GPU:" in out
    assert "Uptime:" in out
    assert "Spent:" in out


def test_render_destroyed_summary_missing_label_is_none() -> None:
    snap = _snap(label=None)
    out = destroy.render_destroyed_summary(snap)
    assert "Label:  <none>" in out


def test_render_destroyed_summary_missing_gpu_fields_dash() -> None:
    snap = _snap(num_gpus=None, gpu_name=None)
    out = destroy.render_destroyed_summary(snap)
    # GPU line falls back to `-` when both fields are missing
    assert "GPU:    -" in out


def test_render_destroyed_summary_missing_uptime_dash() -> None:
    snap = _snap(start_date=None)
    out = destroy.render_destroyed_summary(snap)
    assert "Uptime: -" in out


def test_render_destroyed_summary_missing_dph_dash() -> None:
    snap = _snap(dph_total=None, start_date=time.time() - 60)
    out = destroy.render_destroyed_summary(snap)
    assert "Spent:  -" in out


def test_render_destroyed_summary_minutes_only_format() -> None:
    snap = _snap(start_date=time.time() - 45 * 60)
    out = destroy.render_destroyed_summary(snap)
    # 45 minutes → "45m" (no hours)
    assert "Uptime: 45m" in out


# ----- render_unverified_warning ---------------------------------------- #


def test_render_unverified_warning_full_snapshot() -> None:
    snap = _snap(start_date=time.time() - 3600, dph_total=0.5)
    out = destroy.render_unverified_warning(snap, 12345)
    # Spec: "WARNING: destroy request sent for <id> (label '<l>', <n>× <gpu>,
    # spent $X.XX) but the API did not confirm termination..."
    assert "WARNING" in out
    assert "12345" in out
    assert "training-v1" in out
    assert "RTX 4090" in out
    assert "vastrun-status" in out


def test_render_unverified_warning_none_snapshot_uses_dashes() -> None:
    out = destroy.render_unverified_warning(None, 999)
    assert "WARNING" in out
    assert "999" in out
    # Should still produce a single line with the id and a recovery hint
    assert "vastrun-status" in out


# ----- bulk_heads_up_line ----------------------------------------------- #


def test_bulk_heads_up_line_full_row() -> None:
    inst = _snap(id=12345, label="x", num_gpus=4, gpu_name="A100",
                 start_date=time.time() - 7530, dph_total=1.0,
                 actual_status="running")
    line = destroy.bulk_heads_up_line(inst)
    # `<id>  <label>  <num>× <gpu_name>  uptime <Hh Mm>  spent $X.XX  (<status>)`
    assert line.startswith("12345  x  4× A100  uptime 2h 5m  spent $")
    assert line.endswith("(running)")


def test_bulk_heads_up_line_missing_label_dash() -> None:
    inst = _snap(label=None)
    line = destroy.bulk_heads_up_line(inst)
    assert "  -  " in line  # label slot is a dash


def test_bulk_heads_up_line_missing_status_unknown() -> None:
    inst = {"id": 7, "actual_status": None, "status_msg": None}
    line = destroy.bulk_heads_up_line(inst)
    # Falls back to a non-empty status token (e.g. `unknown`)
    assert line.endswith(")")
    # No empty parens
    assert "()" not in line


def test_bulk_heads_up_line_missing_gpu_dash() -> None:
    inst = _snap(num_gpus=None, gpu_name=None)
    line = destroy.bulk_heads_up_line(inst)
    # GPU slot collapses to `-`
    assert " -  uptime" in line


def test_bulk_heads_up_line_missing_uptime_and_spent_dash() -> None:
    inst = _snap(start_date=None, dph_total=None)
    line = destroy.bulk_heads_up_line(inst)
    assert "uptime -" in line
    assert "spent -" in line
