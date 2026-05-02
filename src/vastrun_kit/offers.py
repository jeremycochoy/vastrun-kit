"""Pure offer-dict functions: query / filter / sort / no-match diagnostic."""

from __future__ import annotations

from dataclasses import dataclass

from . import config

DRIVER_FLOOR = (550, 0, 0)
# First-match-wins order: a row excluded by filter X is not re-counted by Y,
# so the binding-filter heuristic (max single count) is meaningful.
_FILTER_ORDER = (
    "max_bid", "driver_version", "cuda_max_good", "country", "region",
    "min_upload_mbps", "min_download_mbps", "min_tflops", "min_vram_gb",
    "gpu_name", "datacenter",
)
_NON_NEGOTIABLE = {"driver_version", "cuda_max_good"}


@dataclass(frozen=True)
class OfferFilters:
    gpu_name: list[str] | None = None
    min_vram_gb: int | None = None
    num_gpus: int = 1
    max_bid: float | None = None
    min_tflops: float | None = None
    min_reliability: float = config.RELIABILITY_MIN
    country: str | None = None
    region: str | None = None
    min_upload_mbps: float | None = None
    min_download_mbps: float | None = None
    min_disk_gb: int | None = None
    min_disk_bw: int | None = None
    prosumer: bool = False
    spot: bool = False

    def __post_init__(self) -> None:
        # Reliability is silently floored — spec forbids dropping below 0.95.
        if self.min_reliability < config.RELIABILITY_MIN:
            object.__setattr__(self, "min_reliability", config.RELIABILITY_MIN)


def _resolve_region(region: str | None) -> set[str] | None:
    if region is None:
        return None
    if region not in config.REGION_TO_COUNTRIES:
        raise ValueError(
            f"Unknown region '{region}'. Known: {sorted(config.REGION_TO_COUNTRIES)}"
        )
    return {c.upper() for c in config.REGION_TO_COUNTRIES[region]}


def build_offer_query(f: OfferFilters) -> str:
    _resolve_region(f.region)
    tpg = f.min_tflops if f.min_tflops is not None else config.TFLOPS_PER_GPU_MIN
    dl = f.min_download_mbps if f.min_download_mbps is not None else config.BANDWIDTH_DOWN_MBPS_MIN
    disk = f.min_disk_gb if f.min_disk_gb is not None else config.DISK_SPACE_GB_MIN
    disk_bw = f.min_disk_bw if f.min_disk_bw is not None else config.DISK_BW_MIN
    parts = [
        f"reliability>{f.min_reliability}",
        f"direct_port_count>={config.DIRECT_PORT_COUNT_MIN}",
        f"disk_space>={disk}", f"disk_bw>={disk_bw}", f"inet_down>{dl}",
        f"num_gpus>={f.num_gpus}", f"total_flops>={tpg * f.num_gpus}",
    ]
    if not f.prosumer:
        parts.append("datacenter=True")
    if f.min_upload_mbps is not None:
        parts.append(f"inet_up>{f.min_upload_mbps}")
    if f.min_vram_gb is not None:
        parts.append(f"gpu_ram>={f.min_vram_gb}")
    return " ".join(parts)


def _parse_driver(s: str) -> tuple[int, int, int] | None:
    if not s:
        return None  # Empty driver passes — API has already filtered by cuda_max_good.
    try:
        parts = [int(x) for x in s.split(".")[:3]]
    except ValueError:
        return None
    parts += [0] * (3 - len(parts))
    return tuple(parts)  # type: ignore[return-value]


def _excluded_by(row: dict, f: OfferFilters, region_set: set[str] | None) -> str | None:
    eff_max = f.max_bid if f.max_bid is not None else config.MAX_BID
    price = row.get("min_bid", 0) if f.spot else row.get("dph_total", 0)
    if (price or 0) > eff_max:
        return "max_bid"
    drv = _parse_driver(row.get("driver_version") or "")
    if drv is not None and drv < DRIVER_FLOOR:
        return "driver_version"
    cuda = row.get("cuda_max_good") or 0
    if cuda and cuda < config.CUDA_VERSION_MIN:
        return "cuda_max_good"
    cc = row.get("geolocation", "").rsplit(",", 1)[-1].strip().upper()
    if f.country is not None and cc != f.country.strip().upper():
        return "country"
    if region_set is not None and cc not in region_set:
        return "region"
    if f.min_upload_mbps is not None and (row.get("inet_up") or 0) < f.min_upload_mbps:
        return "min_upload_mbps"
    if f.min_download_mbps is not None and (row.get("inet_down") or 0) < f.min_download_mbps:
        return "min_download_mbps"
    tpg = f.min_tflops if f.min_tflops is not None else config.TFLOPS_PER_GPU_MIN
    n = max(1, int(row.get("num_gpus") or 1))
    if (row.get("total_flops") or 0) / n < tpg:
        return "min_tflops"
    if f.min_vram_gb is not None and (row.get("gpu_ram") or 0) / 1024 / n < f.min_vram_gb:
        return "min_vram_gb"
    if f.gpu_name:
        h = row.get("gpu_name", "").lower().replace("_", " ")
        if not any(p.lower().replace("_", " ") in h for p in f.gpu_name):
            return "gpu_name"
    return None


def filter_offers(rows: list[dict], f: OfferFilters) -> tuple[list[dict], list[tuple[str, int]]]:
    region_set = _resolve_region(f.region)
    counts = {n: 0 for n in _FILTER_ORDER}
    survivors: list[dict] = []
    for row in rows:
        which = _excluded_by(row, f, region_set)
        if which is None:
            survivors.append(row)
        else:
            counts[which] += 1
    return survivors, [(n, counts[n]) for n in _FILTER_ORDER]


def sort_offers(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (-(r.get("dlperf_per_dphtotal") or 0), r.get("min_bid") or 0))


# A user-chosen filter is one the user actively narrowed beyond the package floor;
# floors / on-by-default safety filters never qualify as binding, per the spec.
def _user_chose(name: str, f: OfferFilters) -> bool:
    return {
        "max_bid": f.max_bid is not None,
        "country": f.country is not None,
        "region": f.region is not None,
        "gpu_name": bool(f.gpu_name),
        "min_vram_gb": f.min_vram_gb is not None,
        "min_upload_mbps": f.min_upload_mbps is not None,
        "min_download_mbps": (
            f.min_download_mbps is not None
            and f.min_download_mbps > config.BANDWIDTH_DOWN_MBPS_MIN
        ),
        "min_tflops": f.min_tflops is not None and f.min_tflops > config.TFLOPS_PER_GPU_MIN,
    }.get(name, False)


def _label(name: str, f: OfferFilters) -> str:
    mb = f.max_bid if f.max_bid is not None else config.MAX_BID
    dl = f.min_download_mbps if f.min_download_mbps is not None else config.BANDWIDTH_DOWN_MBPS_MIN
    tpg = f.min_tflops if f.min_tflops is not None else config.TFLOPS_PER_GPU_MIN
    return {
        "max_bid": f"max_bid=${mb}/h", "driver_version": "driver ≥ 550",
        "cuda_max_good": f"CUDA ≥ {config.CUDA_VERSION_MIN}",
        "country": f"country={f.country}", "region": f"region={f.region}",
        "min_upload_mbps": f"min_upload_mbps={f.min_upload_mbps}",
        "min_download_mbps": f"min_download_mbps={dl}",
        "min_tflops": f"min_tflops={tpg}", "min_vram_gb": f"min_vram_gb={f.min_vram_gb}",
        "gpu_name": f"gpu_name={f.gpu_name}", "datacenter": "datacenter=True",
    }.get(name, name)


def _tag(name: str) -> str:
    if name in _NON_NEGOTIABLE:
        return " (safety floor — non-negotiable)"
    if name == "datacenter":
        return " (safety floor — opt out via --prosumer if you accept preemption risk)"
    return ""


def render_no_match_diagnostic(
    f: OfferFilters, total_candidates: int, exclusion_log: list[tuple[str, int]],
) -> str:
    counts = dict(exclusion_log)
    user_max = max(((counts.get(n, 0), n) for n in counts if _user_chose(n, f)), default=(0, ""))
    if user_max[0] > 0:
        c, name = user_max
        binding = f"Binding filter: {_label(name, f)} excluded {c} of {total_candidates} candidates."
    else:
        # No user-chosen filter zeroed offers — name the safety floor that did,
        # tagged so the agent doesn't read it as "fix this".
        c, name = max(
            ((counts.get(n, 0), n) for n in counts if not _user_chose(n, f)), default=(0, "")
        )
        binding = (
            f"Binding filter: {_label(name, f)}{_tag(name)} excluded {c} of {total_candidates} candidates."
            if c > 0 else f"No offers matched out of {total_candidates} candidates."
        )

    lines = [
        f"No offers matched ({total_candidates} candidates before filters).", binding, "",
        "Active filters:",
        f"  reliability ≥ {f.min_reliability} (safety floor — non-negotiable) excluded by query (server-side)",
        f"  direct ports ≥ {config.DIRECT_PORT_COUNT_MIN} (safety floor — non-negotiable) excluded by query (server-side)",
    ]
    for n in _FILTER_ORDER:
        if n == "datacenter" and f.prosumer:
            continue
        c = counts.get(n, 0)
        if n in _NON_NEGOTIABLE or n == "datacenter" or _user_chose(n, f) or c > 0:
            lines.append(f"  {_label(n, f)}{_tag(n)} excluded {c}")

    if counts.get("datacenter", 0) > 0 and not f.prosumer:
        lines += [
            "", "Possible (not recommended) actions:",
            "  --prosumer: drops datacenter=True. Prosumer hosts can disappear after "
            "a few hours; this trades reliability for selection.",
        ]
    return "\n".join(lines)
