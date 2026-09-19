"""Grocery tracker: compact card data, product-detail endpoint, ownership isolation."""
from __future__ import annotations

import uuid


def _login(client, username, password):
    return client.post(
        "/auth/login",
        json={"username": username, "password": password, "include_token": True},
    ).json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_grocery_card_carries_category_and_detail_endpoint(client, auth_headers):
    created = client.post(
        "/grocery", headers=auth_headers,
        json={"product_name": "Card Test Product", "category": "FOOD", "quantity": "500 g", "mrp": "120"},
    )
    assert created.status_code == 200, created.text
    item = created.json()["item"]
    assert item["category"] == "FOOD"

    listed = client.get("/grocery", headers=auth_headers).json()["items"]
    card = next(i for i in listed if i["id"] == item["id"])
    assert "image_url" in card and "inspection_number" in card

    detail = client.get(f"/grocery/{item['id']}", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["item"]["id"] == item["id"]
    # product section exists even without a linked scan (honest empty structures)
    assert body["product"]["fields"] == []
    assert body["product"]["scan"] is None


def test_grocery_detail_404_and_auth(client, auth_headers):
    assert client.get("/grocery/999999", headers=auth_headers).status_code == 404
    client.cookies.clear()
    assert client.get("/grocery/1").status_code == 401


def test_grocery_detail_respects_ownership(client):
    """A non-privileged user cannot read another user's tracked product."""
    other = f"ent_{uuid.uuid4().hex[:8]}"
    from backend.database import SessionLocal
    from backend.models import Organization, User
    from backend.security import hash_password

    db = SessionLocal()
    try:
        org = Organization(name=f"Grocery Org {uuid.uuid4().hex[:6]}", kind="BUSINESS")
        db.add(org)
        db.commit()
        db.refresh(org)
        if db.query(User).filter(User.username == other).first() is None:
            db.add(User(username=other, full_name=other, role="REGULATED_ENTITY",
                        organization_id=org.id, password_hash=hash_password("Password123")))
            db.commit()
    finally:
        db.close()

    owner = _login(client, "viewer", "viewer123")
    item_id = client.post("/grocery", headers=_bearer(owner), json={"product_name": "Private Item"}).json()["item"]["id"]

    # a different, non-privileged account cannot see it (and gets a 403, not the data)
    other_token = _login(client, other, "Password123")
    r = client.get(f"/grocery/{item_id}", headers=_bearer(other_token))
    assert r.status_code in (403, 404)
