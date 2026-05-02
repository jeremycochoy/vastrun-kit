"""Loaders for credentials (env / package .env / project .env) and project config (.vastrun.toml)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from . import errors

PACKAGE_ENV = Path(__file__).resolve().parent.parent.parent / ".env"


def _parse_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        v = v.strip().strip('"').strip("'")
        out[k.strip()] = v
    return out


def load_api_key() -> str:
    """Process env > package .env > CWD .env. Raises MissingCredentialError if absent."""
    if v := os.environ.get("VASTAI_API_TOKEN"):
        return v
    for path in (PACKAGE_ENV, Path.cwd() / ".env"):
        if v := _parse_dotenv(path).get("VASTAI_API_TOKEN"):
            return v
    raise errors.MissingCredentialError(
        f"VASTAI_API_TOKEN not found. Set it in process env or write it to {PACKAGE_ENV} "
        "or to a .env in the current directory."
    )


def load_vastrun_toml(path: Path | None = None) -> dict:
    """Read .vastrun.toml from CWD (or `path`). Raises FileNotFoundError naming the file."""
    p = (path or Path.cwd() / ".vastrun.toml").resolve()
    if not p.is_file():
        raise FileNotFoundError(
            f"vastai-kit: .vastrun.toml not found at {p}. "
            "Run `vastrun-init` to scaffold one — see the README for the full schema."
        )
    return tomllib.loads(p.read_text())


def vast_section(toml: dict) -> dict:
    """Return the `[vast]` table from a parsed .vastrun.toml, or an empty dict."""
    return toml.get("vast", {}) or {}
