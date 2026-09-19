"""Manufacturer/packer/importer extraction: line grouping + context + nutrition exclusion.

Core semantic rule: role keywords ("Manufactured by", "Marketed by", "Packed by", ...)
are LABELS/ANCHORS, never values. The extractor locates the associated entity on the same
line or immediately below the anchor. If only the anchor is found, NO candidate is emitted —
the field is recorded as MISSING/UNCERTAIN by the pipeline, never as the anchor text itself.

Address numbers are preserved (e.g. 'Vellakovil - 638111'). Nutrition rows never leak into
manufacturer/address fields.
"""
from __future__ import annotations

import re

from backend.extraction.base import (
    Candidate,
    compose_confidence,
    is_nutrition_line,
    lines_sorted,
    looks_like_company_entity,
)
from backend.extraction.field_schema import anchor_regex
from backend.extraction.product_identity import RELATIONSHIP_INSTRUCTION_RE
from backend.extraction.text_repair import repair_ocr_spacing
from backend.ocr.base import OcrLine

def _role_anchor(field_id: str) -> re.Pattern:
    """Role anchors are DERIVED from the declaration field schema — one source of truth.

    A hand-maintained copy here had drifted from the schema: it listed 'mfd by'/'mfg by' but not
    the dotted 'Mfd. by', so a perfectly legible manufacturer line produced NO entity at all (the
    anchor never matched and the text was re-read as an unanchored company line). Deriving the
    patterns means widening the schema automatically widens recognition, and this class of drift
    cannot recur silently.
    """
    pattern = anchor_regex(field_id)
    if pattern is None:  # the schema lost a field: fail loudly rather than disable a role
        raise RuntimeError(f"field schema declares no anchor for '{field_id}'")
    return pattern


ROLE_KEYWORDS = {
    "manufacturer": _role_anchor("manufacturer"),
    "packer": _role_anchor("packer"),
    "importer": _role_anchor("importer"),
    "marketer": _role_anchor("marketer"),
}

# A company/entity name: contains a legal suffix or ampersand, or is a capitalized multi-word
# line that is clearly not an address continuation.
COMPANY_SUFFIX_RE = re.compile(
    r"\b(?:pvt|private|ltd|limited|llp|industries|enterprises|foods|traders|agency|company|co\.|"
    r"corporation|org|firm|products|product|producers|manufacturers|packers|importers|distributors)\b\.?",
    re.IGNORECASE,
)
# An anchor-only remnant after stripping: no entity was identified.
ANCHOR_ONLY_RE = re.compile(
    r"^(?:manufactured?|mfd|mfg(?:d)?|packed?|pkd|imported?|marketed?|by|at|for|and|&|address|name|"
    r"details|supplier|vendor|made|the|[:.,;\-\s&]|yes|no\.?)*$",
    re.IGNORECASE,
)
# Pure fragment noise: OCR remnants that are neither entity, address, nor label
# (nutrition-column fragments like '(approx)', punctuation-only runs, stray single letters).
# Deliberately does NOT match capitalized multi-letter words, so real entity/address lines pass.
FRAGMENT_NOISE_RE = re.compile(
    r"^(?:[().,;:_\-\s]*[a-z][().,;:_\-\s]*|[\W_]+|(?:\b[a-zA-Z]\b[\s.,]*)+)$"
)

ADDRESS_CONT_RE = re.compile(r"^[^A-Za-z0-9]*(?:[A-Z0-9][^,;:]{2,})$")  # permissive: an address continuation line
PIN_IN_LINE_RE = re.compile(r"\b[1-9]\d{5}\b")
FIELD_LABEL_RE = re.compile(
    r"\b(?:m\.?r\.?p\.?|mrp|net\s*(?:qty|wt|weight|quantity)|batch|lot|fssai|lic(?:ence|ense)|exp|expiry|"
    r"best\s*before|use\s*by|mfd|mfg|packed|customer\s*care|consumer\s*care|email|www|phone|toll)\b",
    re.IGNORECASE,
)
STREET_HINT_RE = re.compile(
    r"\b(?:no\.?\s*\d|\d{1,3}/\d|road|street|nagar|lane|sector|marg|floor|block|plot|suite|"
    r"opp|near|behind|behld|pin(?:code)?|dist|district|talu[kq]|taluk)\b|"
    r"\b[1-9]\d{5}\b",
    re.IGNORECASE,
)


#: Additional printing labels that introduce an entity but are not part of its name. OCR fuses
#: them ('Licensed TM Users:AvA Cholayil…'), so separators between words are optional here.
_LICENCE_LABEL_RE = re.compile(
    r"^(?:licensed\s*)?(?:tm|trade\s*mark)\s*users?\s*[:\-]?\s*|^licensed\s*users?\s*[:\-]?\s*",
    re.IGNORECASE,
)

#: Where an address starts inside an 'Entity, address…' block printed on the anchor line.
_ADDRESS_START_RE = re.compile(
    r"\b(?:no\.?\s*\d|plot|block|sector|street|road|nagar|lane|marg|floor|dist(?:rict)?|taluk|taluq)\b|"
    r"\b[1-9]\d{5}\b|\bm\.?l\.?\s*no\b|\blic(?:ence|ense)\s*no\b",
    re.IGNORECASE,
)


def _clean_company_name(raw: str, role: str) -> str:
    """Strip the leading role keyword/anchor. Returns the residual entity text ('' if none)."""
    name = re.sub(ROLE_KEYWORDS[role], " ", raw, count=1)
    name = name.replace(":", " ", 1)
    # strip any residual role words if the OCR merged labels ("Mfd. & Mkt. by")
    for kw_re in ROLE_KEYWORDS.values():
        name = kw_re.sub(" ", name, count=1)
    name = re.sub(r"^\s*[-–—:.,]*\s*", "", name)
    # Printing labels that INTRODUCE the entity are labels, not part of the entity's name.
    name = _LICENCE_LABEL_RE.sub("", name, count=1)
    name = re.sub(r"\s{2,}", " ", name).strip(" ,;:-")
    if ANCHOR_ONLY_RE.match(name):
        return ""
    # Restore word boundaries lost by the recogniser for the DISPLAY value (raw OCR is kept
    # separately on the candidate) — 'Pvt.Ltd.' -> 'Pvt. Ltd.'.
    return repair_ocr_spacing(name)


def _split_entity_address(name: str) -> tuple[str, str]:
    """Split an 'Entity, address…' block into (entity, address tail).

    The printed entity declaration frequently carries its own address on the same line. Keeping
    the address inside the entity value publishes an address as a company name; cutting it away
    without keeping it would lose a declaration that is on the package. Both parts are therefore
    returned: the entity as the company name and the tail as the address, with the full printed
    block still retained as the candidate's raw text.
    """
    if not name:
        return "", ""
    match = _ADDRESS_START_RE.search(name)
    if match is None or match.start() == 0:
        return name, ""
    head = name[: match.start()].strip(" ,;:-")
    tail = name[match.start():].strip(" ,;:-")
    if len(head) < 4 or not re.search(r"[A-Za-z]{3}", head):
        return name, ""  # too little left to be an entity — never invent a split
    return repair_ocr_spacing(head), repair_ocr_spacing(tail)


# Company-suffix words checked WITHOUT word boundaries: real-world OCR routinely fuses
# company words together (e.g. a brand word + 'PRODUCT' with no space), so \b-anchored
# matching misses them. A boundary-less containment check is safe here because the line
# has already passed the label/address exclusion guards above.
_FUSED_SUFFIX_WORDS = (
    "pvt", "private", "ltd", "limited", "llp", "industries", "enterprises", "foods",
    "traders", "agency", "company", "corporation", "firm", "products", "product",
    "producers", "manufacturers", "packers", "importers", "distributors",
)


def _looks_like_entity(text: str) -> bool:
    """Heuristic: does this line look like a company/entity name (not an address line)?"""
    t = text.strip().strip(":,;-")
    if not t or len(t) < 3:
        return False
    if ANCHOR_ONLY_RE.match(t):
        return False
    if FIELD_LABEL_RE.search(t):
        return False
    if RELATIONSHIP_INSTRUCTION_RE.search(t):
        # 'For Mfd. Unit Address, please refer to the first character of the Batch No.' is an
        # instruction, not a company entity — it must never win the entity contest either.
        return False
    if STREET_HINT_RE.search(t):
        return False  # address-like content
    if COMPANY_SUFFIX_RE.search(t):
        return True
    # fused names: a company word run into its neighbour ('INDUSTRIESLTD.') defeats the
    # word-boundary test above, so the squashed-view check decides those.
    if looks_like_company_entity(t):
        return True
    # fused single-token names: one long alphabetic token containing a company word
    # (e.g. brand+PRODUCT with no space) — words >= 2 handles spaced names, this handles fusion.
    if len(t) >= 6 and t.isalpha() and any(w in t.lower() for w in _FUSED_SUFFIX_WORDS):
        return True
    # capitalized multi-word line with few digits → likely a name
    digits = sum(ch.isdigit() for ch in t)
    words = t.split()
    return len(words) >= 2 and digits <= 1 and t[0].isalpha()


def _looks_like_field_label(line_text: str) -> bool:
    return bool(FIELD_LABEL_RE.search(line_text))


def extract_manufacturer(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    """Extract manufacturer/packer/importer/marketer candidates with following address lines.

    Guarantees:
    - the anchor text itself is NEVER emitted as a value;
    - the entity is taken from the remainder of the anchor line or the first following
      entity-like line (company names win over address-looking lines);
    - anchor-only detections produce no candidate (field becomes MISSING/UNCERTAIN).
    """
    lines = lines_sorted(lines)
    fl = flags if flags is not None else [False] * len(lines)
    candidates: list[Candidate] = []
    # Lines already used as a role anchor or as its associated entity/address. The unanchored
    # entity pass below must not re-offer them (that would raise a false conflict between the
    # anchored reading and the same reading offered as "no anchor").
    anchored_idx: set[int] = set()

    for idx, line in enumerate(lines):
        text = line.text.strip()
        if not text or fl[idx]:
            continue
        for role, kw_re in ROLE_KEYWORDS.items():
            m = kw_re.search(text)
            if not m:
                continue
            anchored_idx.add(idx)

            name = _clean_company_name(text, role)
            addr_lines: list[str] = []
            # An entity printed with its own address on the anchor line: the address belongs to the
            # address declaration, not inside the company name.
            name, anchor_address = _split_entity_address(name)
            if anchor_address:
                addr_lines.append(anchor_address)

            # Walk the following lines: the FIRST one that looks like an entity is the name
            # (when the anchor line held no entity); subsequent address-like lines are grouped.
            # SPATIAL GUARD: labels are read column-by-column on real packages — a line with
            # no horizontal overlap with the anchor (e.g. a nutrition-table remnant beside the
            # declaration panel) belongs to another column and must be skipped, not treated as
            # a walk-terminating boundary. Pure-fragment noise lines are skipped too.
            ax0, ax1 = lines[idx].bbox[0], lines[idx].bbox[2]

            def _same_column(other: OcrLine) -> bool:
                return min(ax1, other.bbox[2]) - max(ax0, other.bbox[0]) > 0.2 * (ax1 - ax0)

            j = idx + 1
            steps = 0
            while j < len(lines) and steps < 8:
                nxt_line = lines[j]
                nxt = nxt_line.text.strip()
                if not nxt or fl[j] or FRAGMENT_NOISE_RE.match(nxt):
                    j += 1  # blank/nutrition/noise lines are skips, not semantic steps
                    continue
                if not _same_column(nxt_line):
                    j += 1  # another column's line likewise
                    continue
                if _looks_like_field_label(nxt):
                    break
                if ROLE_KEYWORDS["manufacturer"].search(nxt) and role != "manufacturer":
                    break
                if not name and _looks_like_entity(nxt):
                    name = repair_ocr_spacing(nxt)
                    anchored_idx.add(j)
                    j += 1
                    steps += 1
                    continue
                if name:
                    # Skip lines duplicating the already-identified entity (association
                    # can synthesize a combined 'entity, address' line that would otherwise
                    # be re-read as fresh address content).
                    if nxt == name or nxt.startswith(name + ",") or name.startswith(nxt + ","):
                        j += 1
                        steps += 1
                        continue
                if name and len(addr_lines) < 3 and (
                    ADDRESS_CONT_RE.match(nxt)
                    or PIN_IN_LINE_RE.search(nxt)
                    or ("," in nxt and len(nxt) > 8 and not _looks_like_field_label(nxt))
                ):
                    if any(nxt in a or a in nxt for a in addr_lines):
                        # same address line observed twice (plain + association-synthesized
                        # variants of one physical line) — skip the repeat, keep walking
                        j += 1
                        steps += 1
                        continue
                    anchored_idx.add(j)
                    addr_lines.append(repair_ocr_spacing(nxt))
                    j += 1
                    steps += 1
                    continue
                break

            if not name:
                # Anchor found but no entity: emit NOTHING for the name. Address lines gathered
                # before an entity are ambiguous — drop them too (never guess).
                if not addr_lines:
                    break
                # keep an address-only observation as the *_address field (still useful evidence)
                field = f"{role}_address"
                value = ", ".join(addr_lines)
            else:
                field = role
                value = name + ((", " + ", ".join(addr_lines)) if addr_lines else "")

            conf = compose_confidence(lines[idx].confidence, 0.85 if name else 0.55, 0.75)
            provenance = " | ".join([text] + addr_lines)
            candidates.append(
                Candidate(
                    field_name=field,
                    value=value,
                    raw_value=provenance,
                    confidence=round(conf, 3),
                    score=conf,
                    engine=lines[idx].engine,
                    variant=lines[idx].variant,
                    bbox=lines[idx].bbox,
                    source_text=provenance,
                    reason=(
                        f"entity after '{m.group(0)}' anchor"
                        if name
                        else f"'{m.group(0)}' anchor detected; entity not identified — address context only"
                    ),
                )
            )
            # The address is ALSO offered as its own field. The entity declaration as printed
            # includes the address, so the *_address field is populated additively — the entity
            # value keeps the full printed block (nothing is removed) while the address becomes
            # separately reportable, auditable and reviewable.
            if name and addr_lines and not field.endswith("_address"):
                addr_conf = compose_confidence(lines[idx].confidence, 0.7, 0.7)
                candidates.append(
                    Candidate(
                        field_name=f"{role}_address",
                        value=", ".join(addr_lines),
                        raw_value=provenance,
                        confidence=round(addr_conf, 3),
                        score=addr_conf,
                        engine=lines[idx].engine,
                        variant=lines[idx].variant,
                        bbox=lines[idx].bbox,
                        source_text=provenance,
                        reason=f"address lines grouped under '{m.group(0)}' anchor (spatial order)",
                    )
                )
            break

    # ---------- unanchored company entity ----------
    # A package may print its company name with no role word read at all, or the role word may be
    # unreadable while the name itself is perfectly legible. The entity is still a printed
    # declaration, so it is offered as the manufacturer with the role explicitly marked
    # UNRESOLVED — never dropped, and never allowed to become the product name instead. Which
    # legal role it fills is the inspector's call, so the value is offered for review.
    for idx, line in enumerate(lines):
        text = line.text.strip()
        if not text or fl[idx] or idx in anchored_idx:
            continue
        if any(kw_re.search(text) for kw_re in ROLE_KEYWORDS.values()):
            continue  # the line names its own role: it is not an unanchored entity
        if not looks_like_company_entity(text):
            continue
        if FIELD_LABEL_RE.search(text) or is_nutrition_line(lines, idx, fl):
            continue
        if STREET_HINT_RE.search(text) or len(text) > 90 or len(text.split()) > 9:
            continue
        conf = compose_confidence(line.confidence, 0.6, 0.6)
        candidates.append(
            Candidate(
                field_name="manufacturer",
                value=text,
                raw_value=text,
                confidence=round(conf, 3),
                score=conf,
                engine=line.engine,
                variant=line.variant,
                bbox=line.bbox,
                source_text=text,
                reason=(
                    "company / legal-entity line printed with no role anchor in the supplied "
                    "images — the exact role (manufacturer / packer / marketer / importer) is "
                    "unresolved, so the entity is offered for inspector confirmation"
                ),
                role_uncertain=True,
            )
        )
    return candidates


#: Country-of-origin wordings. The schema declares 'Country of Origin', 'Origin', 'Made in',
#: 'Product of' and 'Produce of' — the hand-written regex here previously covered only the first
#: two, so 'MADE IN INDIA' (the wording actually printed on most Indian packs) yielded NO country
#: candidate and was left to be mistaken for the product title instead.
_ORIGIN_RE = re.compile(
    r"\b(?:country\s*of\s*(?:origin|produce)|origin)\b\s*[:\-]?\s*([A-Za-z]{3,}[A-Za-z\s]{0,38})"
    # 'made in' is space-tolerant: OCR fuses it ('…M.LNo.:AUS-579MADEININDIA'), and a boundary
    # requirement then loses the declaration entirely.
    r"|(?<![A-Za-z])(?:made|manufactured|produced|packed)\s*in\s*[:\-]?\s*([A-Za-z]{3,}[A-Za-z\s]{0,38})"
    r"|\b(?:product|produce)\s*of\b\s*[:\-]?\s*([A-Za-z]{3,}[A-Za-z\s]{0,38})",
    re.IGNORECASE,
)
#: A country name is at most a few words; stop the capture at a connector or punctuation so
#: 'Made in India for ACME' yields 'India', not 'India for ACME'.
_ORIGIN_TRAIL_RE = re.compile(r"\b(?:for|by|and|at|with|only)\b|[,;./|]", re.IGNORECASE)


def extract_country_of_origin(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    """Country of origin: 'Country of Origin: India', 'Origin: India', 'MADE IN INDIA'."""
    lines = lines_sorted(lines)
    candidates: list[Candidate] = []
    for line in lines:
        m = _ORIGIN_RE.search(line.text)
        if not m:
            continue
        raw_value = next((g for g in m.groups() if g), "")
        trail = _ORIGIN_TRAIL_RE.search(raw_value)
        if trail:
            raw_value = raw_value[: trail.start()]
        value = raw_value.strip(" .,;-")
        if not value:
            continue
        conf = compose_confidence(line.confidence, 0.9, 0.85)
        candidates.append(
            Candidate(
                field_name="country_of_origin",
                value=value.title(),
                raw_value=line.text.strip(),
                confidence=round(conf, 3),
                score=conf,
                engine=line.engine,
                variant=line.variant,
                bbox=line.bbox,
                source_text=line.text.strip(),
                reason="'country of origin' declaration matched",
            )
        )
    return candidates
