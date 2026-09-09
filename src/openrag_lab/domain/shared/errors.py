"""Shared domain errors."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for domain-level errors."""


class NotFoundError(DomainError):
    """Raised when an aggregate or entity does not exist."""


class AlreadyExistsError(DomainError):
    """Raised when creating an entity that already exists."""


class PermissionDeniedError(DomainError):
    """Raised when an actor lacks the required permission."""


class InvalidOperationError(DomainError):
    """Raised when a domain operation violates an invariant."""
