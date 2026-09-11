"""Typed identifiers for the identity domain.

Using newtype-style dataclasses helps keep domain methods explicit and
prevents accidentally mixing tenant ids with user ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class TenantId:
    value: str

    @classmethod
    def generate(cls) -> TenantId:
        return cls(str(uuid4()))


@dataclass(frozen=True, slots=True)
class UserId:
    value: str

    @classmethod
    def generate(cls) -> UserId:
        return cls(str(uuid4()))


@dataclass(frozen=True, slots=True)
class RoleId:
    value: str

    @classmethod
    def generate(cls) -> RoleId:
        return cls(str(uuid4()))


@dataclass(frozen=True, slots=True)
class GlobalRoleId:
    value: str

    @classmethod
    def generate(cls) -> GlobalRoleId:
        return cls(str(uuid4()))


@dataclass(frozen=True, slots=True)
class DocumentId:
    value: str

    @classmethod
    def generate(cls) -> DocumentId:
        return cls(str(uuid4()))
