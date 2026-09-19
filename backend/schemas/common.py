"""Common API schemas.

The Token / UserOut shapes are the ONLY user-facing auth payloads. Neither ever carries a
password hash, MFA secret, recovery code or refresh token.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Token(BaseModel):
    # Empty unless the client opted in with include_token=true — the browser app authenticates
    # with the HttpOnly session cookie and never receives a usable credential in the body.
    access_token: str = ""
    token_type: str = "bearer"
    username: str = ""
    role: str = ""
    full_name: str = ""
    # Where the authenticated user should land (role-aware routing).
    home: str = "/dashboard"
    permissions: list[str] = Field(default_factory=list)
    # Set when the password was accepted but a second factor is still required.
    mfa_required: bool = False
    mfa_token: str | None = None
    message: str = ""


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str
    email: str = ""
    role: str
    status: str = "ACTIVE"
    is_active: bool
    organization_id: int | None = None
    organization: dict | None = None
    mfa_enabled: bool = False
    must_change_password: bool = False
    permissions: list[str] = Field(default_factory=list)
    home: str = "/dashboard"


class Page(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


class Message(BaseModel):
    message: str


class ErrorOut(BaseModel):
    detail: str
