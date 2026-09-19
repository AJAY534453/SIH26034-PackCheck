"""Applicability engine — decides which rules apply BEFORE any validation runs.

Never assumes every rule applies to every package. When classification is UNCERTAIN or
CONFLICTING, conditional rules become UNCERTAIN rather than silently applying exemptions.

Two kinds of "not applicable" are deliberately distinguished, because they are NOT the same thing:

``INAPPLICABLE``     the law, the packaging scope or a CONFIRMED category puts the requirement
                     outside this package — legitimately excluded from the compliance maths.
``EVIDENCE_MISSING`` the package may well be in scope, but no image evidence established it (an
                     import-only rule with no import evidence, or an undetermined/conflicting
                     category). NOT a legal exemption: the scoring layer counts these as
                     unverified, so an unreadable label can never silently raise a percentage.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.models import RuleVersion

KIND_APPLIES = "APPLIES"
KIND_INAPPLICABLE = "INAPPLICABLE"
KIND_EVIDENCE_MISSING = "EVIDENCE_MISSING"


@dataclass
class ApplicabilityResult:
    applies: bool
    reason: str
    kind: str = KIND_APPLIES


def evaluate_applicability(rv: RuleVersion, context: dict) -> ApplicabilityResult:
    """Determine whether a rule version applies given inspection context.

    context keys: category, category_state (DETECTED/CONFIRMED/UNCERTAIN/CONFLICTING),
    declared_quantity (dict|None), is_import_evidence (bool), packaging ('retail_package'|...).
    """
    spec = rv.applicability or {}

    # import-only rules: require positive import evidence; otherwise NOT applicable.
    if spec.get("import_only"):
        if context.get("is_import_evidence"):
            return ApplicabilityResult(
                True, "import evidence present (importer declaration detected)", KIND_APPLIES
            )
        return ApplicabilityResult(
            False,
            "no import evidence in images; import-specific rule not applicable",
            KIND_EVIDENCE_MISSING,
        )

    # packaging scope
    packaging = spec.get("packaging", "any")
    if packaging != "any" and context.get("packaging", "retail_package") != packaging:
        return ApplicabilityResult(
            False,
            f"rule applies to {packaging}; this inspection is {context.get('packaging')}",
            KIND_INAPPLICABLE,
        )

    # category scope
    cats = spec.get("category_any_of")
    if cats:
        category = (context.get("category") or "").upper()
        cat_state = (context.get("category_state") or "UNCERTAIN").upper()
        if cat_state in ("UNCERTAIN", "CONFLICTING"):
            return ApplicabilityResult(
                False,
                f"product category is {cat_state}; category-specific applicability cannot be established "
                "from image evidence (manual verification required)",
                KIND_EVIDENCE_MISSING,
            )
        if category not in [c.upper() for c in cats]:
            return ApplicabilityResult(
                False,
                f"rule applies to categories {cats}; package classified as {category}",
                KIND_INAPPLICABLE,
            )
        return ApplicabilityResult(
            True, f"package category {category} matches applicable categories {cats}", KIND_APPLIES
        )

    if spec.get("conditional"):
        return ApplicabilityResult(
            True, "conditionally applicable based on package context (review if uncertain)", KIND_APPLIES
        )

    return ApplicabilityResult(
        True, "generally applicable to retail packaged commodities", KIND_APPLIES
    )
