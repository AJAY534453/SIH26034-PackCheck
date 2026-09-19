"""Image-analysis tests: enhancement modes, text/no-text/low-confidence states, structure, API."""
from __future__ import annotations

import numpy as np
import pytest

from backend.ocr.base import OcrLine
from backend.preprocessing.enhance import enhance_full_image, enhance_text_image
from backend.services import analysis_service as svc


def _img(h: int = 400, w: int = 600) -> np.ndarray:
    return np.full((h, w, 3), 240, dtype=np.uint8)


def _line(text: str, conf: float = 0.9, bbox=(10, 10, 200, 40)) -> OcrLine:
    return OcrLine(text=text, confidence=conf, bbox=bbox, engine="test", variant="original")


# ---------------------------------------------------------------- enhancement modes

def test_full_enhancement_preserves_colour_and_reports_ops():
    img = _img()
    out, ops = enhance_full_image(img)
    assert out.ndim == 3, "full-image enhancement keeps colour channels"
    assert out is not img
    assert ops and any("contrast" in o for o in ops)


def test_text_enhancement_is_binarised_grayscale_and_returns_scale():
    img = _img()
    out, ops, scale = enhance_text_image(img)
    assert out.ndim == 2, "text-focused enhancement is grayscale"
    assert out.shape[0] >= img.shape[0] and out.shape[1] >= img.shape[1]
    assert scale > 0
    assert any("threshold" in o for o in ops)
    # a binarised image only contains black/white values
    assert set(np.unique(out).tolist()).issubset({0, 255})


# ---------------------------------------------------------------- states (OCR stubbed)

def test_no_text_state(monkeypatch):
    monkeypatch.setattr(svc, "recognize", lambda image, variant="original": [])
    result = svc.analyze_image(_img())
    assert result["state"] == svc.STATE_NO_TEXT
    assert result["message"] == "No readable text detected in this image."
    assert result["text_present"] is False
    assert result["structured"] == {}
    assert result["raw_text"] == ""


def test_low_confidence_text_is_flagged_and_values_are_uncertain(monkeypatch):
    lines = [_line("MRP 200.00", 0.42, (10, 10, 200, 40))]
    monkeypatch.setattr(svc, "recognize", lambda image, variant="original": lines if variant == "original" else [])
    result = svc.analyze_image(_img())
    assert result["state"] == svc.STATE_LOW_CONFIDENCE
    assert result["text_present"] is True
    # nothing is asserted as DETECTED when the reading confidence is low
    assert all(v["state"] != "DETECTED" for v in result["structured"].values())


def test_good_text_is_structured(monkeypatch):
    lines = [
        _line("M.R.P. Rs. 200.00", 0.95, (10, 10, 220, 40)),
        _line("Net Wt. 500 g", 0.93, (10, 50, 220, 80)),
        _line("Batch No. B1234", 0.9, (10, 90, 220, 120)),
    ]
    monkeypatch.setattr(svc, "recognize", lambda image, variant="original": lines if variant == "original" else [])
    result = svc.analyze_image(_img())
    assert result["state"] == svc.STATE_TEXT
    assert result["line_count"] == 3
    assert "mrp" in result["structured"], result["structured"]
    assert result["structured"]["mrp"]["value"].strip()
    assert result["raw_text"].count("\n") >= 2


def test_enhanced_images_are_produced_even_without_text(monkeypatch):
    monkeypatch.setattr(svc, "recognize", lambda image, variant="original": [])
    result = svc.analyze_image(_img())
    assert result["_images"]["full"].ndim == 3
    assert result["_images"]["text"].ndim == 2
    assert result["full_enhancement"]["ops"]
    assert result["text_enhancement"]["ops"]


# ---------------------------------------------------------------- API

def _png_with_text() -> bytes:
    import cv2

    img = np.full((300, 700, 3), 255, dtype=np.uint8)
    cv2.putText(img, "M.R.P. Rs. 250.00", (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
    cv2.putText(img, "Net Wt. 500 g", (20, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_analysis_endpoint_stores_original_and_enhancements(client, auth_headers):
    r = client.post("/analysis", headers=auth_headers,
                    data={"mode": "auto"}, files={"file": ("label.png", _png_with_text(), "image/png")})
    assert r.status_code == 200, r.text
    a = r.json()["analysis"]
    assert a["state"] in {svc.STATE_TEXT, svc.STATE_LOW_CONFIDENCE, svc.STATE_NO_TEXT}
    assert a["original_url"] and a["enhanced_full_url"] and a["enhanced_text_url"]
    assert "original" in a["original_url"]
    assert a["engine"]

    # the original + both derived artifacts are served through the protected file route
    for url in (a["original_url"], a["enhanced_full_url"], a["enhanced_text_url"]):
        assert client.get(url, headers=auth_headers).status_code == 200

    # and are NOT public
    client.cookies.clear()
    assert client.get(a["original_url"]).status_code == 401
    assert client.get(a["enhanced_text_url"]).status_code == 401

    # it is retrievable by id and listed
    got = client.get(f"/analysis/{a['id']}", headers=auth_headers)
    assert got.status_code == 200 and got.json()["id"] == a["id"]
    listed = client.get("/analysis", headers=auth_headers).json()["items"]
    assert any(x["id"] == a["id"] for x in listed)


def test_analysis_rejects_bad_mode_and_empty_file(client, auth_headers):
    assert client.post("/analysis", headers=auth_headers, data={"mode": "nonsense"},
                       files={"file": ("x.png", _png_with_text(), "image/png")}).status_code == 422
    assert client.post("/analysis", headers=auth_headers,
                       files={"file": ("x.png", b"", "image/png")}).status_code == 422


def test_analysis_requires_the_permission(client):
    # a read-only viewer cannot run an analysis
    token = client.post("/auth/login", json={"username": "viewer", "password": "viewer123", "include_token": True}).json()["access_token"]
    r = client.post("/analysis", headers={"Authorization": f"Bearer {token}"},
                    files={"file": ("x.png", _png_with_text(), "image/png")})
    assert r.status_code == 403
    # the regulated entity CAN (it is a capture tool, not a compliance decision)
    ent = client.post("/auth/login", json={"username": "entity", "password": "entity123", "include_token": True}).json()["access_token"]
    r2 = client.post("/analysis", headers={"Authorization": f"Bearer {ent}"},
                     files={"file": ("x.png", _png_with_text(), "image/png")})
    assert r2.status_code == 200
