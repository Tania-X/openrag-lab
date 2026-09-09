"""Auth API schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    display_name: str | None = None
    tenant_name: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    tenant_id: str
    username: str


class MeResponse(BaseModel):
    user_id: str
    tenant_id: str
    username: str
    display_name: str | None = None
    permissions: list[str]
