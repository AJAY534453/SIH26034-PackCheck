"""Vision health and self-test.

Two questions an operator actually needs answered about perception, kept apart:

1. **Is the on-device vision engine working here?** — answered without the network, at any time.
2. **Is the *configured provider* reachable, and what does it read?** — answered only by a real
   call. A health endpoint must not pretend to know: a network probe on every poll would be both
   slow and a lie the moment the provider rate-limits. So `/health/vision` reports what was
   OBSERVED on the last real run (recorded on the inspection), and `POST /vision/test` performs
   the live call and reports its latency and errors verbatim.

Nothing here ever returns a fabricated region, latency or success flag.
"""
from __future__ import annotations

import time

from sqlalchemy.orm import Session

from backend.ai.provider import provider_status
from backend.vision import (
    ENGINE_LABEL,
    availability_reason,
    available,
    analyse_bytes,
    status_text,
)

#: Provider reachability is only known after a real call — never guessed here.
NOT_PROBED = "not probed by this endpoint — call POST /vision/test for a live check"


def health(db: Session | None = None) -> dict:
    """Configuration + the last real vision run, read from the records."""
    status = provider_status()
    payload = {
        "engine": ENGINE_LABEL,
        "on_device": {
            "available": available(),
            "detail": availability_reason(),
        },
        # The always-on engine is what makes vision run at all; the provider is additive.
        "status": "ONLINE" if available() else "OFFLINE",
        "provider": status.provider,
        "model": status.model,
        "configured": status.enabled,
        "reachable": None,
        "reachable_note": NOT_PROBED,
        "last_successful_run": None,
        "last_error": "",
        "latency_ms": None,
    }
    if db is None:
        return payload
    try:
        from backend.models import Inspection

        # The most recent scan that actually recorded a perception status.
        last = (
            db.query(Inspection)
            .filter(Inspection.vision_status != "")
            .order_by(Inspection.id.desc())
            .first()
        )
        if last is not None:
            timings = dict(last.stage_timings or {})
            payload["last_successful_run"] = {
                "inspection_number": last.inspection_number,
                "at": str(last.created_at),
                "vision_status": last.vision_status,
                "vision_status_text": status_text(last.vision_status),
                "engine": last.vision_engine,
                "provider_status": last.provider_status,
                "vision_ms": timings.get("visual_analysis"),
                "provider_ms": timings.get("provider_extraction"),
                "duration_ms": last.duration_ms or 0,
                "regions": "recorded on the inspection",
            }
            payload["latency_ms"] = timings.get("visual_analysis")
            if last.provider_status in ("FAILED", "NO_READING") and last.provider_note:
                payload["last_error"] = last.provider_note
            elif last.vision_status == "FAILED":
                payload["last_error"] = last.vision_note or "On-device vision failed."
    except Exception as exc:  # a health probe must never raise
        payload["last_error"] = f"Could not read the last run from the database: {type(exc).__name__}"
    return payload


def self_test(data: bytes, filename: str = "upload") -> dict:
    """Live check of BOTH perception sources over one uploaded image.

    Runs the on-device engine always, and the provider when one is configured — reporting the
    latency and any error exactly as they occurred.
    """
    provider = provider_status()
    result: dict = {
        "provider": provider.provider,
        "model": provider.model,
        "configured": provider.enabled,
        "provider_reason": provider.reason,
        "on_device": {},
        "provider_call": {},
        "errors": [],
    }

    # ---------- on-device engine ----------
    t0 = time.perf_counter()
    try:
        from backend.ocr import ocr_available, recognize

        from backend.vision.engine import decode_bytes

        image = decode_bytes(data)
        lines = []
        if image is not None and ocr_available():
            # OCR lines are what the engine's text-block analysis consumes; running them here keeps
            # the self-test representative of a real scan rather than a partial picture.
            lines = recognize(image, variant="selftest")
        reading, prep = analyse_bytes(data, lines, filename=filename)
        result["on_device"] = {
            "engine": ENGINE_LABEL,
            "status": "OK" if not reading.error else "FAILED",
            "error": reading.error,
            "image": {"width": prep.width, "height": prep.height},
            "readability": reading.legibility,
            "metrics": {
                "sharpness": prep.sharpness,
                "contrast": prep.contrast,
                "glare": prep.glare,
                "shadow": prep.shadow,
            },
            "ocr_lines_used": len(lines),
            "hero_text": reading.hero_text,
            "hero_bbox": list(reading.hero_bbox) if reading.hero_bbox else None,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "regions": [
                {
                    "kind": region.kind,
                    "label": region.label,
                    "bbox": list(region.bbox) if region.bbox else None,
                    "confidence": region.confidence,
                    "prominence": region.prominence,
                    "contrast": region.contrast,
                    "sharpness": region.sharpness,
                    "text_density": region.text_density,
                    "text": region.text[:200],
                }
                for region in reading.regions
            ],
        }
        if reading.error:
            result["errors"].append(f"on-device: {reading.error}")
    except Exception as exc:
        result["on_device"] = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        result["errors"].append(f"on-device: {type(exc).__name__}: {exc}")

    # ---------- configured provider (only when one is configured) ----------
    if not provider.enabled:
        result["provider_call"] = {
            "attempted": False,
            "status": "NOT_CONFIGURED",
            "latency_ms": None,
            "observations": 0,
            "error": "",
            "detail": provider.reason,
        }
        return result

    from backend.ai.extractor import extract_label_fields

    mime = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
    t1 = time.perf_counter()
    try:
        extraction = extract_label_fields([(data, mime)])
        latency = int((time.perf_counter() - t1) * 1000)
        result["provider_call"] = {
            "attempted": True,
            "status": "USED" if extraction.used else "FAILED",
            "latency_ms": latency,
            "observations": len(extraction.observations),
            "error": extraction.error or "",
            "fields": [
                {"field": o.field, "value": o.value, "confidence": o.confidence, "source_image": o.source_image}
                for o in extraction.observations
            ],
        }
        if extraction.error:
            result["errors"].append(f"provider: {extraction.error}")
        result["reachable"] = bool(extraction.used)
    except Exception as exc:  # pragma: no cover - provider dependent
        result["provider_call"] = {
            "attempted": True,
            "status": "FAILED",
            "latency_ms": int((time.perf_counter() - t1) * 1000),
            "observations": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
        result["reachable"] = False
        result["errors"].append(f"provider: {type(exc).__name__}: {exc}")
    return result
