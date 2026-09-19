"""Font-size (declaration letter height) measurement — Rule 7 of the LM (PC) Rules, 2011.

Rule 7 prescribes minimum letter/numerals heights **in millimetres**. An image provides pixels, so
a millimetre figure can only be produced when a scale is known. This module is explicit about that
and never invents a scale:

``px_per_mm`` resolution order
    1. an explicit calibration recorded on the inspection (the reviewer measured a known length —
       ``calibration_px_per_mm``), or
    2. trustable DPI metadata carried by the image file itself (JFIF/EXIF/PNG pHYs with a plausible
       value — the 72-dpi placeholder most phone JPEGs report is rejected as untrustworthy).
    With neither, the measurement is reported as UNAVAILABLE and the Rule 7 check returns
    UNCERTAIN with the exact reason, instead of guessing.

``letter height``
    OCR bounding boxes frame the whole text line (ascenders + descenders), not the cap height the
    rule speaks about. The conversion uses a documented cap-height ratio and — deliberately — the
    UPPER end of the plausible band (0.75) so the DETECTED height is over-estimated rather than
    under-estimated. Over-estimating the letter height can only make a FAIL harder to reach, which
    is the correct direction for a legal check.

Every intermediate value (bbox pixels, bbox millimetres, the ratio used) is returned so the
inspector can audit the arithmetic; no figure is presented without its method and calibration.
"""
from __future__ import annotations

import json

from backend.config import settings

# A line bbox spans ascender→descender; the rule's "height of the numeral/letter" is the cap
# height. 0.75 is the upper end of the plausible 0.65–0.80 band, chosen to avoid false shortfalls.
CAP_HEIGHT_RATIO = 0.75
# DPI below this is treated as a file placeholder, not a real capture resolution.
MIN_TRUSTWORTHY_DPI = 100.0

SOURCE_UNAVAILABLE = "UNAVAILABLE"


def _parse_bbox(bbox: str | None) -> tuple[int, int, int, int] | None:
    try:
        cleaned = (bbox or "").strip("()[]").replace(" ", "")
        parts = [int(float(p)) for p in cleaned.split(",") if p != ""]
        if len(parts) == 4 and parts[2] > parts[0] and parts[3] > parts[1]:
            return parts[0], parts[1], parts[2], parts[3]
    except (ValueError, TypeError):
        return None
    return None


def image_px_per_mm(path) -> tuple[float | None, str]:
    """Read a trustworthy px/mm from the file's own metadata, else (None, reason)."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            dpi = im.info.get("dpi")
    except Exception:  # noqa: BLE001 — an unreadable file simply has no calibration
        return None, SOURCE_UNAVAILABLE
    if not dpi:
        return None, SOURCE_UNAVAILABLE
    try:
        value = float(dpi[0]) if isinstance(dpi, (tuple, list)) else float(dpi)
    except (TypeError, ValueError):
        return None, SOURCE_UNAVAILABLE
    if value < MIN_TRUSTWORTHY_DPI:
        # Most camera JPEGs carry a nominal 72 dpi that says nothing about the capture.
        return None, f"image_dpi_untrustworthy({value:g})"
    return value / 25.4, f"image_dpi({value:g})"


def resolve_px_per_mm(inspection, images) -> dict:
    """Scale for this inspection + where it came from."""
    manual = getattr(inspection, "calibration_px_per_mm", None)
    if manual and manual > 0:
        return {
            "px_per_mm": float(manual),
            "source": getattr(inspection, "calibration_source", "") or "manual_calibration",
            "note": getattr(inspection, "calibration_note", "") or "",
        }
    for image in images or []:
        path = settings.STORAGE_DIR / "originals" / (image.stored_filename or "")
        if not (image.stored_filename and path.exists()):
            continue
        px_per_mm, source = image_px_per_mm(path)
        if px_per_mm:
            return {"px_per_mm": px_per_mm, "source": source, "note": f"read from {image.original_filename}"}
    return {"px_per_mm": None, "source": SOURCE_UNAVAILABLE, "note": ""}


def panel_area(inspection, images, px_per_mm: float | None) -> dict:
    """Area of the principal display panel in cm², with its provenance.

    ``measured``      — the reviewer entered the panel's real width and height in mm.
    ``entered``       — a panel area (cm²) was entered directly.
    ``estimated``     — computed from the pixel size of a panel image and the calibration; an
                        ESTIMATE that can straddle a Rule 7 bracket boundary, which is why the
                        rule engine refuses to FAIL on an estimate alone.
    ``unavailable``   — no physical size is known.
    """
    width_mm = getattr(inspection, "panel_width_mm", None)
    height_mm = getattr(inspection, "panel_height_mm", None)
    if width_mm and height_mm and width_mm > 0 and height_mm > 0:
        return {
            "cm2": round((width_mm * height_mm) / 100.0, 2),
            "source": "measured",
            "note": f"panel measured {width_mm:g} mm × {height_mm:g} mm",
        }
    entered = getattr(inspection, "pdp_area_cm2", None)
    if entered and entered > 0:
        return {"cm2": round(float(entered), 2), "source": "entered", "note": "panel area entered by the reviewer"}
    if px_per_mm and images:
        panel = next((i for i in images if (i.role or "").upper() in ("FRONT", "CLOSE_UP")), images[0])
        if panel.width and panel.height:
            w_cm = panel.width / px_per_mm / 10.0
            h_cm = panel.height / px_per_mm / 10.0
            return {
                "cm2": round(w_cm * h_cm, 2),
                "source": "estimated",
                "note": (
                    f"estimated from the {panel.role or 'panel'} image ({panel.width}×{panel.height} px) "
                    "assuming the whole frame is the printed panel — confirm the measured size for a "
                    "final figure"
                ),
            }
    return {"cm2": None, "source": "unavailable", "note": ""}


def measure_field(field_row, px_per_mm: float | None) -> dict | None:
    """Measure one extracted declaration's printed letter height from its evidence bbox."""
    if not px_per_mm:
        return None
    bbox = _parse_bbox(field_row.bbox)
    if not bbox:
        return None
    x1, y1, x2, y2 = bbox
    height_px = y2 - y1
    width_px = x2 - x1
    if height_px <= 0:
        return None
    text = (field_row.source_text or field_row.display_value or "").strip()
    avg_char_px = (width_px / len(text)) if text else 0.0
    height_mm = height_px / px_per_mm
    letter_mm = round(height_mm * CAP_HEIGHT_RATIO, 2)
    char_width_mm = round(avg_char_px / px_per_mm, 3) if avg_char_px else None
    ratio = round(char_width_mm / letter_mm, 3) if (char_width_mm and letter_mm > 0) else None
    return {
        "field_name": field_row.field_name,
        "text": (text or field_row.display_value or "")[:120],
        "state": field_row.state,
        "bbox_px": [x1, y1, x2, y2],
        "line_height_px": height_px,
        "line_height_mm": round(height_mm, 2),
        "letter_height_mm": letter_mm,
        "avg_char_width_mm": char_width_mm,
        "width_ratio": ratio,
        "cap_height_ratio": CAP_HEIGHT_RATIO,
        "image_id": field_row.source_image_id,
    }


# Units whose family can be recovered when the stored declaration carries a unit but no
# quantity_type (older rows). Kept in step with backend/extraction/quantity.py's unit table.
_UNIT_FAMILY = {
    "g": "MASS", "kg": "MASS", "ml": "VOLUME", "l": "VOLUME",
    "cm": "LENGTH", "m": "LENGTH", "mm": "LENGTH", "n": "NUMBER",
}


def _normalized_unit_family(field_row) -> str | None:
    """Unit family of the declared net quantity (MASS/VOLUME/…) — decides Table-I vs Table-II.

    Rule 7(2) selects Table-I when the net quantity is declared by weight or volume and Table-II
    when it is declared by length, area or number, so this value decides which legal table applies.
    """
    if field_row is None:
        return None
    try:
        data = json.loads(field_row.normalized_value or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("quantity_type", "unit_type", "family", "measurement_family"):
        value = data.get(key)
        if value:
            return str(value).upper()
    unit = str(data.get("unit") or "").strip().lower()
    return _UNIT_FAMILY.get(unit)


def build_font_measurement(
    inspection,
    field_rows: dict,
    images,
    *,
    declaration_fields: list[str] | None = None,
    ocr_lines: dict | None = None,
) -> dict:
    """Assemble everything the Rule 7 check needs, with provenance for every number."""
    calibration = resolve_px_per_mm(inspection, images)
    px_per_mm = calibration["px_per_mm"]
    area = panel_area(inspection, images, px_per_mm)

    wanted = declaration_fields or [
        "net_quantity", "mrp", "unit_sale_price", "date_expiry", "date_best_before",
        "consumer_care_phone", "consumer_care_email", "consumer_care_address",
    ]
    measurements: list[dict] = []
    for name in wanted:
        row = field_rows.get(name)
        if row is None:
            continue
        if row.state not in ("DETECTED", "MANUALLY_CORRECTED", "CONFLICTING", "UNCERTAIN"):
            continue
        measured = measure_field(row, px_per_mm)
        if measured:
            measurements.append(measured)

    # Smallest printed text on the pack (informational) — shows the inspector the floor of what
    # was read, without claiming it is a regulated declaration.
    smallest_line = None
    if px_per_mm and ocr_lines:
        for lines in ocr_lines.values():
            for line in lines or []:
                bbox = getattr(line, "bbox", None)
                if not bbox:
                    continue
                h_mm = (bbox[3] - bbox[1]) / px_per_mm
                if smallest_line is None or h_mm < smallest_line["line_height_mm"]:
                    smallest_line = {
                        "text": (getattr(line, "text", "") or "")[:120],
                        "line_height_mm": round(h_mm, 2),
                        "letter_height_mm": round(h_mm * CAP_HEIGHT_RATIO, 2),
                    }

    family = _normalized_unit_family(field_rows.get("net_quantity"))
    return {
        "available": bool(px_per_mm) and bool(area.get("cm2")),
        "px_per_mm": px_per_mm,
        "px_per_mm_source": calibration["source"],
        "px_per_mm_note": calibration["note"],
        "panel_area_cm2": area.get("cm2"),
        "panel_area_source": area.get("source"),
        "panel_area_note": area.get("note"),
        "packaging_form": getattr(inspection, "packaging_form", "") or "normal",
        "quantity_family": family,
        "measurements": measurements,
        "smallest_line": smallest_line,
        "cap_height_ratio": CAP_HEIGHT_RATIO,
        "method": (
            "letter height = OCR bounding-box height × 0.75 (documented cap-height ratio), "
            "converted to millimetres with the calibration in use"
            if px_per_mm
            else "no scale calibration available — millimetre measurement impossible from pixels alone"
        ),
    }
