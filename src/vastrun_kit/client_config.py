"""Loaders for credentials (env / package .env / project .env) and project config (.vastrun.toml)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from . import errors

PACKAGE_ENV = Path(__file__).resolve().parent.parent.parent / ".env"
REGISTRY_USERNAME = "VASTRUN_REGISTRY_USERNAME"
REGISTRY_TOKEN = "VASTRUN_REGISTRY_TOKEN"


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


def _lookup(name: str) -> str | None:
    """Process env > package .env > CWD .env. None when no source sets `name`."""
    if v := os.environ.get(name):
        return v
    for path in (PACKAGE_ENV, Path.cwd() / ".env"):
        if v := _parse_dotenv(path).get(name):
            return v
    return None


def load_api_key() -> str:
    """Process env > package .env > CWD .env. Raises MissingCredentialError if absent."""
    if v := _lookup("VASTAI_API_TOKEN"):
        return v
    raise errors.MissingCredentialError(
        f"VASTAI_API_TOKEN not found. Set it in process env or write it to {PACKAGE_ENV} "
        "or to a .env in the current directory."
    )


def load_registry_credentials() -> tuple[str, str] | None:
    """(username, token) that pull a private image, from the same sources as the API key.
    None when neither is set; MissingCredentialError when only one is set."""
    username, token = _lookup(REGISTRY_USERNAME), _lookup(REGISTRY_TOKEN)
    if not username and not token:
        return None
    if not (username and token):
        missing = REGISTRY_USERNAME if token else REGISTRY_TOKEN
        raise errors.MissingCredentialError(
            f"{missing} not found. Set both {REGISTRY_USERNAME} and {REGISTRY_TOKEN}, or neither."
        )
    return username, token


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
