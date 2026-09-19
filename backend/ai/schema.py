"""Structured schema for vision-model extraction.

The model is a PERCEPTION component only. It returns observations; it never returns a
compliance verdict. Everything it reports is re-validated by the deterministic pipeline
(normalization + field validators + rule engine) before it can influence a decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

# Tracked declaration fields the vision model is allowed to report. Must stay a subset of the
# deterministic pipeline's TRACKED_DECLARATION_FIELDS so an observation always lands in a field
# the inspector can review and the rule engine knows about.
AI_FIELD_NAMES: tuple[str, ...] = (
    "product_name",
    "brand",
    "common_name",
    "manufacturer",
    "manufacturer_address",
    "packer",
    "packer_address",
    "importer",
    "importer_address",
    # Pipeline field name for a "Marketed by" declaration. "marketed_by" is accepted as an
    # alias from the model and mapped here.
    "marketer",
    "country_of_origin",
    "net_quantity",
    "mrp",
    "unit_sale_price",
    "date_manufacturing",
    "date_packing",
    "date_import",
    "date_expiry",
    "date_best_before",
    "batch_lot",
    "consumer_care_phone",
    "consumer_care_email",
    "website",
    "fssai_license",
    "ingredients",
    "nutrition_information",
)

# Fields the vision model may report that the deterministic OCR pipeline does not track.
# They are recorded as observations in the field row set only when a value is actually read.
AI_EXTRA_FIELDS: tuple[str, ...] = ("ingredients", "nutrition_information")

# Fields whose value is a free-text/visual judgement rather than a parseable declaration: a
# corroborating OCR reading is meaningful evidence, but an AI-only reading is an inference.
VISUAL_JUDGEMENT_FIELDS: tuple[str, ...] = ("brand", "product_name", "common_name")


@dataclass
class AIFieldObservation:
    """One field as reported by the vision model, before any validation."""

    field: str
    value: str
    confidence: float
    source_image: str = ""
    bbox: list[float] | None = None  # normalised [x, y, w, h] in 0..1 of the source image
    evidence_text: str = ""
    notes: str = ""


@dataclass
class AIExtraction:
    """Everything one vision call reported, plus honest provenance for the call itself."""

    observations: list[AIFieldObservation] = dc_field(default_factory=list)
    provider: str = ""
    model: str = ""
    used: bool = False
    error: str = ""
    # Raw model text (kept for audit/debugging; never surfaced as a declaration).
    raw: str = ""


@dataclass
class AIBillExtraction:
    """Bill/perchase-slip readings. Same rules: observations only, no legal verdict."""

    observations: list[AIFieldObservation] = dc_field(default_factory=list)
    provider: str = ""
    model: str = ""
    used: bool = False
    error: str = ""
    raw: str = ""


BILL_FIELD_NAMES: tuple[str, ...] = (
    "store_name",
    "bill_number",
    "bill_date",
    "product_name",
    "brand",
    "billed_price",
    "quantity",
    "mrp_on_bill",
    "line_items",
)
