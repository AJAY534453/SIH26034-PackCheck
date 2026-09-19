"""Technical validation — structural checks only, fully separate from legal compliance.

Answers: 'Is the extracted data structurally sound?' NOT 'Is the package legally compliant?'

The detailed per-field validators now live in `backend.normalization.validators`; this module
keeps the stable `validate_field` entry point used by the pipeline.
"""
from __future__ import annotations

from backend.normalization.validators import validate_field as _validate_field  # noqa: F401


def validate_field(field_name: str, normalized: dict) -> dict:
    """Return {valid: bool, issues: [str]} — structural validity of one field."""
    return _validate_field(field_name, normalized)


def validate_fields(fields: dict[str, dict]) -> dict[str, dict]:
    """Validate a map of field_name -> normalized dict."""
    return {name: validate_field(name, norm) for name, norm in fields.items()}
