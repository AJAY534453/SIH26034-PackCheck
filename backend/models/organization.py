"""Organizations — the isolation boundary for role-based data access.

A user belongs to at most one organization. `organization_id` on a record is set SERVER-SIDE from
the authenticated user's own account; a client-supplied id is never trusted.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.enums import OrganizationKind
from backend.models.user import utcnow


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), default=OrganizationKind.BUSINESS.value)
    registration_no: Mapped[str] = mapped_column(String(80), default="")
    contact_email: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
