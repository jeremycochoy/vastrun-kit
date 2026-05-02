from __future__ import annotations

import pytest

from vastrun_kit import boot, config


def _patch_polls(monkeypatch: pytest.MonkeyPatch, results: list[dict | None]) -> list[int]:
    """Stub `boot.find_instance` to walk a fixed list of poll responses."""
    seen: list[int] = []
    it = iter(results)

    def fake(inst_id: int):
        seen.append(inst_id)
        try:
            return next(it)
        except StopIteration:
            return results[-1]

    monkeypatch.setattr(boot, "find_instance", fake)
    monkeypatch.setattr(boot.time, "sleep", lambda _s: None)
    return seen


def test_wait_for_boot_doa_status_msg(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_polls(
        monkeypatch,
        [{"status_msg": "docker_build error: bad image", "actual_status": "loading"}],
    )
    with pytest.raises(boot.BootFailure) as exc:
        boot.wait_for_boot(42)
    assert exc.value.reason == "doa"
    assert exc.value.inst_id == 42


def test_wait_for_boot_preempted_intended_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_polls(
        monkeypatch,
        [{"intended_status": "stopped", "actual_status": "loading"}],
    )
    with pytest.raises(boot.BootFailure) as exc:
        boot.wait_for_boot(42)
    assert exc.value.reason == "preempted"


def test_wait_for_boot_terminal_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_polls(monkeypatch, [{"actual_status": "exited"}])
    with pytest.raises(boot.BootFailure) as exc:
        boot.wait_for_boot(42)
    assert exc.value.reason == "terminal"


def test_wait_for_boot_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    final = {"actual_status": "running", "id": 42}
    seen = _patch_polls(
        monkeypatch,
        [{"actual_status": "loading"}, final],
    )
    assert boot.wait_for_boot(42) is final
    assert seen == [42, 42]


def test_wait_for_boot_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "BOOT_TIMEOUT_ITERATIONS", 3)
    monkeypatch.setattr(boot, "find_instance", lambda inst_id: {"actual_status": "loading"})
    monkeypatch.setattr(boot.time, "sleep", lambda _s: None)
    with pytest.raises(boot.BootFailure) as exc:
        boot.wait_for_boot(42)
    assert exc.value.reason == "timeout"


def test_wait_for_boot_not_found_first_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boot, "find_instance", lambda inst_id: None)
    monkeypatch.setattr(boot.time, "sleep", lambda _s: None)
    with pytest.raises(boot.BootFailure) as exc:
        boot.wait_for_boot(42)
    assert exc.value.reason == "not_found"


def test_boot_failure_subclasses_vastrun_error() -> None:
    from vastrun_kit import errors

    assert issubclass(boot.BootFailure, errors.VastrunError)
