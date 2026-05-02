"""Provision-flow helpers — pure orchestration. The CLI layer composes these."""

from __future__ import annotations

import json
import re

from . import config, errors, instances, offers, vastai_cli

# Match `new_contract` in raw JSON, or `instance id is <N>` in plain stdout.
_INST_ID_RE = re.compile(r"(?:['\"]new_contract['\"]\s*:\s*|instance\s+id\s+is\s+)(\d+)", re.IGNORECASE)
_RECOVER = "Run `vastrun-status` and `vastrun-destroy <id> --force` if leaked."


def _search_offers(query: str) -> list[dict]:
    payload = vastai_cli.run_vastai_raw(["search", "offers", query, "--limit", "10000"])
    return payload[0] if (payload and isinstance(payload[0], list)) else payload


def resolve_offer(offer_id: int) -> dict:
    """Find offer dict by `id`. Vast.ai's `search offers id=X` is a no-op —
    the `id` query field refers to instance ids, not offer/contract ids,
    and the API only returns a scored slice for any single search.

    The two scored slices (datacenter and non-datacenter) are mostly
    disjoint, so we query both and pick the row whose `id` matches.
    Filtering on `id` means we ALWAYS return the user's specific offer
    or fail; we never substitute a different machine. (See
    docs/vastai-cli-quirks.md.)"""
    for f in (offers.OfferFilters(), offers.OfferFilters(prosumer=True)):
        for r in _search_offers(offers.build_offer_query(f)):
            if r.get("id") == offer_id:
                return r
    raise errors.OfferUnavailableError(
        f"Offer {offer_id} is no longer available. Run `vastrun-search` to list current offers."
    )


def resolve_image(offer: dict, *, override: str | None) -> str:
    """`override` (must contain ':') wins; else Blackwell→BLACKWELL_IMAGE; else IMAGE."""
    if override is not None:
        if ":" not in override:
            raise ValueError(f"Invalid --image '{override}': must contain a tag (repo:tag form).")
        return override
    gpu = offer.get("gpu_name", "")
    blackwell = any(gpu.startswith(p) for p in config.BLACKWELL_GPU_PREFIXES)
    return config.BLACKWELL_IMAGE if blackwell else config.IMAGE


def check_balance() -> float:
    """`vastai show user --raw` → float credit."""
    return float(vastai_cli.run_vastai_raw(["show", "user"]).get("credit") or 0)


def compute_spot_bid(offer: dict, *, max_bid_cap: float = config.MAX_BID) -> float:
    """`min(offer['min_bid'] * BID_MULTIPLIER, max_bid_cap)`."""
    return min(float(offer.get("min_bid") or 0) * config.BID_MULTIPLIER, max_bid_cap)


def hardware_summary(inst_id: int) -> str | None:
    """Best-effort `Nx GPU · VRAM · TFLOPS/GPU · geo`. None on failure / missing."""
    try:
        inst = instances.find_instance(inst_id)
    except (RuntimeError, errors.VastaiCliError):
        return None
    if not inst:
        return None
    n = max(1, int(inst.get("num_gpus") or 1))
    g, v, t, loc = (inst.get(k) for k in ("gpu_name", "gpu_ram", "total_flops", "geolocation"))
    parts = [s for s in (f"{n}x {g}" if g else None, f"{int(v / n / 1024)}GB" if v else None,
                         f"{t / n:.1f} TFLOPS/GPU" if t else None, loc or None) if s]
    return " · ".join(parts) if parts else None


def create_instance(
    offer_id: int, *, image: str, label: str, ssh_pubkey: str, spot_bid: float | None,
) -> int:
    """One `vastai create instance` call → new instance id. Empty stdout / no id → VastaiCliError."""
    argv = ["create", "instance", str(offer_id), "--ssh", "--direct",
            "--disk", str(config.DISK_GB), "--label", label, "--image", image]
    if spot_bid is not None:
        argv += ["--bid_price", f"{spot_bid:.2f}"]
    rc, out, err = vastai_cli.run_vastai(argv)
    pretty = f"`vastai create instance {offer_id}`"
    if not out.strip():
        raise errors.VastaiCliError(
            f"{pretty} returned empty stdout (CLI bug). It may have created silently. {_RECOVER}"
        )
    if rc != 0:
        msg = (err.strip() or out.strip()).splitlines()[0]
        raise errors.VastaiCliError(f"{pretty} failed: {msg}")
    try:
        data = json.loads(out)
        if isinstance(data, dict) and data.get("new_contract"):
            return int(data["new_contract"])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    if m := _INST_ID_RE.search(out):
        return int(m.group(1))
    raise errors.VastaiCliError(
        f"{pretty} did not return an instance id. stdout={out.strip()!r}. {_RECOVER}"
    )
