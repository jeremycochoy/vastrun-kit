"""Typed exceptions used across the package. All inherit from RuntimeError."""

from __future__ import annotations


class VastrunError(RuntimeError):
    """Base for all vastrun-kit failures."""


class MissingCredentialError(VastrunError):
    """VASTAI_API_TOKEN absent from process env, package .env, project .env."""


class VastaiCliError(VastrunError):
    """vastai subprocess invocation failed (non-zero, empty stdout, bad JSON, timeout)."""


class OfferUnavailableError(VastrunError):
    """search offers id=<X> returned no rows."""
