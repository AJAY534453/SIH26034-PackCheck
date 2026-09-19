"""Placement evidence for the Rule 6(1) / Rule 9 declaration-location check.

A photograph can tell us WHICH VIEW of the package a declaration was read from. It cannot, by
itself, tell us which surface is the principal display panel — that depends on how the pack is
presented for sale (and, for a package of 10 cm³ or less, Rule 7(1) permits the principal display
panel to be a card or tape affixed firmly to the package bearing the required information).

So this module collects the raw, auditable facts:

* which image roles were supplied,
* which image each watched declaration was actually read from, and
* therefore whether a declaration was seen on the panel-type view or only elsewhere.

The check in :mod:`backend.rules.engine` decides PASS / UNCERTAIN from those facts and never
claims to have established a violation from a photograph alone.
"""
from __future__ import annotations

# Roles that can plausibly BE the principal display panel of a package sold on a shelf. A side or
# bottom view cannot be assumed to be it, and a close-up is a detail crop rather than a panel.
PANEL_ROLE_HINTS = ("FRONT", "TOP")

ROLE_LABELS = {
    "FRONT": "front view",
    "BACK": "back view",
    "LEFT_SIDE": "left-side view",
    "RIGHT_SIDE": "right-side view",
    "TOP": "top view",
    "BOTTOM": "bottom view",
    "CLOSE_UP": "close-up detail",
    "ADDITIONAL_EVIDENCE": "additional evidence image",
}


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role or "", role or "unlabelled image")


def build_panel_evidence(images, field_rows: dict, vision_facts: dict | None = None) -> dict:
    """Snapshot the image roles and the view each extracted field was read from.

    ``field_rows`` is the pipeline's ``{field_name: ExtractedField}`` map. Only the facts are
    collected here — no judgement: the rule engine owns the interpretation.

    ``vision_facts`` are the on-device vision observations per image (measured prominence, the
    candidate principal display region, readability). They are attached as RAW OBSERVATIONS so a
    reviewer can see what the visual analysis measured; they never move a verdict on their own,
    because a photograph cannot establish which surface is the principal display panel as a
    matter of law.
    """
    views = [
        {
            "image_id": img.id,
            "role": (img.role or ""),
            "quality_status": getattr(img, "quality_status", "") or "",
            "filename": getattr(img, "original_filename", "") or "",
        }
        for img in images
    ]
    role_by_id = {v["image_id"]: v["role"] for v in views}
    fields: dict[str, dict] = {}
    for name, row in (field_rows or {}).items():
        image_id = getattr(row, "source_image_id", None)
        fields[name] = {
            "image_id": image_id,
            "role": role_by_id.get(image_id, "") if image_id else "",
            "state": getattr(row, "state", "") or "",
            "display_value": getattr(row, "display_value", "") or "",
        }
    evidence = {
        "views": views,
        "roles_supplied": [v["role"] for v in views],
        "fields": fields,
    }
    if vision_facts:
        # Keyed by image id; rendered by the UI as "what the vision engine measured here".
        evidence["vision"] = {str(image_id): facts for image_id, facts in vision_facts.items()}
    return evidence
