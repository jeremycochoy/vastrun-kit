"""Tests for `vastrun-provision`.

Covers the Provision flow (SPEC §"Provision flow"):
- Happy path → success summary printed.
- Offer gone → exit 1 (OfferUnavailableError, points at vastrun-search).
- Bad image override → exit 1.
- Empty SSH pubkey file → exit 1.
- Balance < $0.10 → exit 1, billing-page hint.
- Empty stdout from create_instance → exit 1, recovery commands.
- Boot timeout / DOA → exit 1 naming inst id + recovery commands.
- SSH-attach exhaustion → exit 1.
- Resolve SSH endpoint None → exit 1, recovery message.
- wait_for_ssh False → exit 1, recovery message.
- Marker write failure → exit 1, recovery command.
- Hardware-summary lookup raises → success still prints (decorative).
- Spot vs on-demand cost line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from vastrun_kit import boot, errors
from vastrun_kit.cli import provision as provision_cli

runner = CliRunner()


# ----- harness ------------------------------------------------------------ #

def _toml(tmp_path: Path, body: str = "[vast]\n") -> Path:
    p = tmp_path / ".vastrun.toml"
    p.write_text(body)
    return p


def _pubkey(tmp_path: Path, content: str = "ssh-ed25519 AAAA host") -> Path:
    p = tmp_path / "id_ed25519.pub"
    p.write_text(content)
    return p


@pytest.fixture
def patch_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Wire the entire happy-path flow with stubs. Tests override individual steps."""
    _toml(tmp_path)
    pub = _pubkey(tmp_path)

    seen: dict[str, Any] = {
        "create_argv": None,
        "boot": [],
        "attach": [],
        "marker_writes": [],
        "ssh_exec": [],
        "label_calls": [],
        "wait_for_ssh": [],
        "find_calls": [],
        "sleep": [],
    }

    offer = {"id": 8765432, "gpu_name": "RTX 4090", "dph_total": 0.50, "min_bid": 0.20}
    inst_running = {
        "id": 555,
        "actual_status": "running",
        "num_gpus": 1,
        "gpu_name": "RTX 4090",
        "gpu_ram": 24576,
        "total_flops": 100.0,
        "geolocation": "United States, US",
    }

    seen["resolve_calls"] = []

    def fake_resolve(oid, *, prosumer=False):
        seen["resolve_calls"].append({"offer_id": oid, "prosumer": prosumer})
        return offer

    monkeypatch.setattr(provision_cli.provision, "resolve_offer", fake_resolve)
    monkeypatch.setattr(provision_cli.provision, "check_balance", lambda: 25.0)

    def fake_create(offer_id, *, image, label, ssh_pubkey, spot_bid):
        seen["create_argv"] = {
            "offer_id": offer_id, "image": image, "label": label,
            "ssh_pubkey": ssh_pubkey, "spot_bid": spot_bid,
        }
        return 555

    monkeypatch.setattr(provision_cli.provision, "create_instance", fake_create)

    def fake_wait_boot(inst_id):
        seen["boot"].append(inst_id)
        return inst_running

    monkeypatch.setattr(provision_cli.boot, "wait_for_boot", fake_wait_boot)

    def fake_attach(inst_id, pub):
        seen["attach"].append((inst_id, pub))

    monkeypatch.setattr(provision_cli.ssh, "attach_ssh_key", fake_attach)
    monkeypatch.setattr(provision_cli.ssh, "resolve_ssh_endpoint",
                        lambda iid: ("ssh5.vast.ai", 22000))

    def fake_wait_ssh(host, port):
        seen["wait_for_ssh"].append((host, port))
        return True

    monkeypatch.setattr(provision_cli.ssh, "wait_for_ssh", fake_wait_ssh)

    def fake_ssh_exec(host, port, cmd, **kw):
        seen["ssh_exec"].append({"host": host, "port": port, "cmd": cmd})
        return 0, "", ""

    monkeypatch.setattr(provision_cli.ssh, "ssh_exec", fake_ssh_exec)

    def fake_write_marker(host, port, mk):
        seen["marker_writes"].append({"host": host, "port": port, "label": mk.label})

    monkeypatch.setattr(provision_cli.marker, "write_marker", fake_write_marker)

    def fake_find_instance(inst_id):
        seen["find_calls"].append(inst_id)
        return inst_running

    monkeypatch.setattr(provision_cli.instances, "find_instance", fake_find_instance)

    def fake_run_vastai(args, **kw):
        seen["label_calls"].append(args)
        return 0, "", ""

    monkeypatch.setattr(provision_cli.vastai_cli, "run_vastai", fake_run_vastai)

    monkeypatch.setattr(provision_cli.time, "sleep", lambda s: seen["sleep"].append(s))

    seen["pub_path"] = str(pub)
    return seen


# ----- happy paths -------------------------------------------------------- #

def test_happy_path_on_demand_prints_summary(patch_flow: dict[str, Any]) -> None:
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "training-v1", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    out = result.stdout
    assert "Instance 555 ready" in out
    assert "SSH: ssh -p 22000 root@ssh5.vast.ai" in out
    # Default is on-demand at dph_total.
    assert "Cost: $0.5" in out
    assert "(on-demand)" in out
    assert "Label: training-v1" in out
    # Hardware line populated.
    assert "Hardware:" in out
    assert "RTX 4090" in out
    # Next-tip block present.
    assert "Next:" in out
    assert "vastrun-exec" in out
    assert "vastrun-destroy" in out
    # Marker was written with the label.
    assert patch_flow["marker_writes"]
    assert patch_flow["marker_writes"][0]["label"] == "training-v1"
    # Boot was waited for.
    assert patch_flow["boot"] == [555]
    # SSH key attached.
    assert patch_flow["attach"]
    # vastai label call issued for the display label.
    label_calls = [a for a in patch_flow["label_calls"] if a[:2] == ["label", "instance"]]
    assert label_calls and "training-v1" in label_calls[0]


def test_default_resolves_offer_without_prosumer(patch_flow: dict[str, Any]) -> None:
    """No --prosumer → resolve_offer called with prosumer=False (datacenter only)."""
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "v1", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 0
    assert patch_flow["resolve_calls"] == [{"offer_id": 8765432, "prosumer": False}]


def test_prosumer_flag_passes_through(patch_flow: dict[str, Any]) -> None:
    """--prosumer is the user's explicit opt-in for non-datacenter offers."""
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "v1", "--ssh-key", patch_flow["pub_path"], "--prosumer"],
    )
    assert result.exit_code == 0
    assert patch_flow["resolve_calls"] == [{"offer_id": 8765432, "prosumer": True}]


def test_happy_path_spot_uses_min_bid_times_multiplier(patch_flow: dict[str, Any]) -> None:
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "spot-run", "--spot", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    # Cost line reports the spot bid (min_bid * BID_MULTIPLIER = 0.20 * 1.3 = 0.26).
    assert "(spot)" in result.stdout
    # The bid was passed through to create_instance.
    assert patch_flow["create_argv"]["spot_bid"] is not None
    assert patch_flow["create_argv"]["spot_bid"] == pytest.approx(0.26)


# ----- step-1: missing toml ----------------------------------------------- #

def test_no_vastrun_toml_exits_1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pub = _pubkey(tmp_path)
    # No .vastrun.toml in CWD; loader must raise.
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", str(pub)],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert ".vastrun.toml" in combined


# ----- step-2: offer gone ------------------------------------------------- #

def test_offer_unavailable_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    def boom(_oid, *, prosumer=False):
        raise errors.OfferUnavailableError("offer 8765432 is gone")

    monkeypatch.setattr(provision_cli.provision, "resolve_offer", boom)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "8765432" in combined
    assert "vastrun-search" in combined


# ----- step-3: empty SSH pubkey file -------------------------------------- #

def test_empty_ssh_pubkey_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    bad = tmp_path / "empty.pub"
    bad.write_text("")
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", str(bad)],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "empty" in combined.lower() or "pubkey" in combined.lower()


def test_missing_ssh_pubkey_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", str(tmp_path / "nonexistent.pub")],
    )
    assert result.exit_code == 1


# ----- step-4: bad image override ----------------------------------------- #

def test_bad_image_override_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--image", "no-colon-image", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "no-colon-image" in combined


# ----- step-5: balance too low -------------------------------------------- #

def test_balance_below_floor_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    monkeypatch.setattr(provision_cli.provision, "check_balance", lambda: 0.05)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "billing" in combined.lower() or "console.vast.ai" in combined


# ----- step-6: create empty stdout --------------------------------------- #

def test_create_instance_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    def boom(*a, **k):
        raise errors.VastaiCliError(
            "create returned empty stdout for offer 8765432 — run vastrun-status / vastrun-destroy <id> --force"
        )

    monkeypatch.setattr(provision_cli.provision, "create_instance", boom)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "8765432" in combined
    assert "vastrun-status" in combined or "vastrun-destroy" in combined


# ----- step-7: boot timeout ---------------------------------------------- #

def test_boot_timeout_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    def boom(inst_id):
        raise boot.BootFailure(inst_id, "timeout")

    monkeypatch.setattr(provision_cli.boot, "wait_for_boot", boom)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    # Names the inst id and recovery commands.
    assert "555" in combined
    assert "vastrun-status" in combined or "vastrun-destroy" in combined


def test_boot_doa_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    def boom(inst_id):
        raise boot.BootFailure(inst_id, "doa")

    monkeypatch.setattr(provision_cli.boot, "wait_for_boot", boom)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "555" in combined
    # The reason ("doa" or similar) should be surfaced too.
    assert "vastrun-destroy" in combined


# ----- step-8: SSH attach exhaustion ------------------------------------- #

def test_ssh_attach_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    def boom(inst_id, pub):
        raise errors.VastaiCliError("attach ssh failed after 3 attempts")

    monkeypatch.setattr(provision_cli.ssh, "attach_ssh_key", boom)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "555" in combined
    assert "vastrun-destroy" in combined


# ----- step-9: SSH endpoint None ---------------------------------------- #

def test_ssh_endpoint_missing_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    monkeypatch.setattr(provision_cli.ssh, "resolve_ssh_endpoint", lambda iid: None)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "555" in combined
    assert "SSH info missing" in combined or "SSH info" in combined
    assert "vastrun-destroy 555 --force" in combined


# ----- step-10: wait_for_ssh False ---------------------------------------- #

def test_wait_for_ssh_false_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    monkeypatch.setattr(provision_cli.ssh, "wait_for_ssh", lambda h, p: False)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "555" in combined
    assert "SSH unreachable" in combined
    assert "ssh5.vast.ai:22000" in combined
    assert "vastrun-destroy 555 --force" in combined


# ----- step-11: marker write failure -------------------------------------- #

def test_marker_write_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    def boom(host, port, mk):
        raise errors.VastaiCliError("ssh write failed")

    monkeypatch.setattr(provision_cli.marker, "write_marker", boom)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "555" in combined
    assert "marker" in combined.lower() or "ownership" in combined.lower()
    assert "vastrun-destroy 555 --force" in combined


# ----- step-12: hardware-summary lookup raises → success still prints ----- #

def test_hardware_summary_failure_does_not_block_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    def boom(inst_id):
        raise RuntimeError("API hiccup")

    monkeypatch.setattr(provision_cli.instances, "find_instance", boom)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    # Already billing — do NOT swallow.
    assert result.exit_code == 0
    out = result.stdout
    assert "Instance 555 ready" in out
    assert "SSH: ssh -p 22000 root@ssh5.vast.ai" in out
    # Hardware line is omitted, but the rest prints.
    assert "Label: x" in out


def test_hardware_summary_vastai_error_does_not_block_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    def boom(inst_id):
        raise errors.VastaiCliError("show instances broke")

    monkeypatch.setattr(provision_cli.instances, "find_instance", boom)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 0
    assert "Instance 555 ready" in result.stdout


# ----- vastai-label best-effort ------------------------------------------ #

def test_vastai_label_failure_does_not_exit_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    def fake_run_vastai(args, **kw):
        return 1, "", "label boom"

    monkeypatch.setattr(provision_cli.vastai_cli, "run_vastai", fake_run_vastai)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 0
    assert "Instance 555 ready" in result.stdout


# ----- hello-world probe is decorative ----------------------------------- #

def test_hello_world_failure_does_not_exit_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, patch_flow: dict[str, Any]
) -> None:
    def fake_ssh_exec(host, port, cmd, **kw):
        # Marker write goes through marker.write_marker (already stubbed).
        # Hello-world echo "ok" returns nonzero; should not block the summary.
        return 1, "", "ssh fizzled"

    monkeypatch.setattr(provision_cli.ssh, "ssh_exec", fake_ssh_exec)
    result = runner.invoke(
        provision_cli.app,
        ["8765432", "--label", "x", "--ssh-key", patch_flow["pub_path"]],
    )
    assert result.exit_code == 0
    assert "Instance 555 ready" in result.stdout
