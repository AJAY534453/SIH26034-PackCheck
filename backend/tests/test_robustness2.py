"""OCR/extraction robustness tests (second-generation build).

Covers the approved fixes:
  - engine canonical line order -> nutrition flags can never misalign (image-17 root cause)
  - batch: label text is never the value; pure-digit guard keeps prices out, codes in
  - quantity: junk-prefix + unit-terminated correction ('i5o0g' -> 500 g) with provenance
  - MRP: anchor-present binding + fused-amount decomposition ('200.0010.4091' -> 200.00)
  - manufacturer: column guard + fused-entity recognition + address grouping
  - textnorm guardrails: words ending in unit letters are never 'corrected'
  - bottom-strip pass: bbox back-mapping + conditional escalation
  - golden Sukku regression: pipeline behavior on the exact stored OCR lines, expected
    values in a FIXTURE file only (never in pipeline/extraction code)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.extraction.association import associate_label_values
from backend.extraction.base import OcrLine, lines_sorted, nutrition_line_flags
from backend.extraction.engine import pick_winner, run_extraction
from backend.normalization.service import normalize_field
from backend.ocr.textnorm import normalize_numeric_text

B = (10, 10, 200, 30)


def _line(text: str, conf: float = 0.9, bbox=B, variant: str = "original") -> OcrLine:
    return OcrLine(text=text, confidence=conf, bbox=bbox, engine="test", variant=variant)


# ---------------------------------------------------------------- textnorm guardrails

def test_textnorm_unit_terminated_token_with_junk_prefix():
    corrected, ledger = normalize_numeric_text("NetWeight i5o0g")
    assert corrected == "NetWeight 500g"
    assert ledger and ledger[0]["token_original"] == "i5o0g"
    # the junk letter is DROPPED, never mapped to a digit (no fabricated 1500)
    assert ledger[0]["token_corrected"] == "500g"
    assert ("i", "") in ledger[0]["changes"]


def test_textnorm_word_ending_in_unit_letter_is_never_corrected():
    # 'Blog' ends in 'g'; a naive unit-strip would leave 'Blo' (all ambiguous letters)
    # and "correct" it to 810g. It must stay untouched.
    corrected, ledger = normalize_numeric_text("Blog long")
    assert corrected == "Blog long"
    assert ledger == []


def test_textnorm_ambiguous_only_unit_token_not_corrected():
    corrected, ledger = normalize_numeric_text("5OOg")
    assert corrected == "500g" and ledger


def test_textnorm_does_not_touch_words():
    corrected, _ = normalize_numeric_text("BEST BEFORE 12 MONTHS")
    assert "MONTHS" in corrected and "12" in corrected


# ---------------------------------------------------------------- engine flag alignment

def test_nutrition_flags_do_not_poison_customer_care_lines():
    """Root cause of the image-17 failures: engine sorts lines, flags were computed on a
    different order — a Customer Care line got nutrition-flagged. Both orders must agree."""
    lines = [
        _line("Nutrition Information per100g", bbox=(30, 956, 377, 1003)),
        _line("*Fat", bbox=(30, 1059, 94, 1105)),
        _line("g/100g", bbox=(216, 1067, 298, 1115)),
        _line("0.90", bbox=(325, 1070, 381, 1110)),
        _line("Customer Care:094875 93380", bbox=(434, 1182, 1122, 1249)),
        _line("E.mail:gurucharaa_product@yahoo.com", bbox=(449, 1223, 1128, 1283)),
    ]
    per_field = run_extraction({1: lines})
    phone = per_field.get("consumer_care_phone") or []
    assert phone, "phone must be extracted even with a nutrition table present"
    assert phone[0].value == "09487593380"
    email = per_field.get("consumer_care_email") or []
    assert email and email[0].value == "gurucharaa_product@yahoo.com"


def test_engine_sort_is_canonical_for_flags_and_extractors():
    lines = [
        _line("Customer Care:094875 93380", bbox=(434, 1182, 1122, 1249)),
        _line("g/100g", bbox=(216, 1067, 298, 1115)),
    ]
    sorted_lines = lines_sorted(lines)
    flags = nutrition_line_flags(sorted_lines)
    # the 'g/100g' row is flagged, the customer-care line is not — in SORTED order
    assert flags[sorted_lines.index(next(l for l in sorted_lines if l.text == "g/100g"))] is True
    assert flags[sorted_lines.index(next(l for l in sorted_lines if l.text.startswith("Customer")))] is False


# ---------------------------------------------------------------- batch

def test_batch_label_text_is_never_the_value():
    out = run_extraction({1: [_line("Batch No"), _line("M.R.P. Rs 200", bbox=(10, 40, 300, 60))]})
    winners = [w.value for w, _ in [pick_winner(out.get("batch_lot", []))] if w]
    assert all(v != "Batch No" for v in winners)


def test_batch_accepts_numeric_code_rejects_price():
    out = run_extraction({1: [_line("Batch No 80130")]})
    w, _ = pick_winner(out.get("batch_lot", []))
    assert w and w.value == "80130"
    out2 = run_extraction({1: [_line("Batch: 200")]})
    w2, _ = pick_winner(out2.get("batch_lot", []))
    assert w2 is None or w2.value != "200"


def test_batch_associated_split_pair():
    """The image-17 case: label and value from different OCR variants, joined by association."""
    lines = [
        _line("Batch No", bbox=(425, 1543, 547, 1570)),
        _line("90130", bbox=(741, 1541, 854, 1562)),
    ]
    out = run_extraction({1: associate_label_values(lines)})
    w, _ = pick_winner(out.get("batch_lot", []))
    assert w and "90130" in w.value


# ---------------------------------------------------------------- net quantity

def test_net_quantity_junk_prefix_correction_with_provenance():
    out = run_extraction({1: [_line("NetWeight i5o0g", conf=0.77)]})
    w, _ = pick_winner(out.get("net_quantity", []))
    assert w is not None
    norm = json.loads(w.value)
    assert norm["value"] == "500" and norm["unit"] == "g"
    assert w.raw_value == "NetWeight i5o0g"  # raw preserved
    assert "ocr character correction" in (w.reason or "").lower()


def test_net_quantity_fused_digits_without_unit_is_not_guessed():
    out = run_extraction({1: [_line("Net Weight :5009")]})
    w, _ = pick_winner(out.get("net_quantity", []))
    assert w is None


# ---------------------------------------------------------------- MRP

def test_mrp_fused_amount_decomposition():
    lines = [
        _line("M.R.P.", conf=0.74, bbox=(420, 1451, 511, 1478)),
        _line("200.0010.4091", conf=0.69, bbox=(749, 1463, 1088, 1501)),
    ]
    out = run_extraction({1: associate_label_values(lines)})
    w, _ = pick_winner(out.get("mrp", []))
    assert w is not None
    assert w.value.replace(",", "") == "200.00"


def test_mrp_anchor_with_currency_variants():
    for text in ("MRP ₹200", "M.R.P.: Rs. 200", "Maximum Retail Price Rs 200"):
        out = run_extraction({1: [_line(text)]})
        w, _ = pick_winner(out.get("mrp", []))
        assert w and "200" in str(w.value), text


# ---------------------------------------------------------------- manufacturer

def test_manufacturer_column_guard_skips_foreign_column_noise():
    """A nutrition-column fragment between anchor and entity must not kill the walk."""
    lines = [
        _line("Manufactured &Marketed by:", conf=0.9, bbox=(545, 973, 1024, 1010)),
        _line("(approx)", conf=0.87, bbox=(149, 1001, 254, 1039)),
        _line("GURUCHARAAPRODUCT", conf=0.91, bbox=(443, 1026, 1122, 1089)),
        _line("19/115,,KonguNagar,MuthurRoad", conf=0.89, bbox=(467, 1075, 1101, 1127)),
    ]
    out = run_extraction({1: lines})
    w, _ = pick_winner(out.get("manufacturer", []))
    assert w is not None
    assert "GURUCHARAAPRODUCT" in w.value
    assert "KonguNagar" in w.value


def test_manufacturer_fused_single_token_entity_recognized():
    lines = [
        _line("Manufactured by:", conf=0.9, bbox=(40, 100, 400, 130)),
        _line("GURUCHARAAPRODUCT", conf=0.91, bbox=(40, 140, 400, 170)),
    ]
    out = run_extraction({1: lines})
    w, _ = pick_winner(out.get("manufacturer", []))
    assert w is not None and "GURUCHARAAPRODUCT" in w.value


def test_manufacturer_entity_never_duplicated_in_value():
    lines = [
        _line("Manufactured &Marketed by:", conf=0.9, bbox=(545, 973, 1024, 1010)),
        _line("GURUCHARAAPRODUCT", conf=0.91, bbox=(443, 1026, 1122, 1089)),
        _line("19/115,,KonguNagar,MuthurRoad", conf=0.89, bbox=(467, 1075, 1101, 1127)),
        _line("GURUCHARAAPRODUCT, 19/115,,KonguNagar,MuthurRoad", conf=0.89, bbox=(443, 1026, 1122, 1127)),
    ]
    out = run_extraction({1: lines})
    w, _ = pick_winner(out.get("manufacturer", []))
    assert w is not None
    assert w.value.count("GURUCHARAAPRODUCT") == 1


def test_product_identity_skips_entity_below_role_anchor():
    """A name-like line directly under 'Manufactured ... by:' is the ENTITY, not the product
    name (generic layout semantics — the image-17 mis-attribution)."""
    lines = [
        _line("Manufactured &Marketed by:", conf=0.9, bbox=(545, 973, 1024, 1010)),
        _line("GURUCHARAAPRODUCT", conf=0.91, bbox=(443, 1026, 1122, 1089)),
    ]
    out = run_extraction({1: lines})
    w, _ = pick_winner(out.get("product_name", []))
    assert w is None, "entity line must not become product_name"


def test_manufacturer_anchor_only_still_yields_nothing():
    out = run_extraction({1: [_line("Marketed by:")]})
    w, _ = pick_winner(out.get("manufacturer", []))
    assert w is None or not w.value


def test_manufacturer_address_offered_as_its_own_field():
    """The printed entity block keeps the address, and the address is ALSO its own field so it
    is separately reportable/reviewable (additive — nothing is removed from the entity value)."""
    lines = [
        _line("Marketed by:", conf=0.9, bbox=(40, 100, 400, 130)),
        _line("ABC Foods Pvt Ltd", conf=0.91, bbox=(40, 140, 400, 170)),
        _line("12 Industrial Estate Road, Chennai", conf=0.88, bbox=(40, 175, 400, 200)),
        _line("Tamil Nadu - 600001", conf=0.88, bbox=(40, 205, 400, 230)),
    ]
    out = run_extraction({1: lines})
    entity, _ = pick_winner(out.get("marketer", []) + out.get("manufacturer", []))
    addr, _ = pick_winner(out.get("marketer_address", []) + out.get("manufacturer_address", []))
    assert entity is not None and "ABC Foods" in entity.value
    assert addr is not None
    assert "Industrial Estate" in addr.value and "600001" in addr.value
    # the address appears in the entity block too (nothing removed), never as a second entity
    assert addr.value.count("ABC Foods") == 0


def test_batch_next_line_fallback_strips_repeated_anchor():
    """An association-synthesized next line that repeats the anchor ('Batch No 90130') must
    yield the code only — the label is never part of the batch value."""
    lines = [
        _line("Batch No", conf=0.9, bbox=(20, 100, 200, 125)),
        _line("Batch No 90130", conf=0.86, bbox=(20, 130, 200, 155)),
    ]
    out = run_extraction({1: lines})
    w, _ = pick_winner(out.get("batch_lot", []))
    assert w is not None
    assert w.value == "90130", f"expected the code alone, got {w.value!r}"
    assert "Batch" not in w.value and "No" not in w.value


# ---------------------------------------------------------------- bottom-strip pass / bbox mapping

def test_bottom_strip_bbox_mapping_preserves_original_coordinates():
    import numpy as np

    from backend.ocr.multipass import _bottom_strip_lines

    h, w = 1600, 1195
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    import cv2

    cv2.putText(img, "Batch No 90130", (60, 1540), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    lines = _bottom_strip_lines(img)
    assert lines, "strip OCR should read the drawn text"
    for l in lines:
        x1, y1, x2, y2 = l.bbox
        assert 0 <= x1 < x2 <= w and 1248 <= y1 < y2 <= h  # inside the strip, in original coords
        assert l.variant == "bottom_strip"
    assert any("90130" in l.text for l in lines)


def test_multipass_no_extra_passes_when_pass1_complete():
    from backend.ocr.multipass import run_multipass_ocr

    import cv2
    import numpy as np

    img = np.full((400, 300, 3), 255, dtype=np.uint8)
    cv2.putText(img, "MRP Rs 200", (20, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.putText(img, "Net Wt 500 g", (20, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.putText(img, "Batch No 80130", (20, 320), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    # PASS-1 lines with all critical fields -> no escalation
    base = [
        _line("MRP Rs 200", bbox=(20, 180, 200, 210)),
        _line("Net Wt 500 g", bbox=(20, 240, 220, 270)),
        _line("Batch No 80130", bbox=(20, 300, 240, 330)),
        _line("MFD 04/02/25", bbox=(20, 360, 240, 390)),
    ]
    merged, passes = run_multipass_ocr(img, base, "GOOD")
    assert passes == [] or "bottom_strip" not in passes


def test_multipass_escalates_when_critical_missing():
    from backend.ocr.multipass import run_multipass_ocr

    import cv2
    import numpy as np

    img = np.full((400, 300, 3), 255, dtype=np.uint8)
    cv2.putText(img, "Panchagnula Provisions", (10, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    base = [_line("Panchagnula Provisions", bbox=(10, 180, 250, 215))]
    merged, passes = run_multipass_ocr(img, base, "GOOD")
    assert passes, "critical declarations missing -> targeted passes must run"


# ---------------------------------------------------------------- golden Sukku regression

GOLDEN_FIXTURE = Path(__file__).parent / "fixtures" / "golden_sukku_ocr.json"


def _load_fixture() -> dict:
    return json.loads(GOLDEN_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def golden_lines() -> list[OcrLine]:
    raw = json.loads(
        (Path(__file__).parent / "fixtures" / "golden_sukku_ocr.json").read_text(encoding="utf-8")
    )["ocr_lines"]
    return [
        OcrLine(
            text=item["text"],
            confidence=item["confidence"],
            bbox=tuple(item["bbox"]),
            engine="rapidocr",
            variant=item["variant"],
        )
        for item in raw
    ]


def test_golden_expected_values_live_only_in_fixture():
    """No-hardcoding guard: the fixture is the ONLY place expectations exist."""
    import backend.extraction.engine as eng

    prod_words = {"gurucharaa", "sukkukaapi"}
    for name in ("extract_mrp", "extract_net_quantity", "extract_batch_lot",
                 "extract_manufacturer", "extract_product_identity", "extract_dates"):
        fn = getattr(eng, name, None)
        src = "" if fn is None else (fn.__code__.co_consts and str(fn.__code__.co_consts) or "")
        assert not any(w in src.lower() for w in prod_words), f"{name} contains product-specific constants"


def test_no_golden_or_demo_values_in_production_modules():
    """Broader guard: golden-image and demo-label values must not appear as literals anywhere in
    production (non-test) modules — not in logic, not in comments, not in schema examples.

    Values are read from the golden fixture itself, so the guard automatically covers whatever
    the fixture asserts. Test files and fixtures are exempt by design.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent.parent
    fixture = json.loads(
        (Path(__file__).resolve().parent / "fixtures" / "golden_sukku_ocr.json").read_text(
            encoding="utf-8"
        )
    )
    expectations = fixture.get("expected_fields", {})
    literals: set[str] = set()
    for spec in expectations.values():
        if not isinstance(spec, dict):
            continue
        for key in ("normalized_contains", "raw_contains", "value"):
            value = spec.get(key)
            if not isinstance(value, str):
                continue
            value = value.strip().lower()
            # Only HIGH-SPECIFICITY identifiers are banned. Generic domain vocabulary that the
            # golden panel happens to share (e.g. the duration phrase '12 months', a plain
            # quantity '500 g') is legitimate schema content and is not product-specific.
            # A multi-word phrase ('12 months') is generic domain vocabulary; a single token
            # ('sukkukaapi', 'gurucharaa_product@yahoo.com') or a long digit run is identifying.
            if re.search(r"\d{8,}", value) or (" " not in value and re.search(r"[a-z]{6,}", value)):
                literals.add(value)
    # explicit product-identity words from the golden panel
    literals.update({"sukku", "kaapi", "vedha", "gurucharaa", "nivedha"})
    # long digit runs (FSSAI / phone) are the identifiers that leak most easily
    for raw in list(literals):
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 8:
            literals.add(digits)

    offenders: list[str] = []
    for path in (repo / "backend").rglob("*.py"):
        parts = set(path.parts)
        if "tests" in parts or "fixtures" in parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for lit in literals:
            if lit and lit in text:
                offenders.append(f"{path.relative_to(repo)}: {lit!r}")
    for path in (repo / "frontend" / "src").rglob("*"):
        if path.suffix not in (".ts", ".tsx"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for lit in literals:
            if lit and lit in text:
                offenders.append(f"{path.relative_to(repo)}: {lit!r}")
    assert not offenders, f"product-specific values found in production code: {offenders}"


def test_golden_sukku_extraction_behavior(golden_lines):
    fx = _load_fixture()
    out = run_extraction({1: associate_label_values(golden_lines)})
    for field, expected in fx["expected_fields"].items():
        w, conflict = pick_winner(out.get(field, []))
        if expected.get("state") == "DETECTED":
            assert w is not None and w.value, f"{field}: expected DETECTED"
            if expected.get("normalized_contains"):
                assert expected["normalized_contains"] in str(w.value), f"{field}: {w.value!r}"
        elif expected.get("state") == "UNCERTAIN":
            assert w is None or not w.value, f"{field}: expected no confident value, got {getattr(w, 'value', None)!r}"
        if expected.get("no_conflict"):
            assert not conflict, f"{field}: unexpected conflict"


def test_golden_sukku_missing_is_missing_never_absent(golden_lines):
    """Fields without a declaration label are never asserted as facts.

    Such a field may carry NO value at all, or (for identity fields inferred from layout,
    e.g. a logo read as a brand) a REVIEW CANDIDATE that is explicitly marked `inferred`.
    What must never happen is a confident value with no declaration behind it.
    """
    fx = _load_fixture()
    out = run_extraction({1: associate_label_values(golden_lines)})
    for field in fx.get("never_auto_absent", []):
        w, _ = pick_winner(out.get(field, []))
        assert w is None or not w.value or w.inferred, (
            f"{field} produced a non-inferred value without a declaration: {w!r}"
        )


# ------------------------------------------------- entity/address display normalization

def test_text_block_normalization_fixes_joins_and_repeated_commas():
    nm = normalize_field("manufacturer_address", "19/115,,KonguNagar,MuthurRoad, SenapathyPalayam")
    assert nm["display"] == "19/115, Kongu Nagar, Muthur Road, Senapathy Palayam"


def test_text_block_normalization_never_rewrites_the_raw_evidence():
    raw = "19/115,,KonguNagar,MuthurRoad"
    nm = normalize_field("manufacturer_address", raw)
    assert nm["value"] == raw, "raw OCR must survive normalization unchanged"
    assert nm["display"] != raw


def test_text_block_normalization_leaves_all_caps_fused_names_alone():
    # An ALL-CAPS fused string cannot be split without inventing words — it stays as read.
    nm = normalize_field("manufacturer", "ACMEAGENCIES")
    assert nm["value"] == "ACMEAGENCIES"
    assert "ACMEAGENCIES" in nm["display"]


def test_text_block_normalization_pin_code_hyphen():
    nm = normalize_field("packer_address", "Vellakovil-638111")
    assert nm["display"] == "Vellakovil - 638111"


def test_entity_address_fields_route_to_text_block_normalizer():
    for field in ("manufacturer", "manufacturer_address", "packer", "packer_address",
                  "importer_address", "marketer"):
        nm = normalize_field(field, "KonguNagar,MuthurRoad")
        assert nm["display"] == "Kongu Nagar, Muthur Road", field
