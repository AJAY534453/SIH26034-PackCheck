"""Inspection API schemas."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class ImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    original_filename: str
    stored_filename: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    quality_score: float
    quality_status: str
    quality_metrics: dict
    ocr_line_count: int


class FieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    field_name: str
    state: str
    raw_value: str
    normalized_value: str
    display_value: str
    confidence: float
    source: str
    source_engine: str
    preprocessing_variant: str
    bbox: str
    source_text: str
    extraction_reason: str
    uncertainty_reason: str
    conflict_status: str
    manually_corrected: bool


class RuleEvalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rule_number: str
    title: str
    status: str
    reason: str
    observed: str
    expected: str
    confidence: float
    critical: bool
    # Which check produced the outcome, and its structured traceability (e.g. the Rule 7
    # required/detected letter heights). Exposed so the UI never has to infer it.
    check_type: str = ""
    detail: dict = {}

    @field_validator("detail", mode="before")
    @classmethod
    def _parse_detail(cls, value: Any) -> dict:
        """The column stores JSON text; the API contract is a real object."""
        if isinstance(value, str):
            try:
                parsed = json.loads(value or "{}")
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return value or {}


class ViolationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rule_number: str
    title: str
    description: str
    observed: str
    expected: str
    severity: str
    status: str
    confidence: float


class InspectionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    inspection_number: str
    product_id: int | None
    status: str
    # ``final_decision`` is the automated (AI/system) preliminary verdict.
    # ``official_decision`` is written only by an authorized human finalization — the two are
    # distinct on the wire exactly as they are in the database.
    final_decision: str | None
    official_decision: str | None = None
    finalized_by: str = ""
    finalized_at: datetime | None = None
    finalization_remarks: str = ""
    inspector: str
    category: str
    category_state: str
    overall_quality: str
    created_at: datetime
    processed_at: datetime | None
    completed_at: datetime | None
    # Which perception sources actually produced this result (written from what ran, never from
    # what was configured). See backend.vision for the status vocabulary.
    vision_status: str = ""
    vision_engine: str = ""
    provider_status: str = ""
    duration_ms: int = 0


class InspectionDetail(InspectionSummary):
    stage_status: dict
    summary: str
    notes: str
    category_confidence: float
    overall_quality_score: float
    images: list[ImageOut]
    fields: list[FieldOut]
    rules: list[RuleEvalOut]
    violations: list[ViolationOut]
    review_actions: list[dict]
    reports: list[dict]
    evidence: list[dict] = []
    # Rule evaluation id (as a string key) → the evidence rows that support that conclusion.
    rule_evidence: dict = {}
    # Which engine/rule set produced the ACTIVE result, and whether it is still current.
    provenance: dict = {}
    classification_signals: str = ""
    compliance_review: dict = {}
    font_size: dict = {}
    calibration: dict = {}
    # Per-stage wall-clock milliseconds + the vision observations that were retained.
    stage_timings: dict = {}
    vision: dict = {}
