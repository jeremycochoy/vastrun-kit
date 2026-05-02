"""Read-only enumeration of instances on the Vast.ai account.

Thin wrappers around `vastai show instances --raw`. `find_instance` walks the
returned list; if `list_instances` raises, the error propagates (callers like
the provision CLI's hardware-summary lookup catch and continue).
"""

from __future__ import annotations

from . import vastai_cli


def list_instances() -> list[dict]:
    """Return the parsed `vastai show instances --raw` payload (may be empty)."""
    return vastai_cli.run_vastai_raw(["show", "instances"])


def find_instance(inst_id: int) -> dict | None:
    """Return the row whose `id == inst_id`, or None if absent. Raises propagate."""
    for row in list_instances():
        if row.get("id") == inst_id:
            return row
    return None
