"""Product classification: suggests a category from OCR evidence with honest confidence.

The inspector always confirms or changes the category — AI suggestion is never blindly trusted.

Keyword matching is WORD-BOUNDED, never substring. Substring matching produced false food signals
from unrelated words ('Unit Sale Price' contains 'rice', 'TOILET' contains 'oil'), which pushed a
toilet soap into a CONFLICTING food-ish classification — and a classification is what drives which
regulatory requirements are even considered. A category may only be suggested by a whole word (or a
whole multi-word phrase) that the label actually prints.
"""
from __future__ import annotations

import re

from backend.ocr.base import OcrLine

_KEYWORD_CACHE: dict[str, re.Pattern] = {}


def _keyword_re(keyword: str) -> re.Pattern:
    """Whole-word (or whole-phrase) matcher for a category keyword.

    Multi-word keywords match across any whitespace run, so 'energy protein' also matches the
    two-column print 'Energy   Protein'; the separator is OPTIONAL so that OCR-fused print
    ('TOILETSOAP', 'BESTBEFORE') is recognised too. A leading/trailing non-alphanumeric guard stops
    a keyword from matching inside a longer token ('rice' in 'Price', 'oil' in 'TOILET', 'led' in
    'settled') — which is also what keeps the fused form safe: 'toilet soap' matches 'toiletsoap'
    while the single word 'soap' still cannot match inside 'toiletsoapgr'.
    """
    key = (keyword or "").strip().lower()
    cached = _KEYWORD_CACHE.get(key)
    if cached is None:
        body = r"\s*".join(re.escape(part) for part in key.split())
        cached = re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")
        _KEYWORD_CACHE[key] = cached
    return cached

# Category indicator keywords. Extensible — no product is hardcoded.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "FOOD": [
        "biscuit", "cookies", "snack", "namkeen", "chips", "chocolate", "tea", "coffee", "flour", "atta",
        "rice", "pulse", "dal", "spice", "masala", "salt", "sugar", "honey", "juice", "sauce", "ketchup",
        "noodles", "pasta", "oil", "ghee", "butter", "cheese", "milk", "dairy", "wafers", "candy", "toffee",
        "fssai", "nutrition", "energy protein", "best before", "confectionery", "food",
    ],
    "BEVERAGE": ["beverage", "drink", "water", "soda", "juice", "cola", "tea", "coffee", "liquid", "ml bottle"],
    "COSMETIC": [
        "cosmetic", "soap", "shampoo", "cream", "lotion", "deodorant", "perfume", "toothpaste", "toothbrush",
        "lipstick", "powder", "gel", "face wash", "hair oil", "conditioner",
        # The phrasing most Indian toiletry packs actually print. It is a two-word keyword rather
        # than the single word 'soap' because print is routinely fused ('62% ToiletSoapGr.3'), and
        # only the phrase can be recognised across that fusion without matching inside a longer
        # token.
        "toilet soap", "bathing soap", "ayurvedic soap",
    ],
    "HOUSEHOLD": [
        "detergent", "cleaner", "disinfectant", "phenyl", "room freshener", "repellent", "mosquito",
        "dishwash", "toilet cleaner", "floor cleaner", "agarbatti", "candle", "matchbox",
    ],
    "GARMENT": ["cotton", "polyester", "shirt", "t-shirt", "trouser", "saree", "fabric", "wash care", "size", "apparel"],
    "ELECTRICAL": [
        "bulb", "led", "watt", "voltage", "battery", "cable", "switch", "charger", "extension", "lamp",
        "lumens", "appliance",
    ],
}

CATEGORY_ORDER = list(CATEGORY_KEYWORDS.keys())


def classify_from_ocr(all_text: str, declared_category: str | None = None) -> dict:
    """Classify product category from accumulated OCR text.

    Returns {category, state, confidence, signals}. If declared_category is provided by the
    inspector it is respected, but OCR signals are still reported.
    """
    text = (all_text or "").lower()
    scores: dict[str, int] = {}
    signals: dict[str, list[str]] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if _keyword_re(kw).search(text):
                scores[category] = scores.get(category, 0) + 1
                signals.setdefault(category, []).append(kw)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return {
            "category": (declared_category or "OTHER"),
            "state": "UNCERTAIN",
            "confidence": 0.0,
            "signals": "no category keywords found in OCR text",
        }
    top_cat, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    state = "DETECTED"
    confidence = min(0.95, 0.45 + 0.1 * top_score)
    if second_score >= max(1, top_score - 1) and top_score < 4:
        state = "CONFLICTING"
        confidence = min(0.6, 0.3 + 0.08 * top_score)
    signals_str = "; ".join(f"{c}: {', '.join(kws[:6])}" for c, kws in signals.items())
    return {
        "category": top_cat,
        "state": state,
        "confidence": round(confidence, 3),
        "signals": signals_str,
    }
