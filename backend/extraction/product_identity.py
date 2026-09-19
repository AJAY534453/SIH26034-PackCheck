"""Product identity extraction: product name, brand, common/generic name.

Semantic-separation policy (no blind duplication):
- product_name: the most product-name-like line (layout + letter-ratio evidence).
- brand: an explicit "Brand: X" label wins outright; otherwise the first token of a
  MULTI-TOKEN product name is used as weak layout evidence. A single-token title is
  never split — the same value is not duplicated into brand without supporting evidence.
- common_name: only from an explicit "Generic Name:"/"Common Name:" label, or the
  descriptive tail of a "Brand: X — Y" style line. Never invented.
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
from backend.ocr.base import OcrLine

# A line directly below a role anchor ("Manufactured ... by:") is the ENTITY, not the
# product name — shared role-keyword semantics with manufacturer.py (generic layout rule,
# no product knowledge).
def _role_anchor_source() -> str:
    """The role-anchor alternation, taken from the declaration field schema (single source of truth)."""
    parts = []
    for field_id in ("manufacturer", "packer", "importer", "marketer"):
        pattern = anchor_regex(field_id)
        if pattern is not None:
            parts.append(f"(?:{pattern.pattern})")
    parts.append(r"(?:brand\s*owner)")
    return "|".join(parts)


_ROLE_ANCHOR_RE = re.compile(_role_anchor_source() + r"\s*[:.]?", re.IGNORECASE)

#: A country-of-origin line ('MADE IN INDIA') is a declaration of its own. It was previously scored
#: as a candidate PRODUCT NAME (it is short, all caps and wordy), and the brand inference then took
#: its first token — publishing 'MADE' as the brand of the pack. Derived from the same schema anchor
#: the country-of-origin extractor uses.
_COUNTRY_ORIGIN_RE = anchor_regex("country_of_origin") or re.compile(
    r"\b(?:country\s*of\s*origin|origin|made\s*in|product\s*of|produce\s*of)\b"
)

NOISE_RE = re.compile(
    r"^(?:m\.?r\.?p\.?|mrp|net|qty|wt|weight|batch|lot|fssai|exp|best|use|by|mfd|mfg|packed|manufactured|"
    r"customer|consumer|care|email|www|toll|free|phone|mobile|tel|date|prepared|marketed|imported|"
    r"ingredients|nutrition|storage|instructions|unit|price|max|retail|inclusive|all\s*taxes|₹|rs).*",
    re.IGNORECASE,
)
PARENTHETICAL_RE = re.compile(r"^[^A-Za-z0-9]*(?:\([^)]*\))[^A-Za-z0-9]*$")  # '(Inclusive of all taxes)'
ADDRESS_LIKE_RE = re.compile(
    r"\b(?:no\.?\s*\d|\d{5,6}\b|road|street|nagar|lane|sector|marg|floor|block)\b|"
    r"\b(?:pvt|private|ltd|limited|llp|industries|enterprises)\b",
    re.IGNORECASE,
)
FIELD_LABEL_RE = re.compile(
    r"\b(?:m\.?r\.?p\.?|mrp|net\s*(?:qty|wt|weight|quantity)|batch|lot|fssai|lic(?:ence|ense)\s*no|"
    r"exp(?:iry)?|best\s*before|use\s*by|mfd|mfg(?:d)?|manufactured|packed|customer\s*care|consumer\s*care|"
    r"date|email|www\.|toll\s*free|phone|mobile)\b",
    re.IGNORECASE,
)
CURRENCY_RE = re.compile(r"(?:₹|rs\.?|inr)\s*[\d.,]+", re.IGNORECASE)
BRAND_LABEL_RE = re.compile(r"\bbrand(?:\s*name)?\s*[:\-]\s*(.+)$", re.IGNORECASE)
COMMON_NAME_LABEL_RE = re.compile(r"\b(?:generic|common)\s*name\s*[:\-]\s*(.+)$", re.IGNORECASE)

GENERIC_NOUN_RE = re.compile(
    r"\b(?:biscuits?|cookies?|snacks?|coffee|tea|powder|flour|atta|rice|dal|pulses?|spices?|masala|"
    r"beverages?|juice|water|oil|shampoo|soap|paste|detergent|chips|noodles|salt|sugar|"
    r"chocolate|candy|toffee|hair\s+oil|skin\s+cream|namkeen)\b",
    re.IGNORECASE,
)

# Narrative/usage copy (recipes, usage directions, marketing sentences) is never the
# product name. Matched on discourse markers, not on any product-specific vocabulary.
NARRATIVE_RE = re.compile(
    r'\b(?:recipe|directions?|usage|instructions?|serving|suggestion|mix|take|add|stir|boil|'
    r'blend(?:ed|s)?|taste|serves?|enjoy|preparation|cook|heat|pour|store\s+in|'
    r'\ban?\b|gives?|with\s+one)\b',
    re.IGNORECASE,
)
# Usage / safety / storage copy is INSTRUCTION text, not the display title. 'FOR EXTERNAL USE
# ONLY' is a two-declaration-clear instruction line, and it was previously scored as a product
# name (short, all caps, wordy) — the brand inference then published its first token, 'FOR', as
# the brand of the pack.
INSTRUCTION_RE = re.compile(
    r"\b(?:for\s+(?:external|internal|topical|oral|best|use|application|hair|skin|men|women)\b|"
    r"external\s+use|do\s+not|avoid\s+contact|apply\s+(?:on|to|liberally)|store\s+in|"
    r"keep\s+(?:out|away|in)\b|directions?\s*(?:for|:)|how\s+to\s+use|usage\b|rinse|massage|"
    r"shake\s+well|read\s+(?:the\s+)?(?:label|instructions)|use\s+only|only\s+for)\b",
    re.IGNORECASE,
)
# A PACK COMPOSITION / contents / pack-size line is a QUANTITY declaration, not a display title:
# 'PACK COMPOSITION: 4 X 150 G + 1 X 150 G FREE' is short, all caps and wordy, so it scored as a
# product name and the brand inference then published its first token, 'PACK'.
COMPOSITION_RE = re.compile(
    r"\b(?:pack(?:ing)?\s*composition|composition|contents?|net\s*(?:wt|weight|quantity|volume|content)|"
    r"pack\s*of|combo\s*pack|\d+\s*(?:x|\u00d7)\s*\d+)\b",
    re.IGNORECASE,
)
# A RELATIONSHIP INSTRUCTION prints a link between two declarations instead of naming the product:
# 'For Mfd. Unit Address, please refer to the first character of the Batch No.' It is short, all
# caps and wordy, so it scores like a title and the brand inference then publishes a whole sentence
# as the brand. Matching is space-tolerant because OCR fuses these lines ('pleaserefertothe').
RELATIONSHIP_INSTRUCTION_RE = re.compile(
    r"(?:please)?\s*refer\s*(?:to)?\s*the|first\s*character|unit\s*address|for\s*(?:mfd|manuf)|"
    r"refer\s*to\s*(?:the\s*)?(?:batch|mfg|mfd|label|address)|as\s*per\s*(?:the\s*)?(?:batch|mfg|mfd)",
    re.IGNORECASE,
)
SENTENCE_END_RE = re.compile(r'[.!?]\s*$')
FUNCTION_WORD_RE = re.compile(
    r'\b(?:and|or|the|your|you|for|with|of|in|is|are)\b', re.IGNORECASE,
)
CONTACT_SHAPE_RE = re.compile(r'@|https?://|\bwww\.')
# A quoted line is a claim/slogan, not the display title. (Both double and typographic
# quotes; the leading quote may be fused to the first word by OCR.)
QUOTED_LINE_RE = re.compile(r'^["\'\u201c\u2018]|^[A-Za-z]["\'\u201c\u2018]')


def _score_line(text: str, line: OcrLine | None = None) -> float:
    """Heuristic importance of a line as a product-name candidate (0..1)."""
    t = text.strip()
    if not t or len(t) < 3:
        return 0.0
    if PARENTHETICAL_RE.match(t):
        return 0.0
    if t.startswith("(") or t.count("(") != t.count(")"):
        return 0.0  # a fragment of a parenthetical tax/footer line, never a display title
    if FIELD_LABEL_RE.search(t) or CURRENCY_RE.search(t):
        return 0.0
    if NOISE_RE.match(t):
        return 0.0
    if ADDRESS_LIKE_RE.search(t):
        return 0.0  # company/address lines are not the product name
    if _COUNTRY_ORIGIN_RE.search(t):
        return 0.0  # a country-of-origin declaration is never the product title
    if looks_like_company_entity(t):
        # The company/legal-entity line (manufacturer, packer, marketer, importer) is a
        # declaration of its own, not the product title. Fused OCR ('ACME INDUSTRIESLTD.')
        # is exactly when a word-boundary test fails and the entity would otherwise win the
        # product-name contest.
        return 0.0
    if NARRATIVE_RE.search(t):
        return 0.0  # usage/recipe/narrative sentences are never the product name
    if INSTRUCTION_RE.search(t):
        return 0.0  # safety/usage instructions are never the product name
    if RELATIONSHIP_INSTRUCTION_RE.search(t):
        # A printed instruction that links one declaration to another is not a title, and its
        # first token is never a brand.
        return 0.0
    if COMPOSITION_RE.search(t):
        return 0.0  # a pack-composition / contents / pack-size line is a quantity declaration
    if SENTENCE_END_RE.search(t) and len(t.split()) > 5:
        return 0.0  # a title does not end like a sentence with many words
    if len(FUNCTION_WORD_RE.findall(t)) >= 2 and len(t.split()) > 4:
        return 0.0  # stopword-dense lines are narrative copy, not display titles
    if CONTACT_SHAPE_RE.search(t):
        return 0.0  # email/web/contact lines are never display titles
    if QUOTED_LINE_RE.match(t):
        return 0.0  # a quoted line is a claim/slogan, not the display title
    if line is not None and line.confidence < 0.6:
        # A title is the most prominent print on a panel and is read cleanly when the photo is
        # usable at all. A line whose glyphs were read below 60% confidence is unverified text
        # (typically a thresholded variant of a mangled address block) — publishing it as the
        # product name would be asserting a reading the evidence does not support.
        return 0.0
    if t.count(",") >= 2:
        return 0.0
    letters = sum(ch.isalpha() for ch in t)
    digits = sum(ch.isdigit() for ch in t)
    # A DIGIT-DOMINATED line is a code/date/phone, not a product name. Digits inside an otherwise
    # wordy title are normal though ('50-50 Classic Sweet & Salty', '7 Up', '100% Juice'), so the
    # test is dominance, not mere presence.
    if digits >= 3 and digits / max(1, letters + digits) > 0.4:
        return 0.0
    score = 0.0
    if letters >= 4:
        score += 0.4
    if letters / max(1, len(t.replace(" ", ""))) > 0.6:
        score += 0.2
    if 2 <= len(t.split()) <= 6:
        score += 0.2
    elif len(t.split()) > 8:
        score -= 0.25  # long wordy lines are narrative copy, not a title
    if t.isupper() and len(t) >= 6:
        score += 0.15  # product names are often uppercase on labels
    if len(t.split()) == 1:
        # A lone short uppercase word is the shape of a brand wordmark/logo, which on most
        # labels is printed LARGER than the product title. Without this correction the biggest
        # wordmark wins the product-name contest and the actual title is discarded.
        score -= 0.25
    # larger text (taller bbox) is much more likely the product name — the title is
    # typically the largest print on the panel, so weight height progressively
    try:
        height = max(1, line.bbox[3] - line.bbox[1])
        if height >= 90:
            score += 0.45
        elif height >= 40:
            score += 0.25
        elif height >= 28:
            score += 0.1
    except Exception:
        pass
    # Clamp to 1.0 (not 0.95) so that differences between two strong candidates survive and the
    # winner is decided by evidence rather than by line order after a cap collision.
    return max(0.0, min(1.0, score))


#: Marketing BURST words are printed large on the pack ('FREE!', 'SUPERSAVER', 'COMBO PACK') and
#: have the typographic shape of a wordmark, but they are an offer — never the brand. Rejecting
#: them from the MARK reading keeps an offer word from being published as the brand of the pack.
PROMO_TOKEN_RE = re.compile(
    r"^(?:free|super\s*saver|supersaver|combo|offer|off|save|saving|savings|buy|get|gift|extra|"
    r"bonus|win|trial|sample|refill|pack|new|now|win)+!*$",
    re.IGNORECASE,
)

BRAND_MARK_NOISE_RE = re.compile(
    r"\b(?:recipe|directions?|usage|ingredients?|nutrition|storage|serve|best\s*before|"
    r"net\s*(?:wt|weight|qty|quantity)|mrp|batch|fssai|date|www|email)\b",
    re.IGNORECASE,
)


def _wordmark_shaped(text: str) -> bool:
    """Does this printed line have the shape of a brand wordmark/masthead?

    Single word, short, predominantly uppercase, and not a label/address/commodity word. This is
    the ONE definition of 'wordmark shape' in this module — the logo-band reader and the
    stacked-title masthead check both consult it, so the two cannot drift apart.
    """
    token = (text or "").strip().strip(" .,;:-!|\"'()[]")
    if not token or len(token) > 24 or len(token.split()) != 1:
        return False
    letters = sum(ch.isalpha() for ch in token)
    if letters < 4:
        return False
    if sum(ch.isupper() for ch in token) / max(1, letters) < 0.6:
        return False
    if FIELD_LABEL_RE.search(token) or CURRENCY_RE.search(token) or CONTACT_SHAPE_RE.search(token):
        return False
    if BRAND_MARK_NOISE_RE.search(token) or ADDRESS_LIKE_RE.search(token) or GENERIC_NOUN_RE.search(token):
        return False
    if PROMO_TOKEN_RE.match(token):
        return False  # an offer burst is not a brand wordmark
    return True


def _masthead_line(lines: list[OcrLine], fl: list[bool], token: str) -> bool:
    """True when ``token`` is printed ON ITS OWN as a masthead above the product words.

    Used for a stacked title block ('MEDIMIX' over 'AYURVEDIC SOAP'): the block is the product
    title, but its first line was set as a masthead — which is the same layout evidence a separate
    logo mark provides.

    Typographic test: the token's line must be materially TALLER than the line of product words
    printed directly beneath it on the same column (>= 1.25x). A multi-line title whose words are
    all set at one size ('CLASSIC' / 'SWEET' / '&SALTY') therefore does NOT qualify — its first
    word is a title word, not a masthead — while a wordmark set above smaller product words does.
    """
    for idx, line in enumerate(lines):
        if idx < len(fl) and fl[idx]:
            continue
        if line.text.strip().strip(" .,;:-!|\"'()[]") != token or not _wordmark_shaped(line.text):
            continue
        height = max(1, line.bbox[3] - line.bbox[1])
        below = []
        for j, other in enumerate(lines):
            if j == idx or (j < len(fl) and fl[j]) or not other.text.strip():
                continue
            if other.bbox[1] < line.bbox[3] - 2:
                continue
            if other.bbox[1] - line.bbox[3] > 2 * height:
                continue
            overlap = min(line.bbox[2], other.bbox[2]) - max(line.bbox[0], other.bbox[0])
            if overlap <= 0.2 * max(1, line.bbox[2] - line.bbox[0]):
                continue
            below.append(other)
        if not below:
            continue
        nxt = min(below, key=lambda l: l.bbox[1])
        next_height = max(1, nxt.bbox[3] - nxt.bbox[1])
        if height >= 1.25 * next_height:
            return True
    return False


#: A word to look for inside a printed line: letters first, then alphanumerics and the
#: punctuation an OCR run keeps inside a word; '@'/'.' then separate the trailing markers that
#: sit fused to a wordmark ('MEDIMIX@75' → 'MEDIMIX', '75').
_WORD_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9&.']*")


def _alnum_upper(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text or "").upper()


#: How much longer than the truncated reading a corroborating sighting may be. A wordmark that
#: gains one tail character ('MEDIMI' → 'MEDIMIX') is a completed reading; a sighting with a long
#: extra run is a different (fused) word and must not be pasted onto the wordmark.
_MAX_COMPLETION_TAIL = 3


def corroborated_wordmark_completion(
    value: str,
    lines_by_image: dict[int, list[OcrLine]],
    *,
    min_confidence: float = 0.6,
) -> dict | None:
    """Complete a TRUNCATED wordmark reading from an independently printed occurrence.

    The recogniser truncates words at the tail of a rotated/logo band (the photographed pack's
    wordmark is returned as ``MEDIMI`` while the same wordmark is printed in full elsewhere on the
    pack: ``MEDIMIX@75`` on the side panel). The tail is therefore not guessed — it is taken from a
    second, independent sighting of the SAME word inside the same evidence set, and the sighting
    that supplied it is recorded on the candidate.

    The offer is made only when it cannot invent anything:

    * the candidate is a single word of at least 4 characters;
    * a token in the corpus starts with that word and is 1..3 characters longer;
    * every such sighting agrees on the SAME completion (a tail that differs between sightings is
      a misreading, not a wordmark);
    * the sighting was read with at least ``min_confidence``.

    Returns ``{"value", "line", "image_id", "confidence"}`` or ``None`` when the reading cannot be
    completed without guessing.
    """
    core = _alnum_upper(value)
    if len(core) < 4 or len((value or "").split()) != 1:
        return None  # a multi-word entity or a very short token is never 'completed'
    proposals: list[tuple[str, OcrLine, int]] = []
    for image_id, lines in (lines_by_image or {}).items():
        for line in lines:
            if line.confidence < min_confidence:
                continue
            for token in _WORD_TOKEN_RE.findall(line.text or ""):
                norm = _alnum_upper(token)
                if norm == core:
                    continue  # the same reading is not corroboration
                if norm.startswith(core) and 1 <= len(norm) - len(core) <= _MAX_COMPLETION_TAIL:
                    proposals.append((norm, line, image_id))
    if not proposals:
        return None
    if len({norm for norm, _, _ in proposals}) != 1:
        return None  # sightings disagree on the tail: do not choose one
    norm, line, image_id = max(proposals, key=lambda item: item[1].confidence)
    return {
        "value": norm,
        "line": line,
        "image_id": image_id,
        "confidence": line.confidence,
    }


def cross_panel_brand_candidates(lines_by_image: dict[int, list[OcrLine]]) -> list[Candidate]:
    """Brand marks corroborated by a printed declaration on ANOTHER panel of the same package.

    Multi-image evidence fusion for the ONE field where a single photograph is genuinely weaker
    than the set: a wordmark read on the front panel is only layout evidence, but if the same token
    also occurs inside a printed declaration on another panel (typically the manufacturer entity,
    'BRITANNIA INDUSTRIESLTD.' on the side panel), two independent panels agree on it.

    Such a candidate carries a small score bonus over a mark read from layout alone, so it wins
    against a competing title word that merely happens to be set large. It stays ``inferred`` — it
    is offered for inspector confirmation, never presented as a labelled declaration.
    """
    panels = {
        image_id: [line.text for line in lines if line.text.strip()]
        for image_id, lines in (lines_by_image or {}).items()
    }
    out: list[Candidate] = []
    for image_id, lines in (lines_by_image or {}).items():
        others = " ".join(
            text for other_id, texts in panels.items() if other_id != image_id for text in texts
        )
        if not others:
            continue
        for line in lines:
            token = line.text.strip().strip(" .,;:-!|\"'()[]")
            if not _wordmark_shaped(line.text):
                continue
            # The occurrence must START a word; a fused continuation still counts (see
            # _brand_mark_candidates for the same rule and the cases it comes from).
            if not re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}", others, re.IGNORECASE):
                continue
            conf = min(0.7, compose_confidence(line.confidence, 0.8, 0.7))
            out.append(
                Candidate(
                    field_name="brand",
                    value=token,
                    raw_value=line.text.strip(),
                    confidence=round(conf, 3),
                    # +0.08: two panels agreeing is stronger than one panel's layout alone.
                    score=round(conf + 0.08, 3),
                    engine=line.engine,
                    variant=line.variant,
                    bbox=line.bbox,
                    source_text=line.text.strip(),
                    reason=(
                        "wordmark-shaped line whose token also occurs in a printed declaration on "
                        "another panel of the same package (multi-image corroboration; layout "
                        "evidence only, offered for confirmation)"
                    ),
                    inferred=True,
                )
            )
    return out


def _brand_mark_candidates(
    lines: list[OcrLine], fl: list[bool], exclude_value: str
) -> list[Candidate]:
    """Brand marks (logos) read as short, prominent, uppercase text in the label's top band AND
    corroborated by repetition elsewhere on the label.

    A logo is typography, not a labelled declaration, so this is layout evidence only: the value
    is marked `inferred` and is never presented as a detected fact. Corroboration (the same token
    recurring in another declaration, typically the manufacturer/packer entity) is REQUIRED —
    without it an arbitrary prominent word would be promoted to 'brand', which would be guessing.
    """
    good = [l for i, l in enumerate(lines) if not fl[i] and l.text.strip()]
    if len(good) < 3:
        return []
    ys = [l.bbox[1] for l in good] + [l.bbox[3] for l in good]
    top, bottom = min(ys), max(ys)
    extent = max(1, bottom - top)
    band = top + 0.45 * extent  # upper masthead/logo area of the label
    texts = [l.text.lower() for l in good]
    out: list[Candidate] = []
    excluded = (exclude_value or "").lower()

    for idx, line in enumerate(good):
        raw = line.text.strip()
        token = raw.strip(" .,;:-!|\"'()[]")
        if not token or len(token) > 24:
            continue
        tokens = token.split()
        if not 1 <= len(tokens) <= 3:
            continue
        letters = sum(ch.isalpha() for ch in token)
        if letters < 4:
            continue
        upper = sum(ch.isupper() for ch in token)
        if upper / max(1, letters) < 0.6:
            continue
        if FIELD_LABEL_RE.search(raw) or CURRENCY_RE.search(raw) or CONTACT_SHAPE_RE.search(raw):
            continue
        if BRAND_MARK_NOISE_RE.search(raw) or ADDRESS_LIKE_RE.search(raw):
            continue
        if GENERIC_NOUN_RE.search(raw):
            continue  # a commodity/category word ('BISCUIT', 'BEVERAGES') is not a brand mark
        if PROMO_TOKEN_RE.match(token):
            continue  # an offer burst ('FREE!', 'SUPERSAVER') is not a brand wordmark
        # The detected product name is never ALSO the brand: a string must not be published as both.
        # The veto is therefore EQUALITY against the whole detected title, not containment. A label
        # that prints its wordmark directly above the product words ('MEDIMIX' over 'AYURVEDIC
        # SOAP') merges those lines into one title, so the brand token is contained in that title —
        # and a containment test discarded exactly the legitimate wordmark, leaving the pack with no
        # brand at all. Repetition elsewhere (enforced below) is what keeps an arbitrary title word
        # from being promoted here.
        if excluded and token.lower() == excluded:
            continue
        yc = (line.bbox[1] + line.bbox[3]) / 2
        if not (top <= yc <= band):
            continue
        # Required corroboration: the token recurs in another line (repeated occurrence). The
        # recurrence must START a word — a token buried inside a longer word is NOT an occurrence
        # (that was letting a declaration on the back panel corroborate a title word read large on
        # the front, publishing a product word as the brand), while a FUSED suffix still counts,
        # because there the print is the same word with its neighbour run into it.
        core = token.lower()
        core_re = re.compile(rf"(?<![a-z0-9]){re.escape(core)}")
        repeats = sum(1 for j, t in enumerate(texts) if j != good.index(line) and core_re.search(t))
        if repeats < 1:
            continue
        # a short caption directly beneath strengthens the logo reading (tagline row)
        tagline = any(
            other is not line
            and other.bbox[1] >= line.bbox[3] - 4
            and other.bbox[1] - line.bbox[3] <= max(1.5 * (line.bbox[3] - line.bbox[1]), 30)
            and 1 <= len(other.text.split()) <= 5
            for other in good
        )
        conf = compose_confidence(line.confidence, 0.55 + (0.1 if tagline else 0.0), 0.45)
        conf = min(0.6, conf)
        out.append(
            Candidate(
                field_name="brand",
                value=token,
                raw_value=raw,
                confidence=round(conf, 3),
                score=round(conf, 3),
                engine=line.engine,
                variant=line.variant,
                bbox=line.bbox,
                source_text=raw,
                reason=(
                    "prominent uppercase text in the label's top/logo band, recurring elsewhere "
                    "on the label" + (" with a caption beneath" if tagline else "")
                    + " — layout/typography evidence only, not a labelled 'Brand' declaration"
                ),
                inferred=True,
            )
        )
    return out


_FRAGMENT_MAX_CHARS = 30
_FRAGMENT_MAX_WORDS = 4


def _is_title_fragment(text: str) -> bool:
    """Could this short line be one line OF a stacked display title?"""
    t = (text or "").strip()
    if not t or len(t) > _FRAGMENT_MAX_CHARS:
        return False
    if PARENTHETICAL_RE.match(t) or FIELD_LABEL_RE.search(t) or CURRENCY_RE.search(t):
        return False
    if NOISE_RE.match(t) or ADDRESS_LIKE_RE.search(t) or CONTACT_SHAPE_RE.search(t):
        return False
    if looks_like_company_entity(t) or NARRATIVE_RE.search(t) or QUOTED_LINE_RE.match(t):
        return False
    if not any(ch.isalpha() for ch in t):
        return False
    return len(t.split()) <= _FRAGMENT_MAX_WORDS


def _title_block_lines(lines: list[OcrLine], fl: list[bool]) -> list[OcrLine]:
    """Vertically adjacent title lines merged into one display title.

    Display titles are routinely printed on two or three stacked lines ('CLASSIC' / 'SWEET &
    SALTY'). Read line-by-line each fragment is too weak to win the product-name contest, so the
    panel's largest SINGLE word — or a neighbouring declaration — can win instead. Lines are
    merged only when they are genuinely one block: same column, tight vertical gap, every line
    title-shaped and none of them a label/entity/narrative line. Nothing is invented — the value
    is the printed lines joined in reading order.
    """
    out: list[OcrLine] = []
    n = len(lines)
    i = 0
    while i < n:
        if fl[i] or not _is_title_fragment(lines[i].text):
            i += 1
            continue
        group = [i]
        j = i + 1
        skips = 0
        while j < n and len(group) < 4 and skips < 4:
            prev, cur = lines[group[-1]], lines[j]
            h = max(1, prev.bbox[3] - prev.bbox[1])
            if cur.bbox[1] > prev.bbox[3] + max(1.2 * h, 30):
                break  # past the block: the next stacked line would be a different block
            gap = cur.bbox[1] - prev.bbox[3]
            overlap = min(prev.bbox[2], cur.bbox[2]) - max(prev.bbox[0], cur.bbox[0])
            wider = max(prev.bbox[2] - prev.bbox[0], cur.bbox[2] - cur.bbox[0])
            same_column = (
                abs(cur.bbox[0] - prev.bbox[0]) <= max(0.6 * h, 14)
                or overlap >= 0.75 * wider
            )
            if fl[j] or not _is_title_fragment(cur.text) or not same_column:
                # A line beside the title (a promo tag, a badge, a nutrition column) does not end
                # the block — it is simply not part of it. Only a real gap ends it.
                j += 1
                skips += 1
                continue
            if gap > max(0.8 * h, 18) or gap < -0.3 * h:
                break
            group.append(j)
            skips = 0
            j += 1
        if len(group) >= 2:
            out.append(
                OcrLine(
                    text=" ".join(lines[k].text.strip() for k in group),
                    confidence=min(lines[k].confidence for k in group),
                    bbox=(
                        min(lines[k].bbox[0] for k in group),
                        min(lines[k].bbox[1] for k in group),
                        max(lines[k].bbox[2] for k in group),
                        max(lines[k].bbox[3] for k in group),
                    ),
                    engine=lines[group[0]].engine,
                    variant=lines[group[0]].variant,
                )
            )
            i = j
        else:
            i += 1
    return out


def extract_product_identity(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    """Extract product_name, brand and common_name with evidence-based separation."""
    lines = lines_sorted(lines)
    fl = flags if flags is not None else [False] * len(lines)
    out: list[Candidate] = []

    # ---- explicit brand / common-name labels first (strong evidence) ----
    labeled_brand: Candidate | None = None
    for idx, line in enumerate(lines):
        text = line.text.strip()
        if not text or fl[idx]:
            continue
        bm = BRAND_LABEL_RE.search(text)
        if bm:
            value = bm.group(1).strip(" .,;-")
            if value:
                conf = compose_confidence(line.confidence, 0.95, 0.9)
                labeled_brand = Candidate(
                    field_name="brand",
                    value=value,
                    raw_value=text,
                    confidence=round(conf, 3),
                    score=conf + 0.2,
                    engine=line.engine,
                    variant=line.variant,
                    bbox=line.bbox,
                    source_text=text,
                    reason="explicit 'Brand:' label on package",
                )
                out.append(labeled_brand)
        cm = COMMON_NAME_LABEL_RE.search(text)
        if cm:
            value = cm.group(1).strip(" .,;-")
            if value:
                conf = compose_confidence(line.confidence, 0.9, 0.85)
                out.append(
                    Candidate(
                        field_name="common_name",
                        value=value,
                        raw_value=text,
                        confidence=round(conf, 3),
                        score=conf + 0.1,
                        engine=line.engine,
                        variant=line.variant,
                        bbox=line.bbox,
                        source_text=text,
                        reason="explicit generic/common name label on package",
                    )
                )

    # ---- product name: most product-name-like line ----
    # This is a LAYOUT question ('which line looks like the display title?'), so the layout score
    # decides and OCR quality only breaks near-ties. Ranking on composed confidence instead let a
    # crisply-printed wordmark beat the actual title purely because its OCR confidence was higher.
    best: Candidate | None = None
    best_rank = -1.0
    best_is_block = False
    for idx, line in enumerate(lines):
        text = line.text.strip()
        if not text or fl[idx]:
            continue
        if is_nutrition_line(lines, idx, fl):
            continue
        if labeled_brand and text == labeled_brand.raw_value:
            continue  # the brand-label line itself is not the product name
        # Layout semantics: a name-like line sitting directly below a role anchor is the
        # manufacturer/packer/marketer ENTITY (mis-classifying it as product name conflates
        # two legally distinct declarations). The lookback walks back over noise lines
        # (short fragments, duplicate OCR variants of the same physical line) until the
        # first substantive line and lets THAT decide; the anchor must also be vertically
        # adjacent (within ~2.5 line heights) to count as 'the line above'.
        under_role_anchor = False
        for back in range(1, 9):
            if idx - back < 0:
                break
            prev = lines[idx - back]
            prev_text = prev.text.strip()
            if not prev_text or len(prev_text) <= 8 or prev.bbox == line.bbox:
                continue
            under_role_anchor = bool(_ROLE_ANCHOR_RE.search(prev_text))
            if under_role_anchor:
                h_prev = max(1, prev.bbox[3] - prev.bbox[1])
                h_line = max(1, line.bbox[3] - line.bbox[1])
                gap = (line.bbox[1] + line.bbox[3]) / 2 - (prev.bbox[1] + prev.bbox[3]) / 2
                under_role_anchor = gap <= 2.5 * max(h_prev, h_line)
            break
        if under_role_anchor:
            continue
        s = _score_line(text, line)
        if s <= 0.5:
            continue
        conf = compose_confidence(line.confidence, s, 0.6)
        rank = s + 0.15 * conf
        if rank > best_rank:
            best = Candidate(
                field_name="product_name",
                value=text,
                raw_value=text,
                confidence=round(conf, 3),
                # The LAYOUT rank decides which line is the title, and the layout rank is what
                # must therefore carry into cross-image winner selection: scoring this field on
                # OCR confidence alone lets a crisply-read wordmark on another panel outrank the
                # title chosen here.
                score=round(rank, 3),
                engine=line.engine,
                variant=line.variant,
                bbox=line.bbox,
                source_text=text,
                reason="most product-name-like line by length, letter ratio and label context",
            )
            best_rank = rank
            best_is_block = False

    # ---- stacked display titles: the printed title split across adjacent lines ----
    for merged in _title_block_lines(lines, fl):
        text = merged.text.strip()
        s = _score_line(text, merged)
        if s <= 0.5:
            continue
        conf = compose_confidence(merged.confidence, s, 0.6)
        rank = s + 0.15 * conf
        if rank > best_rank:
            best = Candidate(
                field_name="product_name",
                value=text,
                raw_value=text,
                confidence=round(conf, 3),
                score=round(rank, 3),
                engine=merged.engine,
                variant=merged.variant,
                bbox=merged.bbox,
                source_text=text,
                reason=(
                    "stacked display-title lines read as one block (same column, adjacent rows) "
                    "— the panel's largest print"
                ),
            )
            best_rank = rank
            best_is_block = True
    if best is not None:
        out.append(best)

    # ---- brand: explicit label > logo/masthead mark > first token of a multi-token title ----
    if labeled_brand is None and best is not None:
        marks = _brand_mark_candidates(lines, fl, best.value)
        if marks:
            out.extend(marks)
        else:
            tokens = best.value.split()
            # A single-token fused title (brand+product with no space) is NOT split into a brand —
            # duplicating it without evidence manufactures a semantic interpretation.
            brand_value = tokens[0] if len(tokens) >= 2 else ""
            brand_reason = ""
            if brand_value and not best_is_block:
                brand_reason = "first token of the detected multi-token product name (layout evidence)"
            elif brand_value and _masthead_line(lines, fl, brand_value):
                # A merged STACKED title is not split on its own account — but when the block's
                # first line is itself printed as a one-word masthead above the product words
                # ('MEDIMIX' over 'AYURVEDIC SOAP'), that line is the same layout evidence a
                # separate logo mark would be. Without this, a pack whose wordmark sits directly
                # on top of its product words ends up with no brand at all.
                brand_reason = (
                    "the first line of the stacked title block is printed as its own one-word "
                    "masthead above the product words (layout evidence)"
                )
            if brand_reason:
                # PRECEDENCE (the module's own order): an explicit 'Brand:' label, then a wordmark
                # read as a mark, then this title-split fallback. The fallback is therefore capped
                # BELOW the mark-reading band: a panel whose title contains the product words is
                # weak evidence, and it must not outrank a wordmark corroborated on another panel
                # of the same package (which previously published a title word as the brand of a
                # pack whose wordmark had been read cleanly).
                conf = round(min(0.55, max(0.2, best.confidence - 0.2)), 3)
                out.append(
                    Candidate(
                        field_name="brand",
                        value=brand_value,
                        raw_value=best.raw_value,
                        confidence=conf,
                        score=conf,
                        engine=best.engine,
                        variant=best.variant,
                        bbox=best.bbox,
                        source_text=best.source_text,
                        reason=brand_reason,
                        inferred=True,
                    )
                )

    # ---- common-name fallback: descriptive tail of a multi-token title containing a generic noun ----
    has_common = any(c.field_name == "common_name" for c in out)
    if not has_common and best is not None:
        tokens = best.value.split()
        if len(tokens) >= 2 and GENERIC_NOUN_RE.search(best.value):
            tail = " ".join(tokens[-2:])
            conf = round(max(0.2, best.confidence - 0.15), 3)
            out.append(
                Candidate(
                    field_name="common_name",
                    value=tail,
                    raw_value=best.raw_value,
                    confidence=conf,
                    score=conf - 0.05,
                    engine=best.engine,
                    variant=best.variant,                        bbox=best.bbox,
                        source_text=best.source_text,
                        reason="descriptive tail of the product title containing a generic commodity noun",
                        inferred=True,
                    )
                )

    return out
