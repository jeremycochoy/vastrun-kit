"""Tests for `vastrun_kit.provision` orchestration helpers.

Covered:
- `resolve_offer`: happy path returns the single dict; empty raises OfferUnavailableError.
- `resolve_image`: explicit override (valid + invalid), Blackwell auto-promote, default.
- `check_balance`: happy path; VastaiCliError propagates.
- `create_instance`: argv shape, on-demand vs spot, empty stdout raises naming the offer id.
- `compute_spot_bid`: cap binding vs not.
"""

from __future__ import annotations

import pytest

from vastrun_kit import config, errors, provision


# ---------- resolve_offer ----------

def test_resolve_offer_finds_match_in_broad_search(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    payload = [[
        {"id": 1, "gpu_name": "RTX 3090"},
        {"id": 8765432, "gpu_name": "RTX 4090"},
        {"id": 2, "gpu_name": "RTX 4080"},
    ]]

    def fake(args, **kw):  # noqa: ANN001
        seen.append(args)
        return payload

    monkeypatch.setattr(provision.vastai_cli, "run_vastai_raw", fake)
    out = provision.resolve_offer(8765432)
    assert out == {"id": 8765432, "gpu_name": "RTX 4090"}
    # Issued one broad search-offers call (no `id=X` filter — that's a no-op in Vast.ai).
    assert len(seen) == 1
    assert seen[0][:2] == ["search", "offers"]
    assert not any("id=" in a for a in seen[0])


def test_resolve_offer_flat_list_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """The vastai response may be a flat list rather than nested."""
    payload = [{"id": 8765432, "gpu_name": "RTX 4090"}]
    monkeypatch.setattr(provision.vastai_cli, "run_vastai_raw", lambda args, **kw: payload)
    assert provision.resolve_offer(8765432) == {"id": 8765432, "gpu_name": "RTX 4090"}


def test_resolve_offer_no_match_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"id": 1}, {"id": 2}]
    monkeypatch.setattr(provision.vastai_cli, "run_vastai_raw", lambda args, **kw: payload)
    with pytest.raises(errors.OfferUnavailableError) as exc:
        provision.resolve_offer(99999999)
    assert "99999999" in str(exc.value)


def test_resolve_offer_empty_payload_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provision.vastai_cli, "run_vastai_raw", lambda args, **kw: [])
    with pytest.raises(errors.OfferUnavailableError):
        provision.resolve_offer(123)


def test_resolve_offer_default_query_is_datacenter(monkeypatch: pytest.MonkeyPatch) -> None:
    """No silent prosumer fallback: the default query carries datacenter=True."""
    seen: list[list[str]] = []

    def fake(args, **kw):  # noqa: ANN001
        seen.append(args)
        return []

    monkeypatch.setattr(provision.vastai_cli, "run_vastai_raw", fake)
    with pytest.raises(errors.OfferUnavailableError):
        provision.resolve_offer(123)
    # Exactly one call (no fallback to prosumer); query contains datacenter=True.
    assert len(seen) == 1
    assert any("datacenter=True" in a for a in seen[0])


def test_resolve_offer_prosumer_drops_datacenter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit `prosumer=True` queries non-datacenter offers (the user opted in)."""
    seen: list[list[str]] = []

    def fake(args, **kw):  # noqa: ANN001
        seen.append(args)
        return [{"id": 555, "gpu_name": "RTX 4090"}]

    monkeypatch.setattr(provision.vastai_cli, "run_vastai_raw", fake)
    out = provision.resolve_offer(555, prosumer=True)
    assert out == {"id": 555, "gpu_name": "RTX 4090"}
    # No datacenter clause in the query when prosumer is on.
    assert not any("datacenter=True" in a for a in seen[0])


# ---------- resolve_image ----------

def test_resolve_image_override_returned() -> None:
    out = provision.resolve_image({"gpu_name": "RTX 4090"}, override="my/image:tag")
    assert out == "my/image:tag"


def test_resolve_image_override_without_colon_raises() -> None:
    with pytest.raises(ValueError, match="bogus-image"):
        provision.resolve_image({"gpu_name": "RTX 4090"}, override="bogus-image")


def test_resolve_image_blackwell_promotes_when_no_override() -> None:
    out = provision.resolve_image({"gpu_name": "RTX 5090"}, override=None)
    assert out == config.BLACKWELL_IMAGE


def test_resolve_image_blackwell_b200() -> None:
    out = provision.resolve_image({"gpu_name": "B200 SXM"}, override=None)
    assert out == config.BLACKWELL_IMAGE


def test_resolve_image_default_when_not_blackwell() -> None:
    out = provision.resolve_image({"gpu_name": "RTX 4090"}, override=None)
    assert out == config.IMAGE


def test_resolve_image_override_wins_even_for_blackwell() -> None:
    out = provision.resolve_image({"gpu_name": "RTX 5090"}, override="custom/img:v1")
    assert out == "custom/img:v1"


# ---------- check_balance ----------

def test_check_balance_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake(args, **kw):  # noqa: ANN001
        seen.append(args)
        return {"credit": 12.34, "balance": 0}

    monkeypatch.setattr(provision.vastai_cli, "run_vastai_raw", fake)
    assert provision.check_balance() == pytest.approx(12.34)
    assert seen == [["show", "user"]]


def test_check_balance_missing_credit_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provision.vastai_cli, "run_vastai_raw", lambda a, **kw: {})
    assert provision.check_balance() == 0.0


def test_check_balance_propagates_vastai_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(args, **kw):
        raise errors.VastaiCliError("boom")
    monkeypatch.setattr(provision.vastai_cli, "run_vastai_raw", boom)
    with pytest.raises(errors.VastaiCliError, match="boom"):
        provision.check_balance()


# ---------- create_instance ----------

def _capture_create(monkeypatch: pytest.MonkeyPatch, stdout: str = "Started. New instance id is 555.\n", rc: int = 0) -> dict:
    """Stub run_vastai capturing argv and returning a fixed (rc, out, err)."""
    captured: dict[str, list[str]] = {"argv": []}

    def fake(args, **kw):  # noqa: ANN001
        captured["argv"] = args
        return rc, stdout, ""

    monkeypatch.setattr(provision.vastai_cli, "run_vastai", fake)
    return captured


def test_create_instance_argv_shape_on_demand(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_create(monkeypatch, stdout="Started. New instance id is 555.\n")
    inst_id = provision.create_instance(
        8765432, image="my/image:tag", label="training-v1", ssh_pubkey="ssh-ed25519 AAA",
        spot_bid=None,
    )
    argv = captured["argv"]
    assert argv[:3] == ["create", "instance", "8765432"]
    assert "--ssh" in argv
    assert "--direct" in argv
    assert "--disk" in argv and str(config.DISK_GB) in argv
    assert "--label" in argv and "training-v1" in argv
    assert "--image" in argv and "my/image:tag" in argv
    assert "--bid_price" not in argv
    # Returns an instance id (some integer, parsed from stdout or via a follow-up).
    assert isinstance(inst_id, int)
    assert inst_id == 555


def test_create_instance_argv_includes_bid_price_when_spot(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_create(monkeypatch, stdout="Started. New instance id is 1234.\n")
    inst_id = provision.create_instance(
        100, image="img:tag", label="spot-run", ssh_pubkey="key",
        spot_bid=0.42,
    )
    argv = captured["argv"]
    assert "--bid_price" in argv
    bid_pos = argv.index("--bid_price")
    assert argv[bid_pos + 1] == "0.42"
    assert inst_id == 1234


def test_create_instance_empty_stdout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_create(monkeypatch, stdout="")
    with pytest.raises(errors.VastaiCliError) as exc:
        provision.create_instance(
            555555, image="i:t", label="x", ssh_pubkey="k", spot_bid=None,
        )
    msg = str(exc.value)
    # The message names the offer id and points at the recovery commands.
    assert "555555" in msg
    assert "vastrun-status" in msg
    assert "vastrun-destroy" in msg


def test_create_instance_failure_with_stderr_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(args, **kw):  # noqa: ANN001
        return 1, "starting…\n", "GPU unavailable"
    monkeypatch.setattr(provision.vastai_cli, "run_vastai", fake)
    with pytest.raises(errors.VastaiCliError) as exc:
        provision.create_instance(
            321, image="i:t", label="x", ssh_pubkey="k", spot_bid=None,
        )
    assert "GPU unavailable" in str(exc.value) or "321" in str(exc.value)


def test_create_instance_parses_new_contract_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """The vastai CLI prints `{'success': True, 'new_contract': <id>}`."""
    _capture_create(monkeypatch, stdout='{"success": true, "new_contract": 7835610}\n')
    inst_id = provision.create_instance(
        100, image="i:t", label="x", ssh_pubkey="k", spot_bid=None,
    )
    assert inst_id == 7835610


# ---------- compute_spot_bid ----------

def test_compute_spot_bid_uses_multiplier_when_under_cap() -> None:
    bid = provision.compute_spot_bid({"min_bid": 0.20})
    # 0.20 * 1.3 = 0.26, under MAX_BID
    assert bid == pytest.approx(0.20 * config.BID_MULTIPLIER)


def test_compute_spot_bid_caps_at_max_bid() -> None:
    bid = provision.compute_spot_bid({"min_bid": 1.00}, max_bid_cap=0.40)
    assert bid == pytest.approx(0.40)


def test_compute_spot_bid_caps_at_default_max_bid() -> None:
    # min_bid * BID_MULTIPLIER would be 1.30; cap is MAX_BID = 0.80
    bid = provision.compute_spot_bid({"min_bid": 1.00})
    assert bid == pytest.approx(config.MAX_BID)


def test_compute_spot_bid_zero_min_bid_returns_zero() -> None:
    assert provision.compute_spot_bid({"min_bid": 0.0}) == 0.0
