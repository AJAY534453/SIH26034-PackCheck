"""Test fixtures: isolated temp database per test run (no pollution of real storage)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Isolated DB + storage for the whole test session (set before importing backend.config)
_tmp = tempfile.mkdtemp(prefix="pocket_test_")
os.environ["POCKET_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["POCKET_STORAGE_DIR"] = _tmp


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client):
    # include_token=True: these tests exercise the header/Bearer path. The browser app never asks
    # for a token (it authenticates with the HttpOnly cookie), which the auth tests assert directly.
    r = client.post("/auth/login", json={"username": "inspector", "password": "inspector123", "include_token": True})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def admin_headers(client):
    r = client.post("/auth/login", json={"username": "admin", "password": "admin123", "include_token": True})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def viewer_headers(client):
    r = client.post("/auth/login", json={"username": "viewer", "password": "viewer123", "include_token": True})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def _label_png():
    """A small valid PNG for upload tests."""
    from PIL import Image

    import io

    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, "PNG")
    buf.seek(0)
    return buf.getvalue()
