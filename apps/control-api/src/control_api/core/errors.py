"""Shared pure-Python infrastructure error contracts for Control API modules."""

from __future__ import annotations


class RepositoryUnavailableError(Exception):
    """Raised when an underlying persistence or cache store cannot be reached."""


__all__ = ["RepositoryUnavailableError"]
