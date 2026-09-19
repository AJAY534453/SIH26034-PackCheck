"""Script detection and multilingual duplicate-evidence handling.

Packages routinely carry the same declaration in multiple languages/scripts (English +
Tamil + Hindi ...). Two principles:

1. Detection: classify each OCR line's script (Latin, Tamil, Devanagari, Telugu,
   Malayalam, Kannada, Bengali, Gujarati, Gurmukhi, Oriya, Arabic, CJK) by Unicode
   ranges. Reported as OCR provenance and used by capture guidance.

2. Conflict policy: when two candidates for the SAME field carry values in DIFFERENT
   scripts, they are treated as duplicate semantic evidence of one declaration — NOT a
   conflict. Only values that differ within the SAME script can be a true CONFLICT.
   This prevents multilingual packaging from generating false review flags.

RapidOCR is used as-is for recognition; Indic script support depends on the installed
models. When a non-Latin script is detected but no Indic-capable engine produced the
text, lines are still preserved (raw evidence) and the field-level policy above still
applies to whatever was recognized.
"""
from __future__ import annotations

import re
import unicodedata

# Unicode block ranges per script (checked against the first codepoint of each char).
SCRIPT_RANGES: list[tuple[str, tuple[int, int], ...]] = [
    ("Tamil", (0x0B80, 0x0BFF)),
    ("Telugu", (0x0C00, 0x0C7F)),
    ("Kannada", (0x0C80, 0x0CFF)),
    ("Malayalam", (0x0D00, 0x0D7F)),
    ("Devanagari", (0x0900, 0x097F)),
    ("Bengali", (0x0980, 0x09FF)),
    ("Gujarati", (0x0A80, 0x0AFF)),
    ("Gurmukhi", (0x0A00, 0x0A7F)),
    ("Oriya", (0x0B00, 0x0B7F)),
    ("Arabic", (0x0600, 0x06FF)),
    ("Han", (0x4E00, 0x9FFF)),
    ("Hangul", (0xAC00, 0xD7AF)),
    ("Katakana", (0x30A0, 0x30FF)),
    ("Hiragana", (0x3040, 0x309F)),
]
_LATIN_RE = re.compile(r"[A-Za-z]")


def detect_script(text: str) -> str:
    """Dominant script of a text line ('Latin', 'Tamil', ..., or 'Unknown')."""
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isspace() or ch in "0123456789.,:;/-₹()[]":
            continue
        cp = ord(ch)
        for name, *ranges in SCRIPT_RANGES:
            for lo, hi in ranges:
                if lo <= cp <= hi:
                    counts[name] = counts.get(name, 0) + 1
                    break
            else:
                continue
            break
        if _LATIN_RE.match(ch):
            counts["Latin"] = counts.get("Latin", 0) + 1
    if not counts:
        return "Unknown"
    return max(counts, key=counts.get)


def scripts_present(lines) -> list[str]:
    """Distinct scripts present across OCR lines (order of first appearance)."""
    out: list[str] = []
    for l in lines:
        s = detect_script(l.text)
        if s not in ("Unknown",) and s not in out:
            out.append(s)
    return out


def same_script(a: str, b: str) -> bool:
    """True when two values belong to the same script (per dominant-script detection)."""
    return detect_script(a) == detect_script(b)


def is_multilingual_duplicate(field_name: str, value_a: str, value_b: str) -> bool:
    """True when two differing values for the same field are duplicate declarations in
    different scripts (e.g. an English product title and its Tamil equivalent).

    Such pairs are NOT conflicts — they are independent evidence of the same declaration
    and must both be retained without flagging CONFLICT.
    """
    if not value_a or not value_b:
        return False
    if same_script(value_a, value_b):
        return False
    return True


def language_provenance_note(lines) -> str:
    """Human-readable note about scripts encountered (for evidence/audit display)."""
    scripts = scripts_present(lines)
    if len(scripts) <= 1:
        return ""
    return "Multilingual label detected (" + ", ".join(scripts) + "); same-field values in different scripts are treated as duplicate evidence, not conflicts."
