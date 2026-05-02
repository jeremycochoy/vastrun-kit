"""Single chokepoint for `vastai` subprocess calls. Every call here has a hard timeout
and the API key injected via --api-key (so we never depend on ~/.vast_api_key).

Public surface:
    run_vastai(args)        -> (returncode, stdout, stderr)
    run_vastai_raw(args)    -> parsed JSON from `vastai ... --raw`
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from . import client_config, config, errors


def run_vastai(
    args: list[str],
    *,
    timeout: int = config.VASTAI_API_TIMEOUT_SECONDS,
) -> tuple[int, str, str]:
    cmd = ["vastai", "--api-key", client_config.load_api_key(), *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise errors.VastaiCliError(
            f"`vastai {' '.join(args)}` timed out after {timeout}s"
        ) from e
    return p.returncode, p.stdout, p.stderr


def run_vastai_raw(
    args: list[str],
    *,
    timeout: int = config.VASTAI_API_TIMEOUT_SECONDS,
) -> Any:
    """Run with --raw appended; require non-empty stdout and valid JSON.

    Empty stdout from --raw is a known Vast.ai CLI bug — we surface it as an error
    rather than silently returning None / re-querying.
    """
    rc, out, err = run_vastai([*args, "--raw"], timeout=timeout)
    pretty = " ".join(args)
    if rc != 0:
        msg = (err.strip() or out.strip() or "non-zero exit").splitlines()[0]
        raise errors.VastaiCliError(f"`vastai {pretty}` failed: {msg}")
    if not out.strip():
        raise errors.VastaiCliError(
            f"`vastai {pretty}` returned empty stdout (CLI bug — do not retry blindly)"
        )
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise errors.VastaiCliError(
            f"`vastai {pretty}` returned non-JSON: {out[:200]!r}"
        ) from e
