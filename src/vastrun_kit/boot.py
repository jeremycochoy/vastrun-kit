"""Boot wait + early-failure detection for newly-created instances.

`wait_for_boot` polls until the instance is running, or fails fast on DOA /
preemption / terminal states / lookup miss / timeout. The CLI rendering layer
turns the BootFailure.reason token into the user-facing message.
"""

from __future__ import annotations

import time

from . import config, errors
from .instances import find_instance

_TERMINAL_STATES = frozenset({"exited", "offline", "destroyed", "expired"})


class BootFailure(errors.VastrunError):
    """wait_for_boot detected DOA, preemption, terminal state, lookup miss, or timeout."""

    def __init__(self, inst_id: int, reason: str) -> None:
        super().__init__(f"boot failed for instance {inst_id}: {reason}")
        self.inst_id = inst_id
        self.reason = reason


def wait_for_boot(inst_id: int) -> dict:
    """Poll find_instance up to BOOT_TIMEOUT_ITERATIONS × BOOT_POLL_SECONDS seconds.

    Returns the instance dict once `actual_status == 'running'`. Raises BootFailure
    with one of: 'doa', 'preempted', 'terminal', 'not_found', 'timeout'.
    """
    for i in range(config.BOOT_TIMEOUT_ITERATIONS):
        inst = find_instance(inst_id)
        if inst is None:
            if i == 0:
                raise BootFailure(inst_id, "not_found")
        else:
            if inst.get("actual_status") == "running":
                return inst
            status_msg = (inst.get("status_msg") or "").lower()
            if "error" in status_msg:
                raise BootFailure(inst_id, "doa")
            if inst.get("intended_status") == "stopped":
                raise BootFailure(inst_id, "preempted")
            if inst.get("actual_status") in _TERMINAL_STATES:
                raise BootFailure(inst_id, "terminal")
        time.sleep(config.BOOT_POLL_SECONDS)
    raise BootFailure(inst_id, "timeout")
