"""Plain-ASCII column-padded table renderer.

Shared by `vastrun-status` and `vastrun-search` so both produce the same agent-
readable output (no Unicode box drawing). All cells are stringified upstream;
this helper just measures and pads.
"""

from __future__ import annotations

from typing import Iterable, Sequence


def render_table(headers: Sequence[str], body: Iterable[Sequence[str]]) -> str:
    """Render `headers` and `body` as a whitespace-padded ASCII table.

    Each column's width is the max of its header and cells; columns are
    separated by two spaces. Returns a single multi-line string with no
    trailing newline.
    """
    rows = [tuple(headers), *(tuple(r) for r in body)]
    widths = [max(len(c) for c in col) for col in zip(*rows)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    return "\n".join(fmt.format(*r) for r in rows)
