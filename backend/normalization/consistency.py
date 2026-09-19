"""Date consistency engine: temporal coherence between declared dates.

Answers questions like: "Is the best-before consistent with the manufacturing date and the
declared shelf-life duration?" This is EXPLANATORY analysis for the reviewer — it never
overwrites values, never guesses a format, and its findings feed uncertainty_reasons.
All comparisons operate on what was actually extracted; missing sides simply skip.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass


@dataclass
class Finding:
    code: str  # CONSISTENT | INCONSISTENT | UNVERIFIABLE
    message: str


def _as_date(value: str | None) -> _dt.date | None:
    if not value:
        return None
    v = value.replace("|AMBIGUOUS", "")
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            return _dt.date.fromisoformat(v)
        if re.match(r"^\d{4}-\d{2}$", v):
            return _dt.date(int(v[:4]), int(v[5:7]), 1)
    except ValueError:
        return None
    return None


def _duration(value: str | None) -> tuple[_dt.timedelta | None, str]:
    """Parse a duration value ('12 months', '18 months', or a semantic JSON duration)."""
    if not value:
        return None, ""
    if value.startswith("{"):
        try:
            data = json.loads(value)
        except ValueError:
            return None, ""
        if data.get("type") == "DURATION_FROM_REFERENCE":
            inner_duration, _inner_unit = _duration(str(data.get("duration", "")))
            return inner_duration, str(data.get("reference", ""))
        return None, ""
    m = re.match(r"^(\d{1,3})\s*(month|months|day|days|year|years|week|weeks)$", value.strip(), re.IGNORECASE)
    if not m:
        return None, ""
    n, unit = int(m.group(1)), m.group(2).lower()
    if "month" in unit:
        return _dt.timedelta(days=30 * n), "MONTHS"
    if "year" in unit:
        return _dt.timedelta(days=365 * n), "YEARS"
    if "week" in unit:
        return _dt.timedelta(days=7 * n), "WEEKS"
    return _dt.timedelta(days=n), "DAYS"


def check_consistency(dates: dict[str, str]) -> list[Finding]:
    """Cross-check extracted date fields. `dates` maps field_name -> raw value.

    Findings are advisory: they are attached to the review record and shown in the UI;
    they never modify extraction and never create legal conclusions by themselves.
    """
    findings: list[Finding] = []
    mfg = _as_date(dates.get("date_manufacturing"))
    pkd = _as_date(dates.get("date_packing"))
    expiry = _as_date(dates.get("date_expiry"))

    # best-before may be a date, a bare duration, or a semantic duration-from-reference
    bb_raw = dates.get("date_best_before")
    bb_date = _as_date(bb_raw)
    bb_duration, bb_reference = _duration(bb_raw)
    if bb_duration is not None and bb_reference:
        ref_date = {
            "MANUFACTURING_DATE": mfg,
            "PACKING_DATE": pkd,
            "IMPORT_DATE": _as_date(dates.get("date_import")),
        }.get(bb_reference)
        if ref_date and bb_duration:
            implied = ref_date + bb_duration
            findings.append(Finding(
                "CONSISTENT" if (not expiry or abs((expiry - implied).days) <= 45) else "INCONSISTENT",
                f"Best-before '{bb_duration.days // 30} month(s) from {bb_reference.lower().replace('_date', '')}' "
                f"implies approximately {implied.isoformat()}"
                + (f"; expiry declaration reads {expiry.isoformat()}" + (
                    " — consistent" if abs((expiry - implied).days) <= 45 else " — material difference, verify with the manufacturer's declaration"
                ) if expiry else "."),
            ))
        else:
            findings.append(Finding(
                "UNVERIFIABLE",
                f"Best-before is declared as a duration from {bb_reference.lower().replace('_', ' ')}, "
                "but the reference date was not detected in the supplied images — temporal consistency "
                "cannot be verified from the available evidence.",
            ))
    elif bb_date and expiry:
        delta = (expiry - bb_date).days
        findings.append(Finding(
            "CONSISTENT" if -45 <= delta <= 45 else "INCONSISTENT",
            f"Best-before {bb_date.isoformat()} vs expiry {expiry.isoformat()}"
            + (" — consistent" if -45 <= delta <= 45 else " — differ materially; verify which declaration is correct"),
        ))
    elif bb_duration and not bb_reference:
        # bare duration without reference (e.g. '12 months'): informational only
        findings.append(Finding(
            "UNVERIFIABLE",
            f"Best-before declares a duration ({bb_raw}); the reference date (manufacturing/packing) "
            "was not detected, so the effective date cannot be computed from image evidence.",
        ))

    if mfg and pkd:
        delta = (pkd - mfg).days
        findings.append(Finding(
            "CONSISTENT" if 0 <= delta <= 90 else "INCONSISTENT",
            f"Packing {pkd.isoformat()} vs manufacturing {mfg.isoformat()}"
            + (" — consistent" if 0 <= delta <= 90 else " — packing before manufacturing or far after; verify"),
        ))
    if mfg and expiry and not bb_raw:
        shelf = (expiry - mfg).days
        findings.append(Finding(
            "CONSISTENT" if 0 < shelf <= 3650 else "INCONSISTENT",
            f"Shelf life implied by manufacturing {mfg.isoformat()} → expiry {expiry.isoformat()}: {shelf} days"
            + ("" if 0 < shelf <= 3650 else " — implausible; verify"),
        ))
    return findings


def findings_summary(findings: list[Finding]) -> str:
    """One-line summary for review/UI display."""
    if not findings:
        return ""
    counts = {"CONSISTENT": 0, "INCONSISTENT": 0, "UNVERIFIABLE": 0}
    for f in findings:
        counts[f.code] = counts.get(f.code, 0) + 1
    parts = []
    if counts["INCONSISTENT"]:
        parts.append(f"{counts['INCONSISTENT']} inconsistency(ies)")
    if counts["UNVERIFIABLE"]:
        parts.append(f"{counts['UNVERIFIABLE']} unverifiable (missing reference date)")
    if counts["CONSISTENT"]:
        parts.append(f"{counts['CONSISTENT']} consistent")
    return "Date consistency: " + ", ".join(parts) + "."
