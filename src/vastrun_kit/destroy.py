"""`vastrun-destroy` verification + reporting helpers — single source of truth
for the destroy-side polling loop and post-destroy report rendering."""

from __future__ import annotations

import time

from . import _format, instances, vastai_cli

DESTROY_VERIFY_ATTEMPTS = 6
DESTROY_VERIFY_DELAY_SECONDS = 5
_TERMINAL_STATES = {"exited", "offline", "destroyed", "expired"}


def _is_terminal(inst: dict | None) -> bool:
    return inst is None or (inst.get("actual_status") or "") in _TERMINAL_STATES


def _poll_until_terminal(inst_id: int) -> bool:
    """6 × 5s polls of `find_instance`. True if it reaches a terminal state."""
    for i in range(DESTROY_VERIFY_ATTEMPTS):
        if _is_terminal(instances.find_instance(inst_id)):
            return True
        if i < DESTROY_VERIFY_ATTEMPTS - 1:
            time.sleep(DESTROY_VERIFY_DELAY_SECONDS)
    return False


def verify_destroyed(inst_id: int) -> bool:
    """Poll for up to 30s; if still alive, fire one more `vastai destroy` and
    poll another 30s. Still alive after that → False."""
    if _poll_until_terminal(inst_id):
        return True
    vastai_cli.run_vastai(["destroy", "instance", str(inst_id)])
    return _poll_until_terminal(inst_id)


def _gpu_cell(s: dict) -> str:
    n, name = s.get("num_gpus"), s.get("gpu_name")
    return f"{n}× {name}" if n and name else "-"


def render_destroyed_summary(snap: dict | None) -> str:
    """Spec multi-line summary; missing fields render as `-`/`<none>`."""
    s = snap or {}
    now = time.time()
    return (
        f"Destroyed instance {s.get('id', '-')}\n"
        f"  Label:  {s.get('label') or '<none>'}\n"
        f"  GPU:    {_gpu_cell(s)}\n"
        f"  Uptime: {_format.format_uptime(s.get('start_date'), now)}\n"
        f"  Spent:  {_format.format_spent(s.get('start_date'), now, s.get('dph_total'))}"
    )


def render_unverified_warning(snap: dict | None, inst_id: int) -> str:
    """One-line spec warning destined for stderr."""
    s = snap or {}
    spent = _format.format_spent(s.get("start_date"), time.time(), s.get("dph_total"))
    return (
        f"WARNING: destroy request sent for {inst_id} "
        f"(label '{s.get('label') or 'none'}', {_gpu_cell(s)}, spent {spent}) "
        f"but the API did not confirm termination — the instance may still be "
        f"billing. Check with: vastrun-status"
    )


def bulk_heads_up_line(inst: dict) -> str:
    """`<id>  <label or '-'>  <num>× <gpu_name>  uptime <Hh Mm>  spent $X.XX  (<status>)`."""
    now = time.time()
    status = inst.get("actual_status") or inst.get("status_msg") or "unknown"
    return (
        f"{inst.get('id', '-')}  {inst.get('label') or '-'}  {_gpu_cell(inst)}  "
        f"uptime {_format.format_uptime(inst.get('start_date'), now)}  "
        f"spent {_format.format_spent(inst.get('start_date'), now, inst.get('dph_total'))}  "
        f"({status})"
    )
