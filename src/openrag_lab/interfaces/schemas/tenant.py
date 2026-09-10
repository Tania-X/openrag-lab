"""Tenant API schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str | None = None


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    status: str
