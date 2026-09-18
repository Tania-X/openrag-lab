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


class ConflictError(DomainError):
    """The request conflicts with the resource's current state.

    Distinct from :class:`AlreadyExistsError` ("it exists") and from
    :class:`InvalidOperationError` ("the request itself is not honoured"): the
    request is fine, but the row is mid-transition (e.g. being indexed), so the
    caller should retry after the transition settles. Mapped to 409.
    """


class InvalidOperationError(DomainError):
    """Raised when a domain operation violates an invariant."""
