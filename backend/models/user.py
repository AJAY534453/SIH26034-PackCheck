"""User accounts with role-based access and account-security state.

Role vocabulary (see `backend.models.enums.UserRole`):
  ADMIN, INSPECTOR, VIEWER      — the original internal roles (kept for back-compat)
  ENFORCEMENT_OFFICER           — ROLE 1: regulatory enforcement personnel
  REGULATED_ENTITY              — ROLE 2: a business/entity subject to regulation
  INTERNAL_COMPLIANCE           — ROLE 3: internal corporate compliance personnel

The security-relevant columns below are additive: an existing database row simply gets the
defaults (ACTIVE, MFA off, no lockout). Secrets are never returned by any API.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.enums import UserRole, UserStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(32), default=UserRole.VIEWER.value)
    password_hash: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # ---------- identity / account ----------
    email: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default=UserStatus.ACTIVE.value)
    email_verified: Mapped[bool] = mapped_column(default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    must_change_password: Mapped[bool] = mapped_column(default=False)

    # ---------- MFA (TOTP) ----------
    mfa_enabled: Mapped[bool] = mapped_column(default=False)
    # Base32 TOTP shared secret. Server-side only — never serialized to a client.
    mfa_secret: Mapped[str] = mapped_column(String(64), default="")
    # JSON list of scrypt hashes of one-time recovery codes (plaintext never stored).
    mfa_recovery_codes: Mapped[str] = mapped_column(Text, default="[]")

    # ---------- brute-force protection ----------
    failed_login_count: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_ip: Mapped[str] = mapped_column(String(64), default="")
