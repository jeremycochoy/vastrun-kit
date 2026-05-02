"""Tests for offers.py — query composition, post-filter, sort, no-match diagnostic."""

from __future__ import annotations

import pytest

from vastrun_kit import config
from vastrun_kit.offers import (
    OfferFilters,
    build_offer_query,
    filter_offers,
    render_no_match_diagnostic,
    sort_offers,
)


# ---------- fixtures ----------

def _row(**over) -> dict:
    base = {
        "id": 1,
        "gpu_name": "RTX 4090",
        "num_gpus": 1,
        "gpu_ram": 24576,           # MB total across all GPUs
        "total_flops": 100.0,        # TFLOPS across all GPUs
        "dph_total": 0.40,
        "min_bid": 0.30,
        "driver_version": "565.77.01",
        "cuda_max_good": 12.6,
        "geolocation": "United States, US",
        "inet_up": 800,
        "inet_down": 1500,
        "dlperf_per_dphtotal": 50.0,
    }
    base.update(over)
    return base


# ---------- OfferFilters.min_reliability floor ----------

def test_filters_floors_reliability_below_safety_floor():
    f = OfferFilters(min_reliability=0.5)
    assert f.min_reliability == config.RELIABILITY_MIN


def test_filters_keeps_reliability_at_or_above_floor():
    f = OfferFilters(min_reliability=0.99)
    assert f.min_reliability == 0.99


def test_filters_unknown_region_raises_in_query():
    f = OfferFilters(region="MARS")
    with pytest.raises(ValueError, match="MARS"):
        build_offer_query(f)


# ---------- build_offer_query ----------

def test_query_default_includes_all_safety_floors_and_datacenter():
    q = build_offer_query(OfferFilters())
    assert f"reliability>{config.RELIABILITY_MIN}" in q
    assert f"direct_port_count>={config.DIRECT_PORT_COUNT_MIN}" in q
    assert f"disk_space>={config.DISK_SPACE_GB_MIN}" in q
    assert f"disk_bw>={config.DISK_BW_MIN}" in q
    assert f"inet_down>{config.BANDWIDTH_DOWN_MBPS_MIN}" in q
    assert "datacenter=True" in q
    assert "num_gpus>=1" in q
    assert f"total_flops>={config.TFLOPS_PER_GPU_MIN * 1}" in q


def test_query_drops_datacenter_in_prosumer_mode():
    q = build_offer_query(OfferFilters(prosumer=True))
    assert "datacenter=True" not in q


def test_query_emits_inet_up_only_when_set():
    q1 = build_offer_query(OfferFilters())
    assert "inet_up" not in q1
    q2 = build_offer_query(OfferFilters(min_upload_mbps=300))
    assert "inet_up>300" in q2


def test_query_emits_gpu_ram_only_when_min_vram_set():
    q = build_offer_query(OfferFilters(min_vram_gb=80))
    assert "gpu_ram>=80" in q
    q2 = build_offer_query(OfferFilters())
    assert "gpu_ram" not in q2


def test_query_total_flops_scales_with_num_gpus():
    f = OfferFilters(num_gpus=4, min_tflops=100.0)
    q = build_offer_query(f)
    assert "total_flops>=400" in q  # 100 * 4


# ---------- filter_offers: per-filter rejections ----------

def test_filter_rejects_max_bid_overrun():
    rows = [_row(dph_total=2.0)]
    f = OfferFilters(max_bid=0.5)
    survivors, log = filter_offers(rows, f)
    assert survivors == []
    assert ("max_bid", 1) in log


def test_filter_uses_min_bid_in_spot_mode():
    rows = [_row(dph_total=2.0, min_bid=0.20)]
    f = OfferFilters(spot=True, max_bid=0.5)
    survivors, _ = filter_offers(rows, f)
    assert survivors == rows  # spot price ($0.20) is under cap, on-demand price ignored


def test_filter_falls_back_to_max_bid_default():
    rows = [_row(dph_total=config.MAX_BID + 0.01)]
    survivors, log = filter_offers(rows, OfferFilters())
    assert survivors == []
    assert log[0][0] == "max_bid"


def test_filter_rejects_low_driver_version():
    rows = [_row(driver_version="540.00.00")]
    survivors, log = filter_offers(rows, OfferFilters())
    assert survivors == []
    assert any(name == "driver_version" for name, _ in log)


def test_filter_passes_empty_driver_version():
    # Missing driver_version is OK — API already filtered by cuda_max_good.
    rows = [_row(driver_version="")]
    survivors, _ = filter_offers(rows, OfferFilters())
    assert len(survivors) == 1


def test_filter_passes_three_digit_driver_version():
    rows = [_row(driver_version="590.48.01")]
    survivors, _ = filter_offers(rows, OfferFilters())
    assert len(survivors) == 1


def test_filter_rejects_low_cuda_max_good():
    rows = [_row(cuda_max_good=12.0)]
    survivors, log = filter_offers(rows, OfferFilters())
    assert survivors == []
    counts = dict(log)
    assert counts["cuda_max_good"] == 1


def test_filter_passes_zero_cuda_max_good():
    rows = [_row(cuda_max_good=0)]
    survivors, _ = filter_offers(rows, OfferFilters())
    assert len(survivors) == 1


def test_filter_rejects_country_mismatch():
    rows = [_row(geolocation="Germany, DE")]
    f = OfferFilters(country="US")
    survivors, log = filter_offers(rows, f)
    assert survivors == []
    assert any(name == "country" for name, _ in log)


def test_filter_country_match_case_insensitive():
    rows = [_row(geolocation="United States, US")]
    f = OfferFilters(country="us")
    survivors, _ = filter_offers(rows, f)
    assert len(survivors) == 1


def test_filter_rejects_outside_region_set():
    rows = [_row(geolocation="United States, US")]
    f = OfferFilters(region="EU")
    survivors, log = filter_offers(rows, f)
    assert survivors == []
    assert any(name == "region" for name, _ in log)


def test_filter_passes_in_region_set():
    rows = [_row(geolocation="Germany, DE")]
    f = OfferFilters(region="EU")
    survivors, _ = filter_offers(rows, f)
    assert len(survivors) == 1


def test_filter_unknown_region_raises():
    rows = [_row()]
    f = OfferFilters(region="MARS")
    with pytest.raises(ValueError, match="MARS"):
        filter_offers(rows, f)


def test_filter_rejects_inet_below_user_floor():
    rows = [_row(inet_up=100)]
    f = OfferFilters(min_upload_mbps=500)
    survivors, log = filter_offers(rows, f)
    assert survivors == []
    assert any(name == "min_upload_mbps" for name, _ in log)


def test_filter_rejects_inet_down_below_user_floor():
    rows = [_row(inet_down=200)]
    f = OfferFilters(min_download_mbps=1000)
    survivors, log = filter_offers(rows, f)
    assert survivors == []
    assert any(name == "min_download_mbps" for name, _ in log)


def test_filter_rejects_per_gpu_tflops_below_floor():
    rows = [_row(num_gpus=2, total_flops=100.0)]  # 50 TFLOPS / GPU
    f = OfferFilters(num_gpus=2, min_tflops=80.0)
    survivors, log = filter_offers(rows, f)
    assert survivors == []
    assert any(name == "min_tflops" for name, _ in log)


def test_filter_rejects_per_gpu_vram_below_min():
    # 16 GB total, 2 GPUs => 8 GB / GPU
    rows = [_row(num_gpus=2, gpu_ram=16384)]
    f = OfferFilters(num_gpus=2, min_vram_gb=24)
    survivors, log = filter_offers(rows, f)
    assert survivors == []
    assert any(name == "min_vram_gb" for name, _ in log)


def test_filter_passes_per_gpu_vram_at_or_above_min():
    # 24 GB / GPU
    rows = [_row(num_gpus=1, gpu_ram=24576)]
    f = OfferFilters(num_gpus=1, min_vram_gb=24)
    survivors, _ = filter_offers(rows, f)
    assert len(survivors) == 1


def test_filter_rejects_gpu_name_mismatch():
    rows = [_row(gpu_name="RTX 3060")]
    f = OfferFilters(gpu_name=["A100", "H100"])
    survivors, log = filter_offers(rows, f)
    assert survivors == []
    assert any(name == "gpu_name" for name, _ in log)


def test_filter_passes_gpu_name_substring_with_underscore():
    rows = [_row(gpu_name="RTX 4090")]
    f = OfferFilters(gpu_name=["rtx_4090"])  # underscore↔space, case-insensitive
    survivors, _ = filter_offers(rows, f)
    assert len(survivors) == 1


def test_filter_passes_gpu_name_or_match():
    rows = [_row(gpu_name="H100 SXM5")]
    f = OfferFilters(gpu_name=["A100", "H100"])
    survivors, _ = filter_offers(rows, f)
    assert len(survivors) == 1


def test_filter_first_match_wins_in_exclusion_log():
    # max_bid fires first; even though driver_version is also bad, only max_bid is counted.
    rows = [_row(dph_total=2.0, driver_version="540.0.0")]
    _, log = filter_offers(rows, OfferFilters(max_bid=0.5))
    counts = {name: c for name, c in log}
    assert counts.get("max_bid") == 1
    assert counts.get("driver_version", 0) == 0


def test_filter_happy_path_keeps_all():
    rows = [_row(), _row(id=2)]
    survivors, log = filter_offers(rows, OfferFilters())
    assert len(survivors) == 2
    # Every per-filter count should be 0 in the happy path.
    assert all(c == 0 for _, c in log)


def test_filter_log_has_stable_ordered_names():
    rows = [_row()]
    _, log = filter_offers(rows, OfferFilters())
    names = [name for name, _ in log]
    # max_bid runs before driver_version which runs before cuda_max_good in the spec order.
    assert names.index("max_bid") < names.index("driver_version")
    assert names.index("driver_version") < names.index("cuda_max_good")


# ---------- sort_offers ----------

def test_sort_descends_by_dlperf_per_dphtotal():
    rows = [_row(id=1, dlperf_per_dphtotal=10.0), _row(id=2, dlperf_per_dphtotal=50.0)]
    out = sort_offers(rows)
    assert [r["id"] for r in out] == [2, 1]


def test_sort_breaks_ties_with_min_bid_ascending():
    rows = [
        _row(id=1, dlperf_per_dphtotal=10.0, min_bid=0.50),
        _row(id=2, dlperf_per_dphtotal=10.0, min_bid=0.20),
    ]
    out = sort_offers(rows)
    assert [r["id"] for r in out] == [2, 1]


def test_sort_treats_missing_keys_as_zero():
    rows = [{"id": 1}, _row(id=2, dlperf_per_dphtotal=5.0)]
    out = sort_offers(rows)
    assert [r["id"] for r in out] == [2, 1]


def test_sort_returns_new_list():
    rows = [_row(id=1, dlperf_per_dphtotal=1.0), _row(id=2, dlperf_per_dphtotal=2.0)]
    original = list(rows)
    sort_offers(rows)
    assert rows == original


# ---------- render_no_match_diagnostic ----------

def test_no_match_names_user_chosen_binding_filter():
    f = OfferFilters(max_bid=0.5)
    log = [
        ("max_bid", 80),
        ("driver_version", 5),
        ("cuda_max_good", 0),
        ("country", 0),
        ("region", 0),
        ("min_upload_mbps", 0),
        ("min_download_mbps", 0),
        ("min_tflops", 2),
        ("min_vram_gb", 0),
        ("gpu_name", 0),
        ("datacenter", 0),
    ]
    text = render_no_match_diagnostic(f, total_candidates=92, exclusion_log=log)
    assert "Binding filter" in text
    assert "max_bid" in text
    assert "92" in text  # total candidates
    assert "80" in text  # excluded count


def test_no_match_safety_floors_get_non_negotiable_tag():
    f = OfferFilters()
    log = [("max_bid", 0), ("driver_version", 0), ("cuda_max_good", 0),
           ("country", 0), ("region", 0), ("min_upload_mbps", 0),
           ("min_download_mbps", 0), ("min_tflops", 0), ("min_vram_gb", 0),
           ("gpu_name", 0), ("datacenter", 0)]
    text = render_no_match_diagnostic(f, total_candidates=10, exclusion_log=log)
    assert "reliability" in text and "0.95" in text
    assert "(safety floor — non-negotiable)" in text
    assert "driver" in text and "550" in text
    assert "CUDA" in text and "12.4" in text
    assert "direct ports" in text


def test_no_match_user_raised_reliability_drops_non_negotiable_tag():
    # When the user raises min_reliability above the 0.95 floor, the listing
    # must treat it as user-chosen (no "non-negotiable" tag) — only the floor
    # itself is non-negotiable, not the user's stricter threshold.
    f = OfferFilters(min_reliability=0.97)
    log = [("max_bid", 0), ("driver_version", 0), ("cuda_max_good", 0),
           ("country", 0), ("region", 0), ("min_upload_mbps", 0),
           ("min_download_mbps", 0), ("min_tflops", 0), ("min_vram_gb", 0),
           ("gpu_name", 0), ("datacenter", 0)]
    text = render_no_match_diagnostic(f, total_candidates=10, exclusion_log=log)
    rel_line = next(line for line in text.splitlines() if "reliability" in line)
    assert "0.97" in rel_line
    assert "non-negotiable" not in rel_line


def test_no_match_user_raised_reliability_can_be_binding_filter():
    # SPEC §no-match diagnostic explicitly lists `min_reliability above the
    # safety floor` as a candidate for the binding filter.
    f = OfferFilters(min_reliability=0.99)
    log = [("max_bid", 0), ("driver_version", 0), ("cuda_max_good", 0),
           ("country", 0), ("region", 0), ("min_upload_mbps", 0),
           ("min_download_mbps", 0), ("min_tflops", 0), ("min_vram_gb", 0),
           ("gpu_name", 0), ("datacenter", 0), ("min_reliability", 60)]
    text = render_no_match_diagnostic(f, total_candidates=80, exclusion_log=log)
    binding_line = next(line for line in text.splitlines() if "Binding filter" in line)
    assert "reliability" in binding_line
    assert "0.99" in binding_line


def test_no_match_datacenter_zero_offers_lists_prosumer_as_possible_not_recommended():
    f = OfferFilters()
    log = [("max_bid", 0), ("driver_version", 0), ("cuda_max_good", 0),
           ("country", 0), ("region", 0), ("min_upload_mbps", 0),
           ("min_download_mbps", 0), ("min_tflops", 0), ("min_vram_gb", 0),
           ("gpu_name", 0), ("datacenter", 50)]
    text = render_no_match_diagnostic(f, total_candidates=50, exclusion_log=log)
    assert "datacenter" in text
    assert "--prosumer" in text
    assert "preemption" in text or "non-datacenter" in text or "opt out" in text
    # Must not phrase it as a recommendation:
    assert "recommended" not in text.lower() or "not recommended" in text.lower() \
        or "possible" in text.lower()


def test_no_match_datacenter_excluded_all_named_as_binding_when_no_user_filter():
    f = OfferFilters()
    log = [("max_bid", 0), ("driver_version", 0), ("cuda_max_good", 0),
           ("country", 0), ("region", 0), ("min_upload_mbps", 0),
           ("min_download_mbps", 0), ("min_tflops", 0), ("min_vram_gb", 0),
           ("gpu_name", 0), ("datacenter", 12)]
    text = render_no_match_diagnostic(f, total_candidates=12, exclusion_log=log)
    # When the only zeroing filter is a safety floor (datacenter), the spec wants
    # it named — but framed as a non-recommendation.
    assert "datacenter" in text


def test_no_match_user_chosen_filter_preferred_over_safety_floor_with_higher_count():
    # Even if a safety floor excluded more, the binding filter should be the
    # user-chosen one (so the agent sees a *safe* relaxation).
    f = OfferFilters(country="US")
    log = [("max_bid", 0), ("driver_version", 100), ("cuda_max_good", 0),
           ("country", 5), ("region", 0), ("min_upload_mbps", 0),
           ("min_download_mbps", 0), ("min_tflops", 0), ("min_vram_gb", 0),
           ("gpu_name", 0), ("datacenter", 0)]
    text = render_no_match_diagnostic(f, total_candidates=100, exclusion_log=log)
    # Binding filter line should call out country, not driver_version.
    binding_line = next(line for line in text.splitlines() if "Binding filter" in line)
    assert "country" in binding_line
    assert "driver" not in binding_line


def test_no_match_lists_total_and_per_step_survivor_counts():
    f = OfferFilters(max_bid=0.5)
    log = [("max_bid", 80), ("driver_version", 5), ("cuda_max_good", 0),
           ("country", 0), ("region", 0), ("min_upload_mbps", 0),
           ("min_download_mbps", 0), ("min_tflops", 7), ("min_vram_gb", 0),
           ("gpu_name", 0), ("datacenter", 0)]
    text = render_no_match_diagnostic(f, total_candidates=92, exclusion_log=log)
    # Total candidates appears.
    assert "92" in text
    # Each non-zero exclusion appears with its count.
    assert "80" in text
    assert "5" in text
    assert "7" in text


def test_no_match_user_chosen_filter_has_no_safety_tag():
    # max_bid is user-chosen here.
    f = OfferFilters(max_bid=0.5)
    log = [("max_bid", 5)] + [(n, 0) for n in (
        "driver_version", "cuda_max_good", "country", "region",
        "min_upload_mbps", "min_download_mbps", "min_tflops",
        "min_vram_gb", "gpu_name", "datacenter")]
    text = render_no_match_diagnostic(f, total_candidates=5, exclusion_log=log)
    # Find the line mentioning max_bid; it must NOT carry a "(safety floor" tag.
    max_bid_line = next(line for line in text.splitlines() if "max_bid" in line.lower())
    assert "safety floor" not in max_bid_line


def test_no_match_does_not_recommend_relaxing_safety_floors():
    f = OfferFilters()
    log = [("max_bid", 0), ("driver_version", 50), ("cuda_max_good", 0),
           ("country", 0), ("region", 0), ("min_upload_mbps", 0),
           ("min_download_mbps", 0), ("min_tflops", 0), ("min_vram_gb", 0),
           ("gpu_name", 0), ("datacenter", 0)]
    text = render_no_match_diagnostic(f, total_candidates=50, exclusion_log=log).lower()
    # The diagnostic must never recommend relaxing driver / CUDA / reliability.
    assert "lower the driver" not in text
    assert "lower reliability" not in text
    assert "lower cuda" not in text
