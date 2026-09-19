"""Field variation schema: the knowledge layer of declaration representations.

For every tracked field this declares the ways it can appear on a real package:
aliases, abbreviations, anchor keywords, OCR-confusable variants, value types, units,
and formatting. Extraction anchor regexes are DERIVED from this schema so that widening
the schema automatically widens recognition — no per-product hard-coding, ever.

The schema documents linguistic variety (hundreds of thousands of label/value/format
combinations per field family). Combined with the synthetic variation generator, this
yields a combinatorial test space far exceeding 10 million possibilities — these are
*test-case possibilities*, not claims of real-world images seen.

Extending recognition = edit this file (or future: load a YAML/JSON variant); no
extractor code changes required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field


@dataclass(frozen=True)
class FieldSpec:
    """One declaration field's representation knowledge."""

    field_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    abbreviations: tuple[str, ...]
    anchor_pattern: str  # regex source for the label/anchor
    value_type: str  # currency | quantity | date | duration | code | contact | text | url
    units: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    ocr_confusables: tuple[str, ...] = ()  # chars frequently misread in this field's values
    languages: tuple[str, ...] = ("en",)
    notes: str = ""


# ---------------------------------------------------------------------------
# The schema. Anchor patterns are deliberately generous (they OBSERVE candidates);
# validators and negative guards (elsewhere) decide which candidates are trustworthy.
# ---------------------------------------------------------------------------

FIELD_SCHEMA: dict[str, FieldSpec] = {
    "mrp": FieldSpec(
        field_id="mrp",
        canonical_name="Retail Sale Price (MRP)",
        aliases=(
            "MRP", "M.R.P.", "M R P", "MRP.", "M.R.P",
            "Maximum Retail Price", "Maximum Retail Selling Price",
            "Retail Price", "Retail Sale Price", "Max. Retail Price",
            "Max Retail Price", "Maximum retail price (inclusive of all taxes)",
        ),
        abbreviations=("MRP", "MRP.", "M.R.P.", "M.R.P", "MRP :"),
        anchor_pattern=r"\b(?:m\.?\s*r\.?\s*p\.?|maximum\s+retail\s+(?:selling\s+)?price|retail\s+(?:sale\s+)?price|max\.?\s*retail\s+price)\b\.?\s*[:]?",
        value_type="currency",
        units=("INR",),
        formats=("₹200", "Rs. 200", "Rs 200/-", "INR 200", "₹200.00", "₹ 200", "200/-"),
        ocr_confusables=("O", "S", "B"),
        notes="Unit sale price (₹/g) is a DIFFERENT field and never satisfies MRP.",
    ),
    "unit_sale_price": FieldSpec(
        field_id="unit_sale_price",
        canonical_name="Unit Sale Price",
        aliases=("Unit Price", "Unit Sale Price", "Price per unit", "Per unit price"),
        abbreviations=(),
        anchor_pattern=r"(?:₹|rs\.?|inr)\s*[\d.,]+\s*(?:/|per\s)\s*",
        value_type="currency_per_unit",
        units=("g", "kg", "ml", "L", "100 g", "100 ml", "nos"),
        formats=("₹0.40/g", "Rs. 40 per kg", "₹10/100g"),
    ),
    "net_quantity": FieldSpec(
        field_id="net_quantity",
        canonical_name="Net Quantity",
        aliases=(
            "Net Quantity", "Net Qty", "Net Weight", "Net Wt", "Net Wt.", "Net weight when packed",
            "Net Content", "Net Contents", "Net Volume", "Nett Weight", "Content",
        ),
        abbreviations=("Net Qty", "Net Wt", "N.Wt", "Nt Wt"),
        anchor_pattern=r"\bnet\s*(?:qty|quantity|wt\.?|weight|volume|contents?|content)\b|\bnett+\s*(?:wt\.?|weight)\b",
        value_type="quantity",
        units=("g", "gm", "grams", "kg", "kgs", "ml", "mL", "L", "ltr", "litre", "liter", "N", "nos", "no", "pcs", "pieces", "units", "cm", "m", "mm"),
        formats=("500g", "500 g", "0.5 kg", "200 mL", "1 L", "10 N", "20 pieces"),
        ocr_confusables=("O", "S"),
        notes="Never confuse with gross weight, serving size, ingredient quantity.",
    ),
    "date_manufacturing": FieldSpec(
        field_id="date_manufacturing",
        canonical_name="Date of Manufacture",
        aliases=(
            "Manufacturing Date", "Date of Manufacture", "Date of Mfg", "Manufactured on",
            "Manufactured", "Mfg Date", "Mfg. Date", "MFG Date", "MFD", "MFD.", "MFG", "MFG.",
            "Mfg", "Mfg.", "Production Date",
        ),
        abbreviations=("MFD", "MFD.", "MFG", "MFG.", "Mfg", "Mfg.", "Mfg dt"),
        anchor_pattern=(
            r"\b(?:manufacturing\s*date|date\s*of\s*manufacture|date\s*of\s*mfg|mfg\.?\s*date|"
            r"manufactured\s*(?:on|date)?|mfd\.?|mfgd?\.?|production\s*date)\b"
        ),
        value_type="date",
        formats=("04/02/25", "04-02-2025", "04.02.2025", "02/2025", "FEB 2025", "04 FEB 2025"),
        ocr_confusables=("I", "S", "B", "Z"),
    ),
    "date_packing": FieldSpec(
        field_id="date_packing",
        canonical_name="Date of Packing / Pre-packing",
        aliases=(
            "Packing Date", "Date of Packing", "Packed on", "Packed", "Packaging Date",
            "Pre-packing date", "PKD", "PKD.", "Pkd", "Pkd.", "Packed date",
        ),
        abbreviations=("PKD", "PKD.", "Pkd", "Pkd."),
        anchor_pattern=(
            r"\b(?:packing\s*date|date\s*of\s*packing|packaging\s*date|pre[-\s]?pack(?:ing|ed)?\s*date|"
            r"packed\s*(?:on|date)?|pkd\.?)\b"
        ),
        value_type="date",
        formats=("04/02/25", "04-02-2025", "02/2025"),
    ),
    "date_import": FieldSpec(
        field_id="date_import",
        canonical_name="Date of Import",
        aliases=("Import Date", "Date of Import", "Imported on", "Imported"),
        abbreviations=("IMP",),
        anchor_pattern=r"\b(?:import\s*date|date\s*of\s*import|imported\s*(?:on|date)?)\b",
        value_type="date",
    ),
    "date_expiry": FieldSpec(
        field_id="date_expiry",
        canonical_name="Expiry / Use By",
        aliases=(
            "Expiry Date", "Expiry", "Exp Date", "EXP", "EXP.", "Exp", "Exp.",
            "Use By", "Use by date", "Use Before", "Consume Before", "Consume before date",
        ),
        abbreviations=("EXP", "EXP.", "Exp", "Exp."),
        anchor_pattern=(
            r"\b(?:exp(?:iry)?\.?\s*date|expiry|exp\.?|use\s*by(?:\s*date)?|use\s*before|"
            r"consume\s*before)\b"
        ),
        value_type="date",
        formats=("04/02/26", "FEB 2026", "02/2026"),
        ocr_confusables=("B", "S"),
    ),
    "date_best_before": FieldSpec(
        field_id="date_best_before",
        canonical_name="Best Before",
        aliases=(
            "Best Before", "Best Before:", "Best Before End", "Best before date",
            "Best before 12 months from packaging", "BB", "B.B.",
        ),
        abbreviations=("BB", "B.B."),
        anchor_pattern=r"\bb(?:\.?\s*e\s*s\s*t)?\.?\s*before(?:\s*end)?\b|\bbest\s*before\b",
        value_type="date_or_duration",
        formats=("04/02/26", "12 MONTHS FROM PACKAGING", "9 months from manufacture"),
        notes="Duration form is stored as DURATION_FROM_{reference}, never as a literal date.",
    ),
    "batch_lot": FieldSpec(
        field_id="batch_lot",
        canonical_name="Batch / Lot Number",
        aliases=(
            "Batch No", "Batch No.", "Batch Number", "Batch", "Batch Code", "B.No", "B.No.",
            "Lot No", "Lot No.", "Lot Number", "Lot", "Lot Code", "Lot/Batch No", "Batch/Lot",
        ),
        abbreviations=("B.No", "B.No.", "Bch", "L.No"),
        anchor_pattern=r"\b(?:batch|lot)\s*(?:/|-)?\s*(?:b\.?\s*no\.?|code|number|no\.?)?\b\s*[:.\-]?",
        value_type="code",
        formats=("A0142", "B80131", "AB-1234", "LOT2025A", "24A01", "80130"),
        ocr_confusables=("O", "I", "S", "B", "G", "Z"),
        notes="Only values anchored by a batch/lot label; never arbitrary alphanumerics.",
    ),
    "fssai_license": FieldSpec(
        field_id="fssai_license",
        canonical_name="FSSAI Licence Number",
        aliases=(
            "FSSAI Lic No", "FSSAI Lic. No.", "FSSAI License No.", "FSSAI Licence No.",
            "FSSAI License", "FSSAI", "FSSAI Reg No", "Lic No", "Lic. No.", "License No",
            "Licence No", "Lic No.", "FSSAI no.",
        ),
        abbreviations=("FSSAI", "Lic No"),
        anchor_pattern=(
            r"\b(?:fssai(?:\s*lic(?:ence|ense)?\.?\s*n[o0]\.?)?|fssai\s*n[o0]?|"
            r"lic(?:ence|ense)?\.?\s*n[o0]\.?)\b\s*[:.\-]?"
        ),
        value_type="regulatory_id",
        formats=("12345678901234", "10012345678901"),
        ocr_confusables=("I", "O", "S", "B", "Z"),
        notes="14-digit structure expected; a random 14-digit number without the label is NOT FSSAI.",
    ),
    "consumer_care_phone": FieldSpec(
        field_id="consumer_care_phone",
        canonical_name="Consumer Care Phone",
        aliases=(
            "Consumer Care", "Customer Care", "Consumer care contact", "Customer Service",
            "Consumer Service", "Helpline", "Help Line", "Contact", "Contact Us",
            "Toll Free", "Toll Free No", "Consumer Complaints",
        ),
        abbreviations=("Ph", "Ph.", "Tel", "Tel.", "Mob", "Mob."),
        anchor_pattern=(
            r"\b(?:consumer\s*care|customer\s*care|consumer\s*(?:complaints?|service)|"
            r"customer\s*service|helpline|help\s*line|contact(?:\s*us)?|toll\s*free(?:\s*n[o0]\.?)?)\b"
        ),
        value_type="phone",
        formats=("9876543210", "044 2233 4455", "+91 98765 43210", "1800-123-4567", "1800-XXX-XXXX"),
    ),
    "consumer_care_email": FieldSpec(
        field_id="consumer_care_email",
        canonical_name="Consumer Care Email",
        aliases=("Consumer Care", "Customer Care", "Email", "E-mail", "E mail", "Mail"),
        abbreviations=(),
        anchor_pattern=r"\b(?:e-?mail|consumer\s*care|customer\s*care|contact)\b",
        value_type="email",
        formats=("abc@gmail.com", "abc_product@yahoo.com", "support@example.in"),
    ),
    "website": FieldSpec(
        field_id="website",
        canonical_name="Website",
        aliases=("Website", "Web", "Web Site", "Visit us at", "www"),
        abbreviations=(),
        anchor_pattern=r"\b(?:website|web\s*site|visit\s*us\s*at|www\.)\b",
        value_type="url",
        formats=("www.example.com", "https://example.com", "example.com"),
    ),
    "manufacturer": FieldSpec(
        field_id="manufacturer",
        canonical_name="Manufacturer Name",
        aliases=(
            "Manufactured by", "Manufactured & Marketed by", "Manufactured and Marketed by",
            "Manufactured for", "Manufactured at", "Mfd by", "Mfg by", "Mfd. by", "Mfg. by",
            "Maker", "Made by",
        ),
        abbreviations=("Mfd by", "Mfg by", "Mfgd by"),
        # NOTE: 'mfd. by' (dotted) is the single most common printed form on Indian labels, and the
        # extractor derives its role anchors from THIS pattern — so a form declared here can no
        # longer be missing from extraction (the dotted form was, and a legible manufacturer line
        # produced no entity at all as a result).
        anchor_pattern=(
            r"\b(?:manufactured\s*(?:&|and)?\s*(?:by|at|for)?|mfd\.?\s*by|mfgd?\.?\s*by|"
            r"manufacture|maker|made\s*by)\b"
        ),
        value_type="entity",
        notes="The anchor is a LABEL; the entity is the text following it. Never store the anchor.",
    ),
    "packer": FieldSpec(
        field_id="packer",
        canonical_name="Packer Name",
        aliases=("Packed by", "Packaged by", "Packer", "Pre-packed by", "Repacked by", "Packed & Marketed by"),
        abbreviations=("Pkd by", "Pkd. by"),
        # 'Packed & Marketed by' is one of the most common Indian label wordings for a packer line;
        # the optional 'marketed' keeps the ANCHOR intact so the entity is read as the entity rather
        # than as "Marketed by <entity>" (which the residual-role strip would then have to repair).
        anchor_pattern=r"\b(?:packed\s*(?:&|and)?\s*(?:marketed\s*)?(?:by|at)?|packer|pre[-\s]?packed\s*by|repacked\s*by|packaged\s*by)\b",
        value_type="entity",
        notes="Same anchor-vs-entity rule as manufacturer.",
    ),
    "importer": FieldSpec(
        field_id="importer",
        canonical_name="Importer Name",
        aliases=("Imported by", "Importer", "Imported & Packed by", "Imported and Marketed by"),
        abbreviations=(),
        anchor_pattern=r"\b(?:imported\s*(?:&|and)?\s*(?:by|packed|marketed)?|importer)\b",
        value_type="entity",
        notes="Same anchor-vs-entity rule as manufacturer.",
    ),
    "marketer": FieldSpec(
        field_id="marketer",
        canonical_name="Marketer Name",
        aliases=("Marketed by", "Marketed & Packed by", "Distributed by", "Distributed & Marketed by"),
        abbreviations=(),
        anchor_pattern=r"\b(?:marketed\s*(?:&|and)?\s*(?:by|packed)?|distributed\s*(?:&|and)?\s*(?:by|marketed)?)\b",
        value_type="entity",
        notes="Marketer is a distinct legal role; promoted to manufacturer only when no manufacturer/packer/importer entity exists.",
    ),
    "country_of_origin": FieldSpec(
        field_id="country_of_origin",
        canonical_name="Country of Origin",
        aliases=("Country of Origin", "Origin", "Made in", "Product of", "Produce of"),
        abbreviations=(),
        anchor_pattern=r"\b(?:country\s*of\s*origin|made\s*in|product\s*of|produce\s*of)\b\s*[:]?",
        value_type="text",
    ),
    "brand": FieldSpec(
        field_id="brand",
        canonical_name="Brand",
        aliases=("Brand", "Brand Name", "Mark", "TM"),
        abbreviations=(),
        anchor_pattern=r"\bbrand(?:\s*name)?\b\s*[:]?",
        value_type="entity",
        notes="Without an explicit label, brand requires multi-token-title inference — never a blind copy of product_name.",
    ),
    "product_name": FieldSpec(
        field_id="product_name",
        canonical_name="Product Name",
        aliases=("Product", "Product Name", "Name"),
        abbreviations=(),
        anchor_pattern=r"\bproduct(?:\s*name)?\b\s*[:]?",
        value_type="text",
    ),
    "common_name": FieldSpec(
        field_id="common_name",
        canonical_name="Common / Generic Name",
        aliases=("Common Name", "Generic Name", "Name of Commodity", "Commodity", "Name of Food"),
        abbreviations=(),
        anchor_pattern=r"\b(?:common|generic)\s*name\b|\bname\s*of\s*(?:commodity|food)\b|\bcommodity\b",
        value_type="text",
    ),
}


def get_spec(field_id: str) -> FieldSpec | None:
    return FIELD_SCHEMA.get(field_id)


def all_specs() -> dict[str, FieldSpec]:
    return dict(FIELD_SCHEMA)


def anchor_regex(field_id: str) -> re.Pattern | None:
    """Compiled anchor regex from the schema (shared by extractors and tests)."""
    spec = FIELD_SCHEMA.get(field_id)
    if spec is None:
        return None
    try:
        return re.compile(spec.anchor_pattern, re.IGNORECASE)
    except re.error:
        return None


def variation_count_estimate() -> int:
    """Conservative count of schema-driven label×format×alias combinations per field family.

    This counts only schema-declared representations; the synthetic generator multiplies
    these by layout/OCR-corruption/image-condition dimensions (see
    scripts/generate_variations.py) to reach the 10M+ combinatorial space.
    """
    total = 1
    for spec in FIELD_SCHEMA.values():
        n = len(spec.aliases) + len(spec.abbreviations) + len(spec.formats) + 1
        total *= max(n, 2)
    return total
