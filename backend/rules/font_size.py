"""Rule 7 font-size requirement lookup — pure, data-driven, no hard-coded legal numbers.

The minimum heights live in the versioned rule definition
(``backend/rules/definitions/lm_pcr_2011_rule7.json``, check_type ``font_size``), which transcribes
Table-I and Table-II of Rule 7 of the Legal Metrology (Packaged Commodities) Rules, 2011 as
substituted by G.S.R. 629(E) dated 23.06.2017. This module only *selects* from that data, so an
amendment is a JSON edit — never a code change.

Selection rules implemented here:

* which table applies — Table-I when the net quantity is declared by weight or volume,
  Table-II when it is declared by length, area or number (Rule 7(2));
* which bracket applies — by the area of the principal display panel in cm² (Rule 7(4) defines
  how that area is determined);
* which column applies — ``moulded_mm`` when the declaration is blown/formed/moulded into the
  container surface, else ``normal_mm``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TABLE_FOR_FAMILY = {
    "MASS": "table_i",
    "VOLUME": "table_i",
    "LENGTH": "table_ii",
    "AREA": "table_ii",
    "NUMBER": "table_ii",
}

# Serial numbers are cited in the human-readable reference so an officer can check the report
# against the printed table.
_TABLE_CAPTION = {"table_i": "Table-I", "table_ii": "Table-II"}


@dataclass
class FontRequirement:
    """The applicable minimum text height for one package."""

    table_id: str            # "Table-I" | "Table-II"
    table_key: str           # "table_i" | "table_ii"
    serial: int
    bracket_label: str
    form: str                # "normal" | "moulded"
    required_mm: float
    # The requirement of the adjacent (strictly lower) bracket. Used to keep a verdict honest when
    # the panel area itself is an estimate that could sit either side of a bracket boundary.
    lower_bracket_mm: float
    area_cm2: float

    @property
    def reference(self) -> str:
        form_note = " (blown/formed/molded surface column)" if self.form == "moulded" else ""
        return (
            f"Rule 7(2), {self.table_id} serial {self.serial} — {self.bracket_label}"
            f": minimum {self.required_mm} mm{form_note}"
        )

    def as_dict(self) -> dict:
        return {
            "table_id": self.table_id,
            "serial": self.serial,
            "bracket_label": self.bracket_label,
            "form": self.form,
            "required_mm": self.required_mm,
            "lower_bracket_mm": self.lower_bracket_mm,
            "area_cm2": self.area_cm2,
            "reference": self.reference,
        }


def _bracket_for(table: dict, area_cm2: float) -> tuple[dict, dict | None]:
    """Return (matching bracket, next-smaller bracket) for a panel area."""
    brackets = sorted(table.get("brackets", []), key=lambda b: float(b.get("min_area_cm2", 0)))
    chosen = brackets[0] if brackets else {}
    prev: dict | None = None
    for b in brackets:
        lo = float(b.get("min_area_cm2", 0))
        hi = b.get("max_area_cm2")
        if area_cm2 > lo and (hi is None or area_cm2 <= float(hi)):
            chosen = b
            break
        prev = b
    else:
        # area above the last bracket's lower bound
        if brackets and area_cm2 > float(brackets[-1].get("min_area_cm2", 0)):
            chosen = brackets[-1]
            prev = brackets[-2] if len(brackets) > 1 else None
    if chosen is brackets[0]:
        prev = None
    return chosen, prev


def _height(bracket: dict, form: str) -> float:
    key = "moulded_mm" if form == "moulded" else "normal_mm"
    return float(bracket.get(key, bracket.get("normal_mm", 0.0)))


def requirement_for(
    params: dict,
    *,
    quantity_family: str,
    area_cm2: float,
    form: str = "normal",
) -> FontRequirement | None:
    """Select the applicable Rule 7 minimum height, or ``None`` when it cannot be determined.

    ``quantity_family`` is a ``backend.models.enums.UnitType`` value (MASS/VOLUME/LENGTH/AREA/
    NUMBER); anything else means the table itself cannot be selected.
    """
    table_key = TABLE_FOR_FAMILY.get((quantity_family or "").upper())
    if table_key is None:
        return None
    table = (params or {}).get(table_key)
    if not table or not table.get("brackets"):
        return None
    form = "moulded" if (form or "").lower() in ("moulded", "molded", "blown", "formed") else "normal"
    bracket, prev = _bracket_for(table, float(area_cm2))
    if not bracket:
        return None
    required = _height(bracket, form)
    lower = _height(prev, form) if prev else 0.0
    return FontRequirement(
        table_id=_TABLE_CAPTION.get(table_key, table.get("id", "")),
        table_key=table_key,
        serial=int(bracket.get("serial", 0)),
        bracket_label=str(bracket.get("label", "")),
        form=form,
        required_mm=required,
        lower_bracket_mm=lower,
        area_cm2=float(area_cm2),
    )


def requirement_table(params: dict, quantity_family: str, form: str = "normal") -> dict:
    """The full applicable table, for display in the UI / report (traceability of the decision)."""
    table_key = TABLE_FOR_FAMILY.get((quantity_family or "").upper())
    if table_key is None:
        return {}
    table = (params or {}).get(table_key) or {}
    form = "moulded" if (form or "").lower() in ("moulded", "molded", "blown", "formed") else "normal"
    out = {
        "id": table.get("id", _TABLE_CAPTION.get(table_key, "")),
        "title": table.get("title", ""),
        "form_column": "moulded_mm" if form == "moulded" else "normal_mm",
        "rows": [
            {
                "serial": b.get("serial"),
                "label": b.get("label"),
                "minimum_mm": _height(b, form),
            }
            for b in table.get("brackets", [])
        ],
    }
    return out


def all_tables(params: dict) -> list[dict]:
    """Every Rule 7 table with both columns — what the Rule Library page shows."""
    out = []
    for key in ("table_i", "table_ii"):
        table = (params or {}).get(key) or {}
        if not table:
            continue
        out.append(
            {
                "id": table.get("id", _TABLE_CAPTION.get(key, key)),
                "title": table.get("title", ""),
                "rows": [
                    {
                        "serial": b.get("serial"),
                        "label": b.get("label"),
                        "normal_mm": b.get("normal_mm"),
                        "moulded_mm": b.get("moulded_mm"),
                    }
                    for b in table.get("brackets", [])
                ],
            }
        )
    return out
