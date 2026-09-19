"""Enumerations used across POCKET models, schemas and pipeline stages."""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """String enum that serializes cleanly to JSON."""


class InspectionStatus(StrEnum):
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FinalDecision(StrEnum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


class FieldState(StrEnum):
    DETECTED = "DETECTED"
    MISSING = "MISSING"  # not found in the SUPPLIED images — never implies absence from the package
    CONFLICTING = "CONFLICTING"
    UNCERTAIN = "UNCERTAIN"
    MANUALLY_CORRECTED = "MANUALLY_CORRECTED"
    HUMAN_CONFIRMED_ABSENT = "HUMAN_CONFIRMED_ABSENT"  # inspector has confirmed the declaration is absent


class QualityStatus(StrEnum):
    GOOD = "GOOD"
    ACCEPTABLE = "ACCEPTABLE"
    POOR = "POOR"
    UNUSABLE = "UNUSABLE"


class ImageRole(StrEnum):
    FRONT = "FRONT"
    BACK = "BACK"
    LEFT_SIDE = "LEFT_SIDE"
    RIGHT_SIDE = "RIGHT_SIDE"
    TOP = "TOP"
    BOTTOM = "BOTTOM"
    CLOSE_UP = "CLOSE_UP"
    ADDITIONAL_EVIDENCE = "ADDITIONAL_EVIDENCE"


class RuleStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RuleApplicability(StrEnum):
    MANDATORY = "MANDATORY"
    CONDITIONAL = "CONDITIONAL"
    MANUAL_ONLY = "MANUAL_ONLY"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ViolationStatus(StrEnum):
    OPEN = "OPEN"  # evidence-backed potential violation awaiting human confirmation
    NEEDS_REVIEW = "NEEDS_REVIEW"  # created from missing/uncertain evidence — NOT a confirmed violation
    DISMISSED = "DISMISSED"
    CONFIRMED = "CONFIRMED"  # confirmed by human review
    RESOLVED = "RESOLVED"


class ClassificationState(StrEnum):
    DETECTED = "DETECTED"
    CONFLICTING = "CONFLICTING"
    UNCERTAIN = "UNCERTAIN"


class UnitType(StrEnum):
    MASS = "MASS"
    VOLUME = "VOLUME"
    LENGTH = "LENGTH"
    AREA = "AREA"
    NUMBER = "NUMBER"


class UserRole(StrEnum):
    # Internal / regulator-side roles (existing)
    ADMIN = "ADMIN"
    INSPECTOR = "INSPECTOR"
    VIEWER = "VIEWER"
    # ---- the three major user categories ----
    # ROLE 1 — regulatory enforcement personnel (regulator side)
    ENFORCEMENT_OFFICER = "ENFORCEMENT_OFFICER"
    # ROLE 2 — a business/entity subject to regulation (commercial side)
    REGULATED_ENTITY = "REGULATED_ENTITY"
    # ROLE 3 — internal corporate compliance personnel (internal side)
    INTERNAL_COMPLIANCE = "INTERNAL_COMPLIANCE"


class UserStatus(StrEnum):
    """Account lifecycle state. Only ACTIVE accounts may authenticate."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"          # administratively disabled — cannot log in
    LOCKED = "LOCKED"              # locked (e.g. after repeated failed logins)
    PENDING_VERIFICATION = "PENDING_VERIFICATION"  # created but not yet verified


class OrganizationKind(StrEnum):
    REGULATOR = "REGULATOR"
    BUSINESS = "BUSINESS"
    CORPORATE = "CORPORATE"
