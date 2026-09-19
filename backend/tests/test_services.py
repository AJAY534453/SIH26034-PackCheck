"""Service tests: normalization, quality, end-to-end pipeline on a real rendered label."""
from __future__ import annotations

import pytest


def test_normalize_mrp():
    from backend.normalization.service import normalize_mrp

    n = normalize_mrp("200.00")
    assert n["value"] == 200.0 and n["currency"] == "INR" and "200" in n["display"]


def test_normalize_quantity_roundtrip():
    from backend.normalization.service import normalize_quantity

    import json

    n = normalize_quantity(json.dumps({"value": "500", "unit": "g", "quantity_type": "MASS"}))
    assert n["display"] == "500 g" and n["quantity_type"] == "MASS"


def test_normalize_date_ambiguous_flag():
    from backend.normalization.service import normalize_date

    n = normalize_date("2025-02-04|AMBIGUOUS")
    assert n["ambiguous"] is True and n["display"] == "2025-02-04" and n["iso"] == "2025-02-04"


def test_normalize_website():
    from backend.normalization.service import normalize_website

    n = normalize_website("WWW.VedhaProducts.com")
    assert n["valid"] is True and n["value"] == "www.vedhaproducts.com"


def test_quality_flags_dark_image():
    import cv2
    import numpy as np

    from backend.preprocessing.quality import assess_quality

    dark = np.full((400, 400, 3), 10, dtype=np.uint8)
    q = assess_quality(dark)
    assert q["metrics"]["brightness"] == "POOR"
    assert q["status"] in ("POOR", "UNUSABLE")


def test_quality_flags_blur():
    import cv2
    import numpy as np

    from backend.preprocessing.quality import assess_quality

    img = np.full((800, 800, 3), 200, dtype=np.uint8)
    cv2.circle(img, (400, 400), 100, (20, 20, 20), -1)
    img = cv2.GaussianBlur(img, (31, 31), 10)
    q = assess_quality(img)
    assert q["metrics"]["blur"] in ("POOR", "ACCEPTABLE")


def test_quality_good_on_text_label():
    from PIL import Image, ImageDraw, ImageFont

    import numpy as np

    from backend.preprocessing.quality import assess_quality

    img = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 30)
    except Exception:
        font = ImageFont.load_default()
    for i in range(10):
        d.text((40, 30 + i * 50), "Net Wt 500 g MRP Rs 200 Test Label", fill="black", font=font)
    arr = np.array(img)[:, :, ::-1].copy()
    q = assess_quality(arr)
    # a clean rendered label must not be flagged UNUSABLE/POOR
    assert q["status"] in ("GOOD", "ACCEPTABLE")


@pytest.mark.slow
def test_e2e_pipeline_real_ocr():
    """Real OCR pipeline on a rendered label (skipped with -m 'not slow')."""
    import os

    from PIL import Image, ImageDraw, ImageFont

    import numpy as np

    from backend.ocr import recognize
    from backend.extraction import run_extraction, pick_winner

    img = Image.new("RGB", (760, 700), "white")
    d = ImageDraw.Draw(img)

    def F(size):
        try:
            return ImageFont.truetype("arial.ttf", size)
        except Exception:
            return ImageFont.load_default()

    f36, f24 = F(36), F(24)
    d.text((30, 20), "KRACKJACK BISCUITS", fill="black", font=f36)
    d.text((30, 80), "Net Wt. 500 g", fill="black", font=f24)
    d.text((30, 130), "MRP Rs. 200", fill="black", font=f24)
    d.text((30, 430), "Manufactured by ABC Foods Pvt Ltd", fill="black", font=f24)
    arr = np.array(img)[:, :, ::-1].copy()

    lines = recognize(arr, "original")
    assert len(lines) >= 3, "OCR must recognize the rendered label"
    res = run_extraction({1: lines})
    assert pick_winner(res["mrp"])[0].value == "200"
    assert pick_winner(res["net_quantity"])[0].value.startswith('{"value": "500"')
