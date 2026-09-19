"""PACKCHECK AI vision layer — perception only, never a legal decision.

Public surface:
- `ai_status()`            honest provider status for /status and the UI badge
- `AI_FIELD_NAMES`         the fields a vision model may report
- `ai_field_candidates()`  pipeline hook: vision readings → ranked candidates
- `extract_bill_fields()`  bill/permit-slip reading for the Bill Scanner
"""
from backend.ai.extractor import ai_field_candidates, extract_bill_fields, extract_label_fields, to_candidates
from backend.ai.provider import provider_status, vision_available
from backend.ai.schema import AI_FIELD_NAMES, AIBillExtraction, AIExtraction, AIFieldObservation


def ai_status() -> dict:
    status = provider_status()
    payload = status.as_dict()
    payload["mode"] = "vision" if status.enabled else "ocr_only"
    payload["fields"] = list(AI_FIELD_NAMES)
    return payload


__all__ = [
    "AI_FIELD_NAMES",
    "AIBillExtraction",
    "AIExtraction",
    "AIFieldObservation",
    "ai_field_candidates",
    "ai_status",
    "extract_bill_fields",
    "extract_label_fields",
    "provider_status",
    "to_candidates",
    "vision_available",
]
