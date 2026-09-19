"""Online product listing evidence (e-commerce listings, product pages, listing text).

A listing is a second, independent source of declarations about the same commodity. It is stored
as its own record — with the text it was read from, the extraction result, and the consistency
result against the package — so that:

* a package inspection never has to be re-run to answer "what did the listing say?",
* the comparison is reproducible from stored evidence, and
* an officer can see exactly which page text produced each listing value.

Nothing in this table is a legal finding. The catalog entry that governs how it is used declares
its own legal status (see ``backend/rules/definitions/listings/``) and the application reports a
package/listing difference as a POTENTIAL INCONSISTENCY, never as a confirmed violation.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow

SOURCE_MANUAL_TEXT = "MANUAL_TEXT"
SOURCE_SCREENSHOT = "SCREENSHOT"
SOURCE_LISTING_URL = "LISTING_URL"

SOURCES = (SOURCE_MANUAL_TEXT, SOURCE_SCREENSHOT, SOURCE_LISTING_URL)


class ProductListing(Base):
    __tablename__ = "product_listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(nullable=True, index=True)

    #: The package inspection this listing is being compared against (optional: a listing can be
    #: filed on its own, e.g. a marketplace sweep with no physical pack in hand).
    inspection_id: Mapped[int | None] = mapped_column(
        ForeignKey("inspections.id"), nullable=True, index=True
    )

    source: Mapped[str] = mapped_column(String(24), default=SOURCE_MANUAL_TEXT)
    source_url: Mapped[str] = mapped_column(String(500), default="")
    platform: Mapped[str] = mapped_column(String(80), default="")
    title: Mapped[str] = mapped_column(String(255), default="")

    #: The listing text exactly as supplied/captured — the evidence the extraction ran against.
    raw_text: Mapped[str] = mapped_column(Text, default="")
    #: Extraction result (fields + listing-only concepts, each with its source line).
    extracted_json: Mapped[str] = mapped_column(Text, default="")
    #: Last package-versus-listing comparison result, with the rule reference it was reported against.
    consistency_json: Mapped[str] = mapped_column(Text, default="")
    consistency_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
