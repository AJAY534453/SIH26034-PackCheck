"""Display repair for OCR word boundaries — never a change of the reading.

OCR on real packaging returns runs of characters whose WORD BOUNDARIES were lost: the same glyphs,
fused ('AYURVEDIC SOAPWITH18HERBS', 'BRITANNIA INDUSTRIESLTD.', 'Mfd.By', 'TOILETSOAP'). The
characters are the evidence; the missing separators are a typographic artefact of the recogniser.

This module restores separators for the *display* value under a conservative, checkable rule:

* a fused run is split ONLY when it decomposes **completely** into entries of a generic packaging
  vocabulary — every piece is a word that actually appears on package labels, and the concatenation
  of the pieces is exactly the original run. Nothing is invented, inserted or deleted;
* digit/letter transitions in an all-letter run get a separator ('WITH18' → 'WITH 18');
* a short abbreviation immediately followed by a capital keeps its dot and gains a space
  ('Mfd.By' → 'Mfd. By'). Case-changes that are part of a word ('AvA') are left alone;
* anything else is returned unchanged.

The raw OCR text is ALWAYS preserved separately (``Candidate.raw_value``), so what was read on the
image stays auditable next to the repaired display value — the repair is recorded in the
extraction reason.
"""
from __future__ import annotations

import re

#: Generic packaging vocabulary. Only a COMPLETE decomposition of a fused run into these words is
#: accepted, which is what keeps this from inventing gaps inside genuine words.
_VOCAB = frozenset(
    """with without and an for the of all free net wt weight volume quantity qty mrp price retail sale
    incl inclusive taxes maximum unit address refer please first character batch no number
    manufactured manufacturer mfd mfg mfgd packed packer pkd marketed marketer imported importer
    industries industry ltd limited pvt private company companies products product foods
    enterprises corporation traders agency distributors manufactures
    herbs herbal extracts extract oil oils soap medicine proprietary ayurvedic toilet toiletry
    care customer consumer service toll phone mobile email website www
    best before use expiry date drug drugs licensed license licence users tm made india
    store keep away mix pack composition contents packsize solid bis isi gr grade quality
    sulphate sodium silicate colour colours color water milk juice tea coffee salt sugar flour
    rice atta masala spices biscuit biscuits cookies chocolate cream powder hair skin body face
    hand wash chips noodles ghee butter cheese honey jam sauce paste detergent cleaner fresh pure
    natural premium bottle pouch sachet tub jar box carton contains added preservatives flavour
    flavours artificial vitamin vitamins protein energy calories serving approx gram grams
    millilitre millilitres litre litres kg g mg ml""".split()
)

#: A leading abbreviation ('Mfd.', 'Pkd.', 'Mfg.') fused to the next capitalized word.
_ABBREV_RE = re.compile(r"^([A-Z][a-z]{1,4}\.)(?=[A-Z])")
_TRAILING_PUNCT = ".,;:'\"()[]"


def _decompose(run: str) -> list[str] | None:
    """Split an all-letter uppercase run into vocabulary words, or return None.

    Complete coverage only: every character must end up inside a vocabulary word of two or more
    letters, so a genuine single word (or a run containing any non-vocabulary word) is refused.
    """
    if run.lower() in _VOCAB:
        return None  # already one real word — nothing to split
    n = len(run)
    memo: list[list[str] | None] = [None] * (n + 1)
    memo[0] = []
    for i in range(n):
        if memo[i] is None:
            continue
        for j in range(i + 2, n + 1):
            if memo[j] is not None:
                continue
            piece = run[i:j]
            if piece.lower() in _VOCAB:
                # keep the ORIGINAL case: the repair only inserts separators
                memo[j] = memo[i] + [piece]
    return memo[n]


def _repair_piece(piece: str) -> list[str]:
    lead = len(piece) - len(piece.lstrip(_TRAILING_PUNCT))
    tail = len(piece) - len(piece.rstrip(_TRAILING_PUNCT))
    head = piece[:lead]
    core = piece[lead : len(piece) - tail] if tail else piece[lead:]
    suffix = piece[len(piece) - tail :] if tail else ""
    if not core:
        return [piece]
    # a letter run fused with digits: 'WITH18AROMAS' -> 'WITH 18 AROMAS'
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", core)
    spaced = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", spaced)
    parts: list[str] = []
    for chunk in spaced.split():
        if len(chunk) >= 7 and chunk.isalpha() and chunk.isupper():
            pieces = _decompose(chunk)
            if pieces:
                parts.extend(pieces)
                continue
        parts.append(chunk)
    return [head + " ".join(parts) + suffix]


def repair_ocr_spacing(text: str | None) -> str:
    """Restore lost word boundaries in a display value; returns the input unchanged when unsure."""
    if not text:
        return text or ""
    out: list[str] = []
    for token in str(text).split():
        fixed = token
        # 'Mfd.By' -> 'Mfd. By' (an abbreviation's dot followed by a new capitalized word)
        fixed = _ABBREV_RE.sub(r"\1 ", fixed)
        out.extend(_repair_piece(piece) for piece in fixed.split())
    return " ".join(" ".join(group) for group in out)
