"""User API schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    role_name: str = "user"
    display_name: str | None = None


class UserResponse(BaseModel):
    id: str
    tenant_id: str
    username: str
    display_name: str | None = None
    status: str
