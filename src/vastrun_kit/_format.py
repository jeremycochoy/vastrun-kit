"""Tiny shared formatters for uptime / cost columns.

Used by `cli/status.py` and `destroy.py`. Same math, single source of truth —
copy-paste duplicates were one diverging-format bug away from confusing the
agent that reads the output.
"""

from __future__ import annotations


def format_uptime(start: float | None, now: float) -> str:
    """`Hh Mm` (≥1h), `Mm` (<1h), or `-` (no start_date / clock skew)."""
    if start is None:
        return "-"
    mins = int((now - start) // 60)
    if mins < 0:
        return "-"
    return f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"


def format_money_per_hour(dph: float | None) -> str:
    """`$X.XXXX` or `-`."""
    return f"${dph:.4f}" if dph is not None else "-"


def format_spent(start: float | None, now: float, dph: float | None) -> str:
    """`$X.XX` (cumulative) or `-` (no inputs / clock skew)."""
    if start is None or dph is None or now < start:
        return "-"
    return f"${(now - start) / 3600 * dph:.2f}"
