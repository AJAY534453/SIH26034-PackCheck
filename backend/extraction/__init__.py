"""Extraction package: typed field extractors + candidate scoring + conflict detection."""
from backend.extraction.engine import pick_winner, run_extraction

__all__ = ["run_extraction", "pick_winner"]
