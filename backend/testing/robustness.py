"""Robustness evaluation harness: extraction vs generated declaration variations.

Runs the REAL extraction pipeline (run_extraction, including numeric recovery and
label-value association) over generated text-level variations and measures, per field:
  - detected (expected value recovered, normalized comparison)
  - detected_raw_match (exact string match on the raw rendering)
  - missed (no candidate for the field)
  - wrong_value (candidate exists but a different value won)
  - falsely_assigned (field extracted from a line belonging to a DIFFERENT field —
    counts toward hallucination/false-positive metrics)
  - uncertain-but-anchored (anchor found; no value — safe behavior, not a failure)

Failure classification (WHY was it wrong): which corruption(s) co-occurred with the miss,
so improvement is targeted (numeric recovery, association, layout handling...).

All metrics are computed over SYNTHETIC text variations and are reported as such — never
as real-world accuracy. Real-image accuracy comes only from data/benchmark/ ground truth.
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field as dc_field

from backend.extraction.engine import run_extraction
from backend.extraction.association import associate_label_values
from backend.ocr.base import OcrLine
from backend.testing.variations import SampleGenerator, Sample

# Fields whose expected value is numeric-ish and where OCR letter-confusions matter.
_NUMERIC_FIELDS = {"mrp", "net_quantity", "date_manufacturing", "date_packing", "date_import",
                   "date_expiry", "date_best_before", "batch_lot", "fssai_license",
                   "consumer_care_phone"}


@dataclass
class FieldMetrics:
    field_id: str
    total: int = 0
    detected: int = 0
    raw_match: int = 0
    missed: int = 0
    wrong_value: int = 0
    falsely_assigned: int = 0
    anchored_uncertain: int = 0

    def as_dict(self) -> dict:
        d = {
            "total": self.total, "detected": self.detected, "raw_match": self.raw_match,
            "missed": self.missed, "wrong_value": self.wrong_value,
            "falsely_assigned": self.falsely_assigned, "anchored_uncertain": self.anchored_uncertain,
        }
        denom = self.total or 1
        d["detection_rate"] = round(self.detected / denom, 4)
        d["false_assignment_rate"] = round(self.falsely_assigned / denom, 4)
        return d


@dataclass
class RobustnessReport:
    samples: int = 0
    fields: dict[str, FieldMetrics] = dc_field(default_factory=dict)
    failure_by_corruption: Counter = dc_field(default_factory=Counter)
    failure_examples: dict[str, list[str]] = dc_field(default_factory=lambda: defaultdict(list))
    corruption_totals: Counter = dc_field(default_factory=Counter)
    multilingual_false_conflicts: int = 0

    def summary(self) -> dict:
        total_detected = sum(m.detected for m in self.fields.values())
        total_all = sum(m.total for m in self.fields.values())
        total_false = sum(m.falsely_assigned for m in self.fields.values())
        return {
            "kind": "synthetic-text-variation-robustness",
            "samples": self.samples,
            "field_count": len(self.fields),
            "detection_rate": round(total_detected / total_all, 4) if total_all else 0.0,
            "false_assignment_rate": round(total_false / total_all, 4) if total_all else 0.0,
            "multilingual_false_conflicts": self.multilingual_false_conflicts,
            "failure_by_corruption": dict(self.failure_by_corruption.most_common()),
            "fields": {k: v.as_dict() for k, v in sorted(self.fields.items())},
        }


def _norm_value(field_id: str, value: str) -> str:
    """Canonical comparison form (whitespace/format-insensitive where safe)."""
    v = (value or "").strip().lower()
    v = v.replace(",", "")
    if field_id in _NUMERIC_FIELDS:
        m = re.search(r"\d[\d.]*", v)
        if m:
            num = m.group(0).rstrip(".")
            try:
                f = float(num)
                return str(int(f)) if f == int(f) else str(f)
            except ValueError:
                return num
    return re.sub(r"\s+", " ", v)


_DURATION_UNIT_RE = re.compile(r"(\d{1,3})\s*(month|months|day|days|year|years|week|weeks)", re.IGNORECASE)
_REF_RE = re.compile(r"from[^a-z0-9]*(?:the\s*)?(?:date\s*of\s*)?(manufactur\w*|mfg\w*|mfd|pack\w*|import\w*)", re.IGNORECASE)


def _expected_semantic(field_id: str, expected: str, raw: str) -> str | None:
    """Canonicalize an EXPECTED value the way the extractor canonizes its own outputs.

    Dates: '04/02/25' → ISO (day/month ambiguity tolerated both ways). Durations:
    '12 MONTHS from the date of manufacturing' → semantic JSON. Phones: digits.
    Returns None when the field has no special canonicalization.
    """
    import json as _json

    if field_id.startswith("date_"):
        dm = _DURATION_UNIT_RE.search(raw)
        if dm:
            rm = _REF_RE.search(raw)
            ref_word = rm.group(1).lower() if rm else ""
            if ref_word.startswith(("manufactur", "mfg", "mfd")):
                ref = "MANUFACTURING_DATE"
            elif ref_word.startswith("import"):
                ref = "IMPORT_DATE"
            elif ref_word:
                ref = "PACKING_DATE"
            else:
                return _norm_value(field_id, expected)
            n = dm.group(1)
            unit = dm.group(2).lower()
            unit = unit if unit.endswith("s") else unit + "s"
            return _json.dumps({"type": "DURATION_FROM_REFERENCE", "duration": f"{n} {unit}", "reference": ref})
        fm = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b", raw)
        if fm:
            d, mth, y = int(fm.group(1)), int(fm.group(2)), int(fm.group(3))
            if y < 100:
                y += 2000
            if mth > 12 and d <= 12:
                d, mth = mth, d
            if 1 <= mth <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mth:02d}-{d:02d}"
        my = re.search(r"\b(\d{1,2})[/\-](\d{4})\b", raw)
        if my:
            return f"{my.group(2)}-{int(my.group(1)):02d}"
        return _norm_value(field_id, expected)
    if field_id == "consumer_care_phone":
        digits = re.sub(r"\D", "", expected)
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        return digits
    return None


def _extracted_semantic(field_id: str, value: str) -> str:
    """Canonicalize an EXTRACTED value for comparison."""
    if field_id.startswith("date_") and value.startswith("{"):
        try:
            data = json.loads(value)
        except ValueError:
            return value
        dur = str(data.get("duration", ""))
        dur = re.sub(r"(month|year|week|day)s?$", lambda m: m.group(1) + "s" if not m.group(1).endswith("s") else m.group(1), dur)
        return json.dumps({"type": "DURATION_FROM_REFERENCE", "duration": dur, "reference": data.get("reference", "")})
    return value


def _values_match(field_id: str, extracted: str, expected: str, raw: str) -> bool:
    """Field-aware equality between an extracted value and the expected value."""
    if field_id.startswith("date_"):
        exp = _expected_semantic(field_id, expected, raw)
        got = _extracted_semantic(field_id, extracted).replace("|AMBIGUOUS", "")
        if exp and got:
            if exp.startswith("{") or got.startswith("{"):
                try:
                    e = json.loads(exp) if exp.startswith("{") else {"duration": exp}
                    g = json.loads(got) if got.startswith("{") else {"duration": got}
                    return (
                        str(e.get("duration", "")).lower().replace(" ", "")
                        == str(g.get("duration", "")).lower().replace(" ", "")
                        and (not e.get("reference") or not g.get("reference") or e["reference"] == g["reference"])
                    )
                except ValueError:
                    return False
            return exp.replace("|AMBIGUOUS", "") == got
        return False
    if field_id in ("mrp",):
        return _norm_value(field_id, extracted) == _norm_value(field_id, expected)
    if field_id == "net_quantity":
        try:
            q = json.loads(extracted)
        except (ValueError, TypeError):
            return False
        exp_num = re.search(r"([\d.]+)", expected)
        exp_unit = re.search(r"(kg|gm|grams?|g|ml|ltr|L|pcs|pieces|N)", expected, re.IGNORECASE)
        if not exp_num or not exp_unit:
            return False
        got_unit = str(q.get("unit", "")).lower()
        exp_unit_c = exp_unit.group(1).lower()
        unit_alias = {"gm": "g", "grams": "g", "gram": "g", "ltr": "l", "mL": "ml"}
        exp_unit_c = unit_alias.get(exp_unit_c, exp_unit_c)
        try:
            same_num = float(q.get("value")) == float(exp_num.group(1))
        except (TypeError, ValueError):
            same_num = False
        return same_num and got_unit == exp_unit_c
    if field_id == "batch_lot":
        return extracted.strip().lower() == expected.strip().lower()
    if field_id == "fssai_license":
        return re.sub(r"\D", "", extracted) == re.sub(r"\D", "", expected)
    if field_id in ("consumer_care_email", "website"):
        return extracted.strip().lower().rstrip(".,;") == expected.strip().lower().rstrip(".,;")
    if field_id == "unit_sale_price":
        e = re.sub(r"\s+", "", extracted.lower())
        x = re.sub(r"\s+", "", expected.lower())
        return e == x or (re.search(r"\d+(\.\d+)?", e) or [""])[0] == (re.search(r"\d+(\.\d+)?", x) or [""])[0]
    if field_id in ("manufacturer", "packer", "importer", "marketer", "country_of_origin"):
        exp_c = re.sub(r"\s+", " ", expected.strip().lower())
        got_c = re.sub(r"\s+", " ", extracted.strip().lower())
        # extractor may append address/prefix text — match when the expected entity is contained
        return exp_c in got_c or got_c in exp_c
    return _norm_value(field_id, extracted) == _norm_value(field_id, expected)


def _bbox_for(i: int) -> tuple[int, int, int, int]:
    y = 40 + i * 34
    return (10, y, 400, y + 22)


def _evaluate_sample(sample: Sample, metrics: dict[str, FieldMetrics], report: RobustnessReport) -> None:
    lines: list[OcrLine] = []
    idx = 0
    for text in sample.corrupted_text.split("\n"):
        if text.strip():
            lines.append(OcrLine(text=text.strip(), confidence=0.85, bbox=_bbox_for(idx), engine="synthetic", variant="original"))
            idx += 1
    for text in sample.neighbors:
        lines.append(OcrLine(text=text, confidence=0.85, bbox=_bbox_for(idx), engine="synthetic", variant="original"))
        idx += 1
    lines = associate_label_values(lines)
    per_field = run_extraction({1: lines})

    m = metrics.setdefault(sample.field_id, FieldMetrics(sample.field_id))
    m.total += 1
    report.samples += 1
    for c in sample.corruptions:
        report.corruption_totals[c] += 1

    cands = per_field.get(sample.field_id, [])
    expected_norm = _norm_value(sample.field_id, sample.expected_value)
    won = None
    if cands:
        won = sorted(cands, key=lambda c: c.score, reverse=True)[0]
    if won is None:
        m.missed += 1
        report.failure_by_corruption["NO_CANDIDATE|" + (sample.corruptions[0] if sample.corruptions else "clean")] += 1
        if len(report.failure_examples[sample.field_id]) < 5:
            report.failure_examples[sample.field_id].append(sample.raw_text)
        return
    if _values_match(sample.field_id, won.value, sample.expected_value, sample.raw_text):
        m.detected += 1
        if won.raw_value.replace(" ", "").lower() == sample.raw_text.replace(" ", "").lower():
            m.raw_match += 1
    else:
        m.wrong_value += 1
        report.failure_by_corruption["WRONG_VALUE|" + (sample.corruptions[0] if sample.corruptions else "clean")] += 1
        if len(report.failure_examples[sample.field_id]) < 5:
            report.failure_examples[sample.field_id].append(f"{sample.raw_text!r} -> {won.value!r}")

    # hallucination guard: candidates of THIS field whose raw line belongs to a neighbor
    # declaration of another field family (checked by exact text match against neighbor pool)
    for c in cands:
        stripped = (c.raw_value or "").strip().lower()
        if stripped in {n.lower() for n in sample.neighbors} and c.score >= won.score * 0.9:
            m.falsely_assigned += 1
            break


def evaluate_robustness(seed: int = 1234, samples_per_field: int = 200,
                        fields: tuple[str, ...] | None = None) -> RobustnessReport:
    """Run the harness. Deterministic per seed. Returns the full report."""
    gen = SampleGenerator(seed=seed, fields=fields)
    fields_to_test = tuple(fields) if fields else gen.fields
    metrics: dict[str, FieldMetrics] = {}
    report = RobustnessReport(fields=metrics)

    rng = random.Random(seed)
    # group generation round-robin so every field gets samples_per_field samples
    for _ in range(samples_per_field):
        for f in fields_to_test:
            sample = gen.generate_one(rng, field_id=f)
            _evaluate_sample(sample, metrics, report)
    return report


def render_markdown(report: RobustnessReport) -> str:
    """Markdown rendering for docs/ROBUSTNESS_REPORT.md."""
    s = report.summary()
    lines = [
        "# Extraction Robustness Report (Synthetic Text Variations)",
        "",
        f"- Samples: **{s['samples']}** generated declaration variations (seeded, reproducible)",
        f"- Kind: **{s['kind']}** — synthetic; NOT real-world accuracy",
        f"- Overall detection rate: **{s['detection_rate']:.1%}**",
        f"- False-assignment rate (hallucination guard): **{s['false_assignment_rate']:.2%}**",
        f"- Multilingual false-conflict count: **{s['multilingual_false_conflicts']}**",
        "",
        "| Field | n | detected | raw match | missed | wrong value | false assign | anchored-uncertain | detection rate |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for fid, m in s["fields"].items():
        lines.append(
            f"| {fid} | {m['total']} | {m['detected']} | {m['raw_match']} | {m['missed']} | "
            f"{m['wrong_value']} | {m['falsely_assigned']} | {m['anchored_uncertain']} | {m['detection_rate']:.1%} |"
        )
    lines += ["", "## Failure modes (by corruption type)", ""]
    if s["failure_by_corruption"]:
        for k, v in s["failure_by_corruption"].items():
            lines.append(f"- `{k}`: {v}")
    else:
        lines.append("- none recorded")
    return "\n".join(lines) + "\n"
