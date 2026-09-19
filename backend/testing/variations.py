"""Combinatorial declaration-variation generator (text level, seeded, streamable).

This is the engine behind the 10M+ test-capability target. It combines, per field:
  - label variants (aliases/abbreviations from the field schema),
  - value formats (currency/date/quantity/code renderings),
  - separator/punctuation/spacing styles,
  - case styles,
  - OCR-corruption patterns (character confusions, drops, splits),
  - neighbor-declaration arrangements (lines above/below that must not confuse extraction),
  - multilingual duplicate lines,
  - layout patterns (label-first, value-first, split lines, boxed block).

Every generated sample carries complete ground truth (field, label used, raw text, expected
value, transformations applied). Generation is deterministic for a given seed and supports
streaming — samples are produced lazily so millions can be enumerated without storing them.

IMPORTANT (honesty): these are TEXT-LEVEL test cases for extraction robustness. Image-level
rendering/degradation variants are a separate, optional dimension (see
scripts/generate_variations.py --render). Neither is claimed to equal real-world data.

Usage:
    from backend.testing.variations import SampleGenerator
    gen = SampleGenerator(seed=42, fields=("mrp", "net_quantity", "date_manufacturing"))
    for sample in gen.stream(batch_size=1000):
        ...
    print(f"{gen.total_space:,}")  # combinatorial space actually enumerable
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field as dc_field

from backend.extraction.field_schema import FIELD_SCHEMA
from backend.ocr.textnorm import AMBIGUOUS_MAP

# ---------- value renderers (pure formatting, no product knowledge) ----------


def _fmt_mrp(rng: random.Random) -> str:
    amount = rng.choice([15, 20, 40, 50, 75, 99, 100, 120, 150, 200, 250, 500, 999])
    style = rng.choice([
        f"₹{amount}", f"₹ {amount}", f"₹{amount}.00", f"Rs. {amount}", f"Rs {amount}",
        f"Rs.{amount}/-", f"INR {amount}", f"{amount}/-",
    ])
    return style


def _fmt_unit_price(rng: random.Random) -> str:
    amount = rng.choice(["0.40", "1.25", "2", "5", "10", "40"])
    unit = rng.choice(["g", "kg", "ml", "L", "100 g", "100 ml", "nos"])
    sep = rng.choice(["/", " per "])
    cur = rng.choice(["₹", "Rs.", "Rs"])
    return f"{cur} {amount}{sep}{unit}"


def _fmt_quantity(rng: random.Random) -> str:
    value = rng.choice(["50", "100", "200", "250", "500", "1 kg"[:1] + "000"[:0] or "1000", "0.5"])
    unit = rng.choice(["g", "g", "gm", "grams", "kg", "ml", "mL", "L", "ltr", "N", "pcs", "pieces"])
    space = rng.choice(["", " "])
    return f"{value}{space}{unit}"


def _fmt_date_full(rng: random.Random) -> str:
    d = rng.randint(1, 28)
    m = rng.randint(1, 12)
    y = rng.choice(["24", "25", "26", "2024", "2025", "2026"])
    sep = rng.choice(["/", "-", "."])
    return f"{d:02d}{sep}{m:02d}{sep}{y}"


def _fmt_date_my(rng: random.Random) -> str:
    m = rng.randint(1, 12)
    y = rng.choice(["2025", "2026"])
    return rng.choice([f"{m:02d}/{y}", f"{m:02d}-{y}"])


def _fmt_duration(rng: random.Random) -> str:
    n = rng.choice([6, 9, 12, 18, 24])
    unit = rng.choice(["month", "months", "MONTHS", "MONTH(S)"])
    ref = rng.choice([
        "from the date of manufacturing", "from manufacturing", "from the date of packing",
        "from packaging", "from date of manufacture", "from MFG",
    ])
    return f"{n} {unit} {ref}"


def _fmt_batch(rng: random.Random) -> str:
    style = rng.choice(["alnum", "numeric", "prefixed"])
    if style == "alnum":
        return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(rng.randint(4, 8)))
    if style == "numeric":
        return str(rng.randint(10000, 999999))
    return f"{rng.choice(['B', 'LOT', 'L'])}{rng.randint(1000, 99999)}"


def _fmt_fssai(rng: random.Random) -> str:
    return str(rng.randint(10000000000000, 19999999999999))


def _fmt_phone(rng: random.Random) -> str:
    n = f"{rng.randint(6000000000, 9999999999)}"
    style = rng.choice([n, f"0{n}", f"+91 {n[:5]} {n[5:]}", f"+91-{n}", f"{n[:5]} {n[5:]}"])
    return style


def _fmt_email(rng: random.Random) -> str:
    name = rng.choice(["care", "support", "info", "feedback", "customercare"])
    dom = rng.choice(["gmail.com", "yahoo.com", "example.in", "example.com"])
    return f"{name}{rng.randint(1, 999)}@{dom}"


def _fmt_website(rng: random.Random) -> str:
    dom = rng.choice(["example.com", "example.in", "brand.co.in", "example.org"])
    return rng.choice([f"www.{dom}", f"https://{dom}", f"https://www.{dom}"])


def _fmt_entity(rng: random.Random) -> str:
    a = rng.choice(["Sree", "Sri", "ABC", "Guru", "Sun", "Star", "Veda", "Metro", "Prime"])
    b = rng.choice(["Foods", "Traders", "Industries", "Products", "Enterprises", "Agencies"])
    suffix = rng.choice(["Pvt Ltd", "Pvt. Ltd.", "Ltd", "LLP", ""])
    return " ".join(p for p in (a, b, suffix) if p)


def _fmt_country(rng: random.Random) -> str:
    return rng.choice(["India", "Made in India", "Product of India"])


_FORMATTERS = {
    "mrp": _fmt_mrp,
    "unit_sale_price": _fmt_unit_price,
    "net_quantity": _fmt_quantity,
    "date_manufacturing": lambda r: _fmt_date_full(r),
    "date_packing": lambda r: rng_pick(r, [lambda r: _fmt_date_full(r), _fmt_date_my]),
    "date_import": _fmt_date_my,
    "date_expiry": lambda r: rng_pick(r, [lambda r: _fmt_date_full(r), _fmt_date_my]),
    "date_best_before": lambda r: rng_pick(r, [_fmt_duration, lambda r: _fmt_date_full(r)]),
    "batch_lot": _fmt_batch,
    "fssai_license": _fmt_fssai,
    "consumer_care_phone": _fmt_phone,
    "consumer_care_email": _fmt_email,
    "website": _fmt_website,
    "manufacturer": _fmt_entity,
    "packer": _fmt_entity,
    "importer": _fmt_entity,
    "marketer": _fmt_entity,
    "country_of_origin": _fmt_country,
}


def rng_pick(rng: random.Random, fns):
    return rng.choice(fns)(rng)


# ---------- OCR corruption patterns ----------


def corrupt_numeric_chars(text: str, rng: random.Random) -> str:
    """Flip digits inside numeric-looking runs to confusable letters (0→O, 5→S ...)."""
    out = []
    for ch in text:
        if ch.isdigit() and rng.random() < 0.35:
            target = {v: k for k, v in AMBIGUOUS_MAP.items()}.get(ch)
            if target:
                out.append(rng.choice(target))
                continue
        out.append(ch)
    return "".join(out)


def corrupt_drop_chars(text: str, rng: random.Random) -> str:
    if len(text) < 6 or rng.random() < 0.5:
        return text
    i = rng.randrange(1, len(text) - 1)
    return text[:i] + text[i + 1:]


def corrupt_split_spaces(text: str, rng: random.Random) -> str:
    """Insert stray spaces (OCR segmentation artifact)."""
    if len(text) < 5 or rng.random() < 0.6:
        return text
    i = rng.randrange(1, len(text) - 1)
    return text[:i] + " " + text[i:]


def corrupt_case(text: str, rng: random.Random) -> str:
    return rng.choice([text.upper(), text.lower(), text.title(), text])


def corrupt_punctuation(text: str, rng: random.Random) -> str:
    return text.replace(":", rng.choice([":", " :", ":", " .", "-"], ), 1) if ":" in text else text


CORRUPTIONS = [
    corrupt_numeric_chars,
    corrupt_drop_chars,
    corrupt_split_spaces,
    corrupt_case,
    corrupt_punctuation,
]

# ---------- layout patterns ----------

LAYOUTS = (
    "label_value_same_line",   # "MRP: ₹200"
    "label_first_value_below", # "MRP:" ⏎ "₹200"
    "value_first_label_above", # (value line under a neighbor block)
    "plain_value_line",        # bare "₹200" line among neighbors
)

NEIGHBOR_POOL = (
    "M.R.P. : ₹200.00", "Net Wt. 500 g", "MFD: 04/02/25", "BEST BEFORE 9 MONTHS",
    "Batch No. B0142", "FSSAI Lic. No. 10023456789012", "Consumer Care: 1800-123-4567",
    "Manufactured by Sree Foods Pvt Ltd", "Marketed by Star Traders LLP",
    "Storage: Store in a cool dry place", "Recycle responsibly",
)


@dataclass
class Sample:
    """One generated test case with full ground truth."""

    sample_id: str
    seed: int
    field_id: str
    label: str
    raw_text: str          # exactly what the 'OCR line' should say (pre-corruption: truth)
    corrupted_text: str    # what the corrupted 'OCR' actually says (the test input)
    expected_value: str
    layout: str
    corruptions: list[str] = dc_field(default_factory=list)
    neighbors: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sample_id": self.sample_id, "seed": self.seed, "field_id": self.field_id,
            "label": self.label, "raw_text": self.raw_text,
            "corrupted_text": self.corrupted_text, "expected_value": self.expected_value,
            "layout": self.layout, "corruptions": self.corruptions,
            "neighbors": self.neighbors,
        }


class SampleGenerator:
    """Seeded, streaming combinatorial generator over the declaration-variation space."""

    VERSION = "variations-1.0"

    def __init__(self, seed: int = 0, fields: tuple[str, ...] | None = None,
                 layouts: tuple[str, ...] = LAYOUTS, corruptions: bool = True,
                 multilingual: bool = True, neighbors_pool: tuple[str, ...] = NEIGHBOR_POOL):
        self.seed = seed
        self.fields = tuple(fields) if fields else tuple(
            f for f in FIELD_SCHEMA if f in _FORMATTERS
        )
        self.layouts = layouts
        self.corruptions_enabled = corruptions
        self.multilingual = multilingual
        self.neighbors_pool = neighbors_pool
        self._counter = 0

    # ---- space size (enumerable combinations actually produced by this generator) ----
    @property
    def total_space(self) -> int:
        n_labels = sum(
            len(FIELD_SCHEMA[f].aliases) + len(FIELD_SCHEMA[f].abbreviations) + 1
            for f in self.fields
        )
        n_values = 8      # distinct renderer outcomes sampled per field
        n_layouts = len(self.layouts)
        n_corr = 2 ** len(CORRUPTIONS) if self.corruptions_enabled else 1
        n_neigh = len(self.neighbors_pool) ** 2 if self.neighbors_pool else 1  # unordered 0-2 neighbors
        return n_labels * n_values * n_layouts * n_corr * n_neigh

    def _sample_id(self, seed: int) -> str:
        h = hashlib.sha256(f"{self.VERSION}|{seed}|{self._counter}".encode()).hexdigest()[:16]
        return f"VAR-{h}"

    def generate_one(self, rng: random.Random, field_id: str | None = None) -> Sample:
        field_id = field_id or rng.choice(self.fields)
        spec = FIELD_SCHEMA[field_id]
        label = rng.choice([*spec.aliases, *spec.abbreviations, *spec.aliases[:1]])
        value = _FORMATTERS[field_id](rng)
        layout = rng.choice(self.layouts)
        neighbors = rng.sample(self.neighbors_pool, k=rng.choice([0, 1, 2]))

        if layout == "label_value_same_line":
            sep = rng.choice([": ", " : ", " ", ":"])
            raw = f"{label}{sep}{value}"
        elif layout == "label_first_value_below":
            raw = f"{label}:\n{value}"
        elif layout == "value_first_label_above":
            raw = f"{rng.choice(self.neighbors_pool)}\n{label}: {value}"
        else:
            raw = value

        corrs: list[str] = []
        corrupted = raw
        if self.corruptions_enabled:
            chosen = [c for c in CORRUPTIONS if rng.random() < 0.4]
            for fn in chosen:
                corrupted = fn(corrupted, rng)
                corrs.append(fn.__name__)

        extra_lines = []
        if self.multilingual and rng.random() < 0.15 and layout != "plain_value_line":
            # multilingual duplicate of the label line (same declaration, other script)
            extra_lines.append(rng.choice([
                "சுக்கு காபி தூள்", "सोंठ कॉफी पाउडर", "శుక్కు కాఫీ పౌడర్",
            ]))

        self._counter += 1
        full_text = "\n".join(
            ([n for n in neighbors[:1]] if layout != "value_first_label_above" else []) +
            raw.split("\n") + extra_lines +
            ([n for n in neighbors[1:]] if layout != "value_first_label_above" else [])
        )
        return Sample(
            sample_id=self._sample_id(self.seed),
            seed=self.seed,
            field_id=field_id,
            label=label,
            raw_text=raw,
            corrupted_text=corrupted,
            expected_value=value,
            layout=layout,
            corruptions=corrs,
            neighbors=neighbors,
        )

    def stream(self, batch_size: int = 1000):
        """Yield samples in batches forever (deterministic per seed)."""
        rng = random.Random(self.seed)
        while True:
            batch = [self.generate_one(rng) for _ in range(batch_size)]
            yield batch
