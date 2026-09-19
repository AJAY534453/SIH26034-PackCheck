"""Vision extraction → field candidates.

The model's job is PERCEPTION: read what is printed on the package face(s). This module

1. builds a strict prompt (report only what is visible, null when unsure, never invent),
2. calls the configured provider,
3. validates every returned observation against its own schema,
4. CORROBORATES each observation against the on-device OCR evidence already recognised for
   the same image, and
5. emits ordinary `Candidate` objects — the same currency the deterministic extractor uses.

Because the output is a candidate, the existing pipeline keeps all the guarantees it already
had: ranking, cross-image fusion, conflict detection, normalization, validators and the rule
engine. A vision reading can never bypass them, and it can never become a legal decision.

Confidence policy (the honest part):
- corroborated by OCR          → the model's own confidence is kept (capped at 0.97).
- NOT corroborated by OCR      → confidence is capped at 0.62 and the candidate is marked
  `inferred`, which the pipeline records as UNCERTAIN with an explicit reason. The value is
  still offered to the inspector (that is the point of vision — reading stylised print OCR
  misses), but it is never published as a detected fact on the model's word alone.
"""
from __future__ import annotations

import difflib
import json
import re
from typing import Iterable

from backend.ai.provider import provider_status, run_vision_json
from backend.ai.schema import (
    AI_EXTRA_FIELDS,
    AI_FIELD_NAMES,
    AIBillExtraction,
    AIExtraction,
    AIFieldObservation,
)
from backend.config import settings
from backend.extraction.base import Candidate, squashed

LABEL_PROMPT = """You are a perception component inside a Legal Metrology (Packaged Commodities)
inspection tool. You read packaged-commodity labels and report ONLY what is visibly printed.

Absolute rules:
- Never invent, guess, complete or "correct" a value. If a field is not clearly visible, return
  its value as null with confidence 0.
- Confidence is your own reading certainty for THAT field, 0.0-1.0. Do not inflate it.
- Copy text as printed. Do not translate, summarise or explain.
- Do NOT decide compliance or cite any legal rule. Do not output any verdict.
- MRP is the printed maximum retail price. Never report a unit price, discount, or struck-through
  price as MRP.
- net_quantity is the printed declaration (e.g. "500 g", "1 kg + 100 g", "10 x 20 g",
  "28.4 g + 6.1 g EXTRA = 34.5 g"). Report the whole printed expression as one value.
- Dates: use the label's own words to pick the field. "MFD"/"Manufactured"/"Date of Mfg" →
  date_manufacturing. "PKD"/"Packed on" → date_packing. "EXP"/"Expiry"/"Use By" → date_expiry.
  "Best Before" with a calendar date → date_expiry; "Best Before 12 months" (a duration, no
  calendar date) → date_best_before. Give the date exactly as printed.
- company/entity names: manufacturer is the entity name only; its address goes in
  manufacturer_address. "Marketed by X" → marketer. "Packed by X" → packer. "Imported by X" →
  importer. Never merge an entity name with its address.
- brand is the trade mark / logo wordmark. product_name is the name of the commodity itself
  (e.g. "50-50 Classic Sweet & Salty"). A company name is NEVER the product name.
- batch_lot is the printed batch/lot code, copied exactly. Letters stay letters: a code whose
  first character looks like a letter must not be rewritten as a digit.

Image identity: the images below are numbered in the order given. Report the 1-based index of the
image a field was read from as source_image.

Return ONLY JSON with exactly this shape (use null for anything not visible):

{
  "fields": [
    {
      "field": "<one of the tracked field names>",
      "value": "<printed text, or null>",
      "confidence": 0.0,
      "source_image": 1,
      "bbox": [0.0, 0.0, 0.0, 0.0],
      "evidence_text": "<the label line you read it from, verbatim>"
    }
  ]
}

Tracked field names (use these exact strings):
{bill_fields}
bbox is the region of the value inside that image as normalised [x, y, width, height] in 0..1.
If you cannot localise it, use [0, 0, 0, 0].
"""

BILL_PROMPT = """You are a perception component inside a packaged-commodity inspection tool.
Read a shop bill / invoice / receipt and report ONLY what is visibly printed.

Absolute rules:
- Never invent a value. If something is not visible, return null with confidence 0.
- billed_price is the price actually charged. mrp_on_bill is a printed MRP only if the bill
  prints one; never copy the billed price into mrp_on_bill.
- Report line_items as a single newline-separated string of each printed line.
- Do not decide whether any price is lawful or unlawful, and do not cite any rule.

Return ONLY JSON in this shape:
{
  "fields": [
    {"field": "<store_name|bill_number|bill_date|product_name|brand|billed_price|quantity|mrp_on_bill|line_items>",
     "value": "<printed text or null>",
     "confidence": 0.0,
     "source_image": 1,
     "bbox": [0.0, 0.0, 0.0, 0.0],
     "evidence_text": "<verbatim printed line>"}
  ]
}
source_image is the 1-based index of the image the value was read from.
"""


def _strip_code_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json_object(raw: str) -> dict:
    """Parse the model's JSON. Raises ValueError when the payload is unusable."""
    text = _strip_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Recover the outermost {...} block if the model wrapped it in prose.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("Vision provider did not return JSON.")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Vision provider returned malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Vision provider returned JSON that is not an object.")
    return data


def _coerce_bbox(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if box[2] <= 0 or box[3] <= 0:
        return None
    if max(box) > 1.5:  # already in pixels or nonsense — do not guess
        return None
    return [max(0.0, min(1.0, v)) for v in box]


def _observations_from(data: dict, allowed: Iterable[str]) -> list[AIFieldObservation]:
    allowed_set = set(allowed)
    out: list[AIFieldObservation] = []
    fields = data.get("fields")
    if not isinstance(fields, list):
        return out
    for item in fields:
        if not isinstance(item, dict):
            continue
        name = str(item.get("field") or "").strip().lower()
        # Tolerate the schema's published alias for "Marketed by".
        if name == "marketed_by":
            name = "marketer"
        if name not in allowed_set:
            continue
        value = item.get("value")
        if value is None:
            continue
        value = str(value).strip()
        if not value:
            continue
        try:
            confidence = float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            source_index = int(item.get("source_image") or 0)
        except (TypeError, ValueError):
            source_index = 0
        out.append(
            AIFieldObservation(
                field=name,
                value=value,
                confidence=max(0.0, min(1.0, confidence)),
                source_image=str(source_index) if source_index > 0 else "",
                bbox=_coerce_bbox(item.get("bbox")),
                evidence_text=str(item.get("evidence_text") or "").strip(),
            )
        )
    return out


# ---------- corroboration against on-device OCR ----------


def _ocr_needle_hit(value: str, haystack_squashed: str, threshold: float = 0.82) -> float:
    """Best squashed-substring similarity of `value` anywhere in the OCR text (0..1)."""
    needle = squashed(value)
    if len(needle) < 3 or not haystack_squashed:
        return 0.0
    if needle in haystack_squashed:
        return 1.0
    # Sliding comparison over windows near the needle's length (fuzzy OCR agreement).
    best = 0.0
    size = max(3, min(len(needle), 24))
    step = max(1, size // 2)
    for start in range(0, max(1, len(haystack_squashed) - size // 2), step):
        window = haystack_squashed[start : start + size]
        if not window:
            continue
        ratio = difflib.SequenceMatcher(None, needle, window).ratio()
        if ratio > best:
            best = ratio
            if best >= 0.99:
                break
    return best


def to_candidates(
    observations: list[AIFieldObservation],
    ocr_text_by_index: dict[int, str],
    dims_by_index: dict[int, tuple[int, int]],
    image_id_by_index: dict[int, int],
    *,
    max_uncorroborated: float = 0.62,
) -> list[Candidate]:
    """Turn validated observations into pipeline candidates with explicit provenance."""
    out: list[Candidate] = []
    for obs in observations:
        idx = int(obs.source_image) if str(obs.source_image).isdigit() else 0
        haystack = ocr_text_by_index.get(idx, "") or "".join(ocr_text_by_index.values())
        similarity = _ocr_needle_hit(obs.value, haystack)
        corroborated = similarity >= 0.82
        confidence = min(0.97, obs.confidence) if corroborated else min(max_uncorroborated, obs.confidence)
        bbox: tuple[int, int, int, int] | None = None
        if obs.bbox and idx in dims_by_index:
            width, height = dims_by_index[idx]
            x, y, w, h = obs.bbox
            bbox = (
                int(round(x * width)),
                int(round(y * height)),
                int(round((x + w) * width)),
                int(round((y + h) * height)),
            )
        reason = (
            f"Vision reading (provider={obs.source_image or '-'}) corroborated by on-device OCR "
            f"(similarity {similarity:.2f})."
            if corroborated
            else "Vision reading not corroborated by on-device OCR — offered for human "
            "confirmation, not asserted as a detected fact."
        )
        out.append(
            Candidate(
                field_name=obs.field,
                value=obs.value,
                raw_value=obs.evidence_text or obs.value,
                confidence=confidence,
                score=confidence,
                engine=f"ai_vision:{_provider_label()}",
                variant="vision",
                bbox=bbox,
                source_image_id=image_id_by_index.get(idx),
                source_text=obs.evidence_text or obs.value,
                reason=reason,
                inferred=not corroborated,
            )
        )
    return out


def _provider_label() -> str:
    return f"{provider_status().provider}:{provider_status().model}"


# ---------- public entry points ----------


def extract_label_fields(images: list[tuple[bytes, str]]) -> AIExtraction:
    """Call the provider for a package label. Never raises: failures return used=False + error."""
    status = provider_status()
    if not status.enabled:
        return AIExtraction(provider=status.provider, model=status.model, used=False, error=status.reason)
    prompt = LABEL_PROMPT.replace("{bill_fields}", ", ".join(AI_FIELD_NAMES))
    try:
        raw = run_vision_json(prompt, images)
    except Exception as exc:  # provider failures must never break the inspection
        return AIExtraction(provider=status.provider, model=status.model, used=False, error=str(exc))
    try:
        data = _parse_json_object(raw)
    except ValueError as exc:
        return AIExtraction(provider=status.provider, model=status.model, used=False, error=str(exc), raw=raw)
    allowed = set(AI_FIELD_NAMES) | set(AI_EXTRA_FIELDS)
    return AIExtraction(
        observations=_observations_from(data, allowed),
        provider=status.provider,
        model=status.model,
        used=True,
        raw=raw,
    )


def extract_bill_fields(images: list[tuple[bytes, str]]) -> AIBillExtraction:
    """Call the provider for a bill/receipt. Never raises: failures return used=False + error."""
    from backend.ai.schema import BILL_FIELD_NAMES

    status = provider_status()
    if not status.enabled:
        return AIBillExtraction(provider=status.provider, model=status.model, used=False, error=status.reason)
    try:
        raw = run_vision_json(BILL_PROMPT, images)
    except Exception as exc:
        return AIBillExtraction(provider=status.provider, model=status.model, used=False, error=str(exc))
    try:
        data = _parse_json_object(raw)
    except ValueError as exc:
        return AIBillExtraction(provider=status.provider, model=status.model, used=False, error=str(exc), raw=raw)
    return AIBillExtraction(
        observations=_observations_from(data, BILL_FIELD_NAMES),
        provider=status.provider,
        model=status.model,
        used=True,
        raw=raw,
    )


def ai_field_candidates(images: list) -> tuple[list[Candidate], str]:
    """Pipeline hook: read every uploaded package image with the provider (when configured).

    `images` are InspectionImage rows. Returns (candidates, note) — `note` is an honest
    one-line status recorded on the inspection regardless of the outcome.
    """
    status = provider_status()
    if not status.enabled:
        return [], f"Vision extraction skipped: {status.reason}"

    payload: list[tuple[bytes, str]] = []
    ocr_text_by_index: dict[int, str] = {}
    dims_by_index: dict[int, tuple[int, int]] = {}
    image_id_by_index: dict[int, int] = {}
    for index, image in enumerate(images, start=1):
        path = settings.STORAGE_DIR / "originals" / image.stored_filename
        try:
            data = path.read_bytes()
        except OSError:
            continue
        payload.append((data, image.mime_type or "image/jpeg"))
        ocr_text_by_index[index] = image.ocr_text or ""
        dims_by_index[index] = (image.width or 0, image.height or 0)
        image_id_by_index[index] = image.id
    if not payload:
        return [], "Vision extraction skipped: uploaded images could not be read from storage."

    result = extract_label_fields(payload)
    if not result.used:
        return [], f"Vision extraction unavailable: {result.error}"
    candidates = to_candidates(result.observations, ocr_text_by_index, dims_by_index, image_id_by_index)
    note = (
        f"Vision extraction: provider={result.provider}:{result.model}, "
        f"{len(result.observations)} field(s) reported, {len(candidates)} candidate(s) added. "
        "Each reading is validated and ranked by the same deterministic pipeline as OCR."
    )
    return candidates, note
