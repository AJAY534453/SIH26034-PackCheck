"""Deterministic rule engine — data-driven evaluation. No LLM decides legal outcomes here.

Each evaluation returns PASS / FAIL / UNCERTAIN / NOT_APPLICABLE with a human-readable reason
citing observed evidence.

Core safety policy (non-negotiable):
- NOT FOUND BY OCR ≠ NOT PRESENT ON PACKAGE. Missing evidence yields UNCERTAIN, never FAIL.
- A FAIL is produced only when positive, sufficient evidence establishes a violation — never
  from absence alone.
- A PASS requires substantive evidence: a detected value that is more than a bare anchor label.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from backend.models import RuleVersion
from backend.rules.font_size import all_tables, requirement_for, requirement_table
from backend.rules.placement import PANEL_ROLE_HINTS, role_label

#: A value that is nothing but role wording ("Manufactured by", "Packed & Marketed by",
#: "Name & Address:") is an ANCHOR, not a declaration. The separators between the wording are part of
#: the pattern: without them "Manufactured by" was read as substantive content and could earn a PASS
#: with no entity behind it.
ROLE_LABEL_ONLY_RE = re.compile(
    r"^(?:(?:manufactured?|mfd|mfg|packed?|pkd|imported?|marketed?|by|at|for|and|&|address|"
    r"name|details|only)\b[\s:.,;\-]*)+$",
    re.IGNORECASE,
)


@dataclass
class RuleOutcome:
    rule_id: str
    rule_version_id: int
    rule_number: str
    title: str
    requirement: str
    status: str  # PASS | FAIL | UNCERTAIN | NOT_APPLICABLE
    reason: str
    observed: str
    expected: str
    confidence: float
    critical: bool
    applicability_reason: str = ""
    # Weight this requirement carries in the score (declared in the rule definition; the
    # criticality default is only a fallback). Pinned onto the evaluation row at write time.
    weight: float = 1.0
    # APPLIES | INAPPLICABLE | EVIDENCE_MISSING — see backend.rules.applicability.
    applicability_kind: str = ""
    check_type: str = ""
    human_confirmed: bool = False  # FAIL basis was an inspector's confirmed absence
    # Structured decision traceability (required/detected/reference for measurable checks such as
    # Rule 7 font size). Persisted verbatim so a report can show WHY the status was reached.
    detail: dict = field(default_factory=dict)


def _field_state(fields: dict, name: str):
    f = fields.get(name)
    if not f:
        return None, None
    return f, (f.get("confidence") or 0.0)


def _human_confirmed_absent(fields: dict, names: list[str]) -> bool:
    """True if the inspector has explicitly confirmed absence for any of the tracked fields.

    This is the ONLY path (besides positive contradictory evidence) by which a declaration
    check may FAIL: a human confirmed the declaration is genuinely absent from the package.
    """
    for n in names:
        f = fields.get(n)
        if f and f.get("state") == "HUMAN_CONFIRMED_ABSENT":
            return True
    return False


def _substantive(display_value: str | None) -> bool:
    """A value is substantive evidence only if it carries real content — a bare anchor label
    such as 'Manufactured by' (or an anchor followed by punctuation alone) is NOT sufficient to
    justify a PASS; the declaration needs the entity/number behind the anchor."""
    if not display_value or not display_value.strip():
        return False
    if ROLE_LABEL_ONLY_RE.match(display_value.strip()):
        return False
    # Numeric content (prices, quantities, dates) is substantive evidence on its own —
    # a value like "₹ 200.00" or "500 g" has no 2+-letter token but is real evidence.
    if re.search(r"\d", display_value):
        return True
    # at least one token containing 2+ letters (e.g. company names, product names)
    return any(sum(ch.isalpha() for ch in tok) >= 2 for tok in display_value.split())


def _observed_summary(fields: dict, names: list[str]) -> str:
    parts = []
    for n in names:
        f = fields.get(n)
        if f and f.get("display_value"):
            parts.append(f"{n}: {f['display_value']}")
    return "; ".join(parts) if parts else "no matching declaration detected"


def check_declaration_present(rv: RuleVersion, params: dict, fields: dict, context: dict) -> tuple[str, str, str, float]:
    """Generic 'at least one of these fields is present' check. Returns (status, reason, observed, confidence).

    Policy: PASS requires a substantive detected value. Non-detection yields UNCERTAIN with
    language graded by image quality — OCR absence alone never establishes legal absence.
    """
    names = params.get("fields", [])
    present = []
    conflicting = []
    best_conf = 0.0
    for n in names:
        f, conf = _field_state(fields, n)
        if not f:
            continue
        if f.get("state") in ("DETECTED", "MANUALLY_CORRECTED") and _substantive(f.get("display_value")):
            present.append(n)
            best_conf = max(best_conf, conf)
        elif f.get("state") == "CONFLICTING":
            conflicting.append(n)
            best_conf = max(best_conf, conf)
    observed = _observed_summary(fields, names)
    if present:
        label = ", ".join(present)
        return (
            "PASS",
            f"Declaration appears present ({label}) with supporting evidence from the uploaded images.",
            observed,
            best_conf,
        )
    if conflicting:
        return (
            "UNCERTAIN",
            f"Declaration was detected for {', '.join(conflicting)} but candidate values conflict; "
            "manual verification required to resolve the correct value.",
            observed,
            best_conf,
        )
    if _human_confirmed_absent(fields, names):
        absent = [n for n in names if (fields.get(n) or {}).get("state") == "HUMAN_CONFIRMED_ABSENT"]
        return (
            "FAIL",
            f"Declaration ({', '.join(absent)}) was confirmed absent from the package by the reviewing "
            "inspector after manual examination.",
            observed,
            1.0,
            True,
        )
    # Nothing substantive detected. Distinguish "NOT FOUND" from "PROVEN ABSENT":
    # an ordinary photograph cannot prove absence, so this is always UNCERTAIN. Image quality
    # only grades the language and the reviewer's priors — it never upgrades to FAIL.
    quality = context.get("overall_quality", "POOR")
    if quality in ("GOOD", "ACCEPTABLE"):
        return (
            "UNCERTAIN",
            f"No declaration matching {names} was detected in readable images of adequate quality. "
            "This does not prove the declaration is absent from the package (it may appear on an "
            "unphotographed side or have been missed by OCR). Manual verification is required to "
            "establish presence or absence.",
            observed,
            0.5,
        )
    return (
        "UNCERTAIN",
        f"Declaration matching {names} could not be reliably verified from the supplied images "
        f"(image evidence limited, quality: {quality}). Manual verification required — "
        "absence from OCR is not proof of absence from the package.",
        observed,
        0.3,
    )


def check_net_quantity(rv: RuleVersion, params: dict, fields: dict, context: dict) -> tuple[str, str, str, float]:
    names = params.get("fields", ["net_quantity"])
    f, conf = _field_state(fields, "net_quantity")
    observed = _observed_summary(fields, names)
    if f and f.get("state") == "HUMAN_CONFIRMED_ABSENT":
        return (
            "FAIL",
            "Net quantity declaration was confirmed absent from the package by the reviewing inspector "
            "after manual examination.",
            observed,
            1.0,
            True,
        )
    if f and f.get("state") in ("DETECTED", "MANUALLY_CORRECTED") and _substantive(f.get("display_value")):
        try:
            q = json.loads(f.get("normalized_value") or "{}")
        except ValueError:
            q = {}
        unit = q.get("unit")
        valid_units = ("g", "kg", "ml", "L", "N", "cm", "m", "mm")
        if unit in valid_units:
            return (
                "PASS",
                f"Net quantity declaration detected as '{f['display_value']}' with a recognized standard unit "
                "(declaration only — the physical quantity inside the package is not verified by image).",
                observed,
                conf,
            )
        return (
            "UNCERTAIN",
            f"Net quantity detected ('{f['display_value']}') but the unit is non-standard; manual verification required.",
            observed,
            conf,
        )
    # Not detected: UNCERTAIN — never FAIL. (Missing OCR evidence ≠ missing declaration.)
    quality = context.get("overall_quality", "POOR")
    if quality in ("GOOD", "ACCEPTABLE"):
        return (
            "UNCERTAIN",
            "Net quantity declaration was not detected in readable images of adequate quality. "
            "This does not prove the declaration is absent from the package; manual verification "
            "is required to establish presence or absence.",
            observed,
            0.5,
        )
    return (
        "UNCERTAIN",
        "Net quantity declaration could not be reliably verified from the supplied images "
        "(image evidence limited). Manual verification required.",
        observed,
        0.3,
    )


def check_mrp_declaration(rv: RuleVersion, params: dict, fields: dict, context: dict) -> tuple[str, str, str, float]:
    f, conf = _field_state(fields, "mrp")
    observed = _observed_summary(fields, ["mrp", "unit_sale_price"])
    if f and f.get("state") == "HUMAN_CONFIRMED_ABSENT":
        return (
            "FAIL",
            "MRP declaration was confirmed absent from the package by the reviewing inspector after "
            "manual examination.",
            observed,
            1.0,
            True,
        )
    if f and f.get("state") in ("DETECTED", "MANUALLY_CORRECTED") and _substantive(f.get("display_value")):
        return (
            "PASS",
            f"MRP declaration detected ('{f['display_value']}') with supporting evidence. "
            "(This verifies the declaration is present — not the price actually charged at sale.)",
            observed,
            conf,
        )
    # unit sale price detected but no MRP: a common confusion case — call it out explicitly
    usp = fields.get("unit_sale_price")
    if usp and usp.get("state") in ("DETECTED", "MANUALLY_CORRECTED") and _substantive(usp.get("display_value")):
        return (
            "UNCERTAIN",
            f"A unit sale price ('{usp['display_value']}') was detected but no MRP declaration; a unit price is "
            "not a substitute for MRP. Manual verification required.",
            observed,
            0.4,
        )
    quality = context.get("overall_quality", "POOR")
    if quality in ("GOOD", "ACCEPTABLE"):
        return (
            "UNCERTAIN",
            "MRP declaration was not detected in readable images of adequate quality. This does not "
            "prove MRP is absent from the package (it may appear on an unphotographed side or have "
            "been missed by OCR). Manual verification is required to establish presence or absence.",
            observed,
            0.5,
        )
    return (
        "UNCERTAIN",
        "MRP could not be reliably detected from the available image evidence. Manual verification required.",
        observed,
        0.3,
    )


def check_legibility(rv: RuleVersion, params: dict, fields: dict, context: dict) -> tuple[str, str, str, float]:
    """Legibility: only claims visibility, never physical font-size compliance."""
    quality = context.get("overall_quality", "POOR")
    n_images = context.get("image_count", 0)
    if n_images == 0:
        return "UNCERTAIN", "No images available for legibility assessment.", "no images", 0.2
    if quality in ("GOOD", "ACCEPTABLE"):
        return (
            "PASS",
            "Declarations appear visible and readable in the supplied images at available resolution. "
            "(Physical font height in millimetres cannot be verified without scale calibration — "
            "manual verification required for physical size.)",
            f"image quality {quality}; {n_images} image(s)",
            0.7,
        )
    if quality == "UNUSABLE":
        return (
            "UNCERTAIN",
            "Image quality is unusable; legibility cannot be assessed. Manual verification required.",
            f"image quality {quality}",
            0.2,
        )
    return (
        "UNCERTAIN",
        f"Image quality is {quality}; declarations may be less legible than they appear. "
        "Estimated character height: pixels only; physical scale: unavailable; "
        "manual verification required.",
        f"image quality {quality}; {n_images} image(s)",
        0.3,
    )


def check_font_size(rv: RuleVersion, params: dict, fields: dict, context: dict) -> tuple[str, str, str, float, dict]:
    """Rule 7(2)-(3): is the printed declaration text at least the legally required height?

    The requirement is selected from the versioned rule data (Table-I / Table-II) — this function
    contains no legal constant of its own. A millimetre figure needs a scale, so the check states
    exactly what is missing (calibration and/or panel area) instead of guessing, and it refuses to
    FAIL on an estimated panel area alone: an estimate can straddle a Rule 7 bracket boundary, and
    a legal FAIL must not rest on that.
    """
    params = params or {}
    fm = context.get("font_measurement") or {}
    area = fm.get("panel_area_cm2")
    area_source = fm.get("panel_area_source") or "unavailable"
    family = fm.get("quantity_family")
    form = fm.get("packaging_form") or "normal"
    px_per_mm = fm.get("px_per_mm")
    measurements = list(fm.get("measurements") or [])
    width_min = float(params.get("width_ratio_min", 1.0 / 3.0))
    tolerance = float(params.get("borderline_tolerance", 0.9))

    detail: dict = {
        "available": False,
        "reference": rv.source_reference,
        "requirement": rv.requirement,
        "method": fm.get("method"),
        "px_per_mm": px_per_mm,
        "px_per_mm_source": fm.get("px_per_mm_source"),
        "px_per_mm_note": fm.get("px_per_mm_note"),
        "panel_area_cm2": area,
        "panel_area_source": area_source,
        "panel_area_note": fm.get("panel_area_note"),
        "packaging_form": form,
        "quantity_family": family,
        "cap_height_ratio": fm.get("cap_height_ratio"),
        "required_mm": None,
        "required_reference": None,
        "detected_mm": None,
        "detected_field": None,
        "detected_text": None,
        "satisfied": None,
        "meterage_floor_mm": None,
        "width_ratio_ok": None,
        "width_ratio_min": width_min,
        "measurements": measurements,
        "smallest_line": fm.get("smallest_line"),
        "tables": all_tables(params),
        "exempt_note": params.get("exempt_rule7_5", ""),
    }
    measured_pairs = [
        "{} {} mm".format(m.get("field_name"), m.get("letter_height_mm")) for m in measurements
    ]
    observed_prefix = (
        "declaration text measured on the pack: " + ", ".join(measured_pairs)
        if measurements
        else "no declaration region available to measure"
    )

    if not px_per_mm:
        return (
            "UNCERTAIN",
            "Rule 7 prescribes a minimum letter height in millimetres, and no scale calibration is "
            "available for these images, so physical text height cannot be measured. Record a "
            "calibration (a known printed length and its pixel span, or panel width × height in mm) "
            "and the height check is evaluated automatically.",
            observed_prefix,
            0.2,
            detail,
        )
    if not family:
        return (
            "UNCERTAIN",
            "The net quantity declaration (or its unit family) was not detected, and Rule 7(2) uses "
            "the nature of that declaration to choose between Table-I and Table-II. The applicable "
            "minimum cannot be selected without it; manual verification required.",
            observed_prefix,
            0.3,
            detail,
        )
    if not area:
        table = requirement_table(params, family, form)
        detail["applicable_table"] = table
        return (
            "UNCERTAIN",
            f"The area of the principal display panel is required to select the Rule 7 minimum "
            f"({table.get('id', 'Rule 7 table')} applies to this declaration). No measured or entered "
            "panel size is available, so the requirement cannot be fixed; enter the panel width × "
            "height (Rule 7(4) defines the area) to complete this check.",
            observed_prefix,
            0.3,
            detail,
        )
    requirement = requirement_for(params, quantity_family=family, area_cm2=float(area), form=form)
    if requirement is None:
        return (
            "UNCERTAIN",
            "The applicable Rule 7 minimum height could not be resolved from the rule data "
            "(declaration family or panel area outside the tabulated ranges); manual verification required.",
            observed_prefix,
            0.3,
            detail,
        )
    detail["available"] = True
    detail["required_mm"] = requirement.required_mm
    detail["required_reference"] = requirement.reference
    detail["required_table"] = requirement.as_dict()
    detail["applicable_table"] = requirement_table(params, family, form)
    if not measurements:
        return (
            "UNCERTAIN",
            f"The applicable Rule 7 minimum for this package is {requirement.required_mm} mm "
            f"({requirement.reference}), but no declaration text region with an evidence bounding "
            "box was available to measure. Manual measurement required.",
            observed_prefix,
            0.3,
            detail,
        )
    smallest = min(measurements, key=lambda m: float(m.get("letter_height_mm") or 99))
    detected = float(smallest.get("letter_height_mm") or 0.0)
    detail["detected_mm"] = detected
    detail["detected_field"] = smallest.get("field_name")
    detail["detected_text"] = smallest.get("text")

    # An ESTIMATED panel area can fall either side of a bracket boundary, so a shortfall is only
    # actionable below the next-lower bracket's requirement. A measured/entered area is
    # authoritative, so there the requirement itself is the floor.
    if area_source == "estimated" and requirement.lower_bracket_mm:
        floor = requirement.lower_bracket_mm
        floor_note = (
            f" panel area is an estimate ({area} cm²), so the most lenient adjacent bracket "
            f"({floor} mm) is applied as the floor for a shortfall"
        )
    else:
        floor = requirement.required_mm
        floor_note = ""
    detail["meterage_floor_mm"] = floor

    ratios = [m.get("width_ratio") for m in measurements if m.get("width_ratio")]
    min_ratio = min(ratios) if ratios else None
    width_ok = None if min_ratio is None else min_ratio >= width_min * 0.8
    detail["width_ratio_ok"] = width_ok
    detail["min_width_ratio"] = min_ratio
    width_note = (
        "" if min_ratio is None
        else f" Narrowest measured letter/number width ratio {min_ratio:.2f} "
             f"({'meets' if width_ok else 'below'} the Rule 7(3) one-third minimum, estimated from bbox width)."
    )

    measured_basis = (
        f"Smallest measured declaration letter height: {detected} mm "
        f"('{smallest.get('field_name')}': {str(smallest.get('text') or '')[:60]}). "
        f"Required minimum: {requirement.required_mm} mm — {requirement.reference}. "
        f"Method: {fm.get('method')} (calibration: {fm.get('px_per_mm_source')})."
        f"{floor_note}{width_note}"
    )

    if detected >= requirement.required_mm:
        detail["satisfied"] = True
        return (
            "PASS",
            f"Declared text height complies with Rule 7. {measured_basis}",
            f"{measured_basis} Measured declarations: {len(measurements)}.",
            round(0.75 if area_source in ("measured", "entered") else 0.55, 3),
            detail,
        )
    if detected < floor * tolerance:
        detail["satisfied"] = False
        return (
            "FAIL",
            f"Declared text is below the minimum height prescribed by Rule 7. {measured_basis} "
            "The shortfall is larger than the measurement tolerance (10% below the applicable "
            "floor), so it is reported as a positive, evidence-backed non-compliance for human "
            "confirmation.",
            f"{measured_basis} Measured declarations: {len(measurements)}.",
            0.6,
            detail,
        )
    detail["satisfied"] = None
    return (
        "UNCERTAIN",
        f"Declared text height is at or near the Rule 7 minimum but the measurement is not decisive. "
        f"{measured_basis} A calibrated close-up with the panel size measured by hand is required to "
        "establish compliance or a shortfall.",
        f"{measured_basis} Measured declarations: {len(measurements)}.",
        0.4,
        detail,
    )


def check_placement(rv: RuleVersion, params: dict, fields: dict, context: dict) -> tuple[str, str, str, float, dict]:
    """Rule 6(1) / Rule 9 — are the mandatory declarations borne on the package (label) in the
    place the rules require, as far as the supplied views can establish it?

    What this check CAN establish from evidence: which view of the package each watched declaration
    was read from, and whether every watched declaration was seen on a panel-type view.
    What it CANNOT establish: that the surface photographed IS the principal display panel — that
    depends on how the pack is presented for sale, and Rule 7(1) even allows a card or tape affixed
    to a package of 10 cm³ or less to serve as the principal display panel. A shortfall therefore
    yields UNCERTAIN (manual verification), never FAIL.
    """
    params = params or {}
    watched = list(params.get("fields") or [])
    panel_roles = tuple(params.get("panel_roles") or PANEL_ROLE_HINTS)
    pe = context.get("panel_evidence") or {}
    view_fields = pe.get("fields") or {}
    roles_supplied = [r for r in (pe.get("roles_supplied") or []) if r]

    detail: dict = {
        "reference": rv.source_reference,
        "requirement": rv.requirement,
        "watched_fields": watched,
        "panel_roles": list(panel_roles),
        "roles_supplied": roles_supplied,
        "views": pe.get("views") or [],
        "on_panel": [],
        "off_panel": [],
        "role_unknown": [],
        "not_detected": [],
        "principal_panel_established": False,
        "exempt_note": params.get("exempt_rule7_1", ""),
    }

    on_panel: list[tuple[str, str]] = []
    off_panel: list[tuple[str, str]] = []
    role_unknown: list[str] = []
    not_detected: list[str] = []
    for name in watched:
        row = view_fields.get(name) or {}
        value = (row.get("display_value") or "").strip()
        detected = row.get("state") in ("DETECTED", "MANUALLY_CORRECTED") and _substantive(value)
        if not detected:
            not_detected.append(name)
            continue
        role = row.get("role") or ""
        if role in panel_roles:
            on_panel.append((name, role))
        elif role:
            off_panel.append((name, role))
        else:
            role_unknown.append(name)

    detail["on_panel"] = [f"{n} ({role_label(r)})" for n, r in on_panel]
    detail["off_panel"] = [f"{n} ({role_label(r)})" for n, r in off_panel]
    detail["role_unknown"] = role_unknown
    detail["not_detected"] = not_detected

    observed = "; ".join(
        filter(
            None,
            [
                "on panel view: " + ", ".join(f"{n}" for n, _ in on_panel) if on_panel else "",
                "only on other views: " + ", ".join(f"{n} ({role_label(r)})" for n, r in off_panel)
                if off_panel
                else "",
                "view not identified: " + ", ".join(role_unknown) if role_unknown else "",
                "not detected: " + ", ".join(not_detected) if not_detected else "",
            ],
        )
    ) or "no declaration from the placement watch-list was detected"

    if not watched:
        return (
            "UNCERTAIN",
            "No declaration watch-list is configured for this placement check; manual verification required.",
            observed,
            0.2,
            detail,
        )
    if not any((on_panel, off_panel, role_unknown)):
        return (
            "UNCERTAIN",
            "None of the declarations this check watches for was detected in the supplied images, so "
            "their placement cannot be assessed. Absence from these images is not evidence of a "
            "placement breach; manual verification required.",
            observed,
            0.3,
            detail,
        )

    panel_views_captured = [r for r in roles_supplied if r in panel_roles]
    detail["principal_panel_established"] = bool(panel_views_captured)

    if off_panel and not on_panel and not role_unknown:
        views = ", ".join(sorted({role_label(r) for _, r in off_panel}))
        return (
            "UNCERTAIN",
            f"Every watched declaration was read from a {views}, not from a front/top panel view. "
            "This does not by itself mean the declarations are in the wrong place — a side or bottom "
            "surface can carry declarations lawfully, and Rule 7(1) permits a card or tape affixed to "
            "a small package to serve as the principal display panel. The principal display panel "
            "cannot be established from these images; manual verification required.",
            observed,
            0.4,
            detail,
        )

    if on_panel and not off_panel:
        unknown_note = (
            ""
            if not role_unknown
            else f" {len(role_unknown)} watched declaration(s) came from images with no role label, so "
            "they were not used to reach this result."
        )
        detail["satisfied"] = True
        return (
            "PASS",
            "Every watched declaration was read from a panel-type view of the package "
            f"({', '.join(sorted({role_label(r) for _, r in on_panel}))}), which is consistent with "
            "Rule 6(1) (declaration borne on the package or on a label securely affixed to it) and "
            "Rule 9 (legible and prominent). This records WHERE the declarations were photographed; "
            "it does not certify that the photographed surface is the principal display panel as a "
            "matter of law. " + (params.get("exempt_rule7_1", "") or "").strip() + unknown_note,
            observed,
            0.55,
            detail,
        )

    if on_panel and off_panel:
        views = ", ".join(sorted({role_label(r) for _, r in off_panel}))
        return (
            "UNCERTAIN",
            "Watched declarations were split across views: "
            f"{', '.join(n for n, _ in on_panel)} were read from a panel-type view, while "
            f"{', '.join(n for n, _ in off_panel)} appeared only on a {views}. Whether the package "
            "satisfies the placement requirement cannot be settled from these images; manual "
            "verification required.",
            observed,
            0.4,
            detail,
        )

    # Declarations detected, but every evidence image lacks a role label -> the app cannot tell a
    # panel view from any other view. Say exactly that instead of guessing.
    return (
        "UNCERTAIN",
        "The watched declarations were detected, but the images carrying them have no view role "
        "assigned (front / back / side / ...), so which surface they appear on cannot be determined. "
        "Assign image roles and the placement check is evaluated automatically; until then, manual "
        "verification required.",
        observed,
        0.3,
        detail,
    )


def check_manual_only(rv: RuleVersion, params: dict, fields: dict, context: dict) -> tuple[str, str, str, float]:
    return (
        "UNCERTAIN",
        f"{rv.title}: cannot be verified automatically from image evidence — manual verification required. "
        f"{rv.description}",
        "not automatically verified",
        0.1,
    )


CHECKS = {
    "declaration_present": check_declaration_present,
    "net_quantity": check_net_quantity,
    "mrp_declaration": check_mrp_declaration,
    "legibility": check_legibility,
    "font_size": check_font_size,
    "placement": check_placement,
    "manual_only": check_manual_only,
}


def evaluate_rule(rv: RuleVersion, fields: dict, context: dict, applicability) -> RuleOutcome:
    """Evaluate one rule version against extracted fields. Deterministic, evidence-based."""
    if not applicability.applies:
        return RuleOutcome(
            rule_id=getattr(rv, "rule_id", "") or "",
            rule_version_id=rv.id,
            rule_number=getattr(rv, "rule_number", "") or "",
            title=rv.title,
            requirement=rv.requirement,
            status="NOT_APPLICABLE",
            reason=applicability.reason,
            observed="",
            expected=rv.requirement,
            confidence=1.0,
            critical=False,
            applicability_reason=applicability.reason,
            weight=_weight(rv),
            applicability_kind=getattr(applicability, "kind", "") or "",
            check_type=rv.check_type,
        )
    check = CHECKS.get(rv.check_type)
    result: tuple | None = None
    if check is None:
        status, reason, observed, conf = (
            "UNCERTAIN",
            f"Unknown check type '{rv.check_type}' — manual verification required.",
            "",
            0.1,
        )
    else:
        result = check(rv, rv.params or {}, fields, context)
        status, reason, observed, conf = result[:4]
        detail = result[4] if len(result) > 4 else {}
    return RuleOutcome(
        rule_id=getattr(rv, "rule_id", "") or "",
        rule_version_id=rv.id,
        rule_number=getattr(rv, "rule_number", "") or "",
        title=rv.title,
        requirement=rv.requirement,
        status=status,
        reason=reason,
        observed=observed,
        expected=rv.requirement,
        confidence=conf,
        critical=_is_critical(rv),
        applicability_reason=applicability.reason,
        weight=_weight(rv),
        applicability_kind=getattr(applicability, "kind", "") or "",
        check_type=rv.check_type,
        human_confirmed=bool(status == "FAIL" and result is not None and len(result) == 5 and result[4] is True),
        detail=detail if isinstance(detail, dict) else {},
    )


def _is_critical(rv: RuleVersion) -> bool:
    """Critical rules drive NON_COMPLIANT when a FAIL is confirmed (human-confirmed or
    evidence-backed).

    The rule DEFINITION owns this (``critical`` in backend/rules/definitions/*.json, seeded onto
    the rule version). The check-type mapping below is only a fallback for a rule that does not
    declare it, so a regulatory re-prioritisation needs no code change.
    """
    declared = getattr(rv, "critical", None)
    if declared is not None:
        return bool(declared)
    return rv.check_type in ("net_quantity", "mrp_declaration")


def _weight(rv: RuleVersion) -> float:
    """Scoring weight: declared on the rule, else 2.0 for a critical requirement and 1.0 otherwise.

    Data-first by design — the compliance percentage is defined by the rule data, not by this file.
    """
    declared = getattr(rv, "weight", None)
    if declared is not None:
        try:
            value = float(declared)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return 2.0 if _is_critical(rv) else 1.0
