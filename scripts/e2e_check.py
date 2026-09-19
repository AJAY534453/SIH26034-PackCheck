"""End-to-end pipeline verification: rendered label image -> full inspection -> decision.

This exercises the REAL pipeline (OCR + extraction + rules) on an image generated at runtime.
It is a verification script, not a fake demo: results come from actual OCR of the image.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252; force UTF-8 so ₹ prints correctly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def render_label(path: Path) -> None:
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
    d.text((30, 230), "MFD: 04/02/25", fill="black", font=f24)
    d.text((30, 280), "BEST BEFORE: 5 MONTHS", fill="black", font=f24)
    d.text((30, 330), "Batch No: A0130", fill="black", font=f24)
    d.text((30, 380), "FSSAI Lic. No. 10012345678901", fill="black", font=f24)
    d.text((30, 430), "Manufactured by ABC Foods Pvt Ltd", fill="black", font=f24)
    d.text((30, 480), "No. 19, Muthur Road, Vellakovil - 638111", fill="black", font=f24)
    d.text((30, 530), "Consumer Care: 1800-123-4567", fill="black", font=f24)
    img.save(path, "PNG")


def main() -> int:
    import os

    os.environ["POCKET_DATABASE_URL"] = "sqlite:///./storage/pocket.db"
    from fastapi.testclient import TestClient

    from backend.main import app

    label_path = Path("storage") / "demo_label.png"
    label_path.parent.mkdir(parents=True, exist_ok=True)
    render_label(label_path)

    with TestClient(app) as client:
        # include_token: this script is an API client, so it opts in to the bearer token. The
        # browser app receives no token in the body — it authenticates with the HttpOnly cookie.
        r = client.post(
            "/auth/login",
            json={"username": "inspector", "password": "inspector123", "include_token": True},
        )
        assert r.status_code == 200, r.text
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = client.post("/inspections", headers=h)
        assert r.status_code == 200, r.text
        insp_id = r.json()["id"]
        print(f"inspection created: {r.json()['inspection_number']} (id={insp_id})")

        with open(label_path, "rb") as fh:
            r = client.post(
                f"/inspections/{insp_id}/images",
                headers=h,
                data={"role": "BACK"},
                files={"file": ("demo_label.png", fh, "image/png")},
            )
        assert r.status_code == 200, r.text
        print(f"image uploaded: quality={r.json()['quality_status']} ({r.json()['quality_score']:.0%})")

        r = client.post(f"/inspections/{insp_id}/process", headers=h)
        assert r.status_code == 200, r.text
        print("process:", r.json())

        r = client.get(f"/inspections/{insp_id}", headers=h)
        assert r.status_code == 200, r.text
        detail = r.json()

        print("\n--- FIELDS ---")
        for f in detail["fields"]:
            print(f"  {f['field_name']:22s} {f['display_value'][:38]:40s} conf={f['confidence']:.0%} state={f['state']}")
        print("--- RULES ---")
        for ru in detail["rules"]:
            print(f"  {ru['rule_number']:10s} {ru['status']:14s} {ru['title'][:44]}")
        print("--- VIOLATIONS ---")
        for v in detail["violations"]:
            print(f"  {v['rule_number']:10s} {v['title'][:60]} severity={v['severity']}")
        print("--- DECISION ---")
        print(f"  {detail['final_decision']}")
        print(f"  {detail['summary'][:220]}")

        # --- evidence: every detected field must carry a usable source location ---
        print("--- EVIDENCE ---")
        with_evidence = [f for f in detail["fields"] if f["display_value"].strip()]
        ev_by_field = {}
        for ev in detail["evidence"]:
            ev_by_field.setdefault(ev["field_name"], []).append(ev)
        missing_evidence = [f["field_name"] for f in with_evidence if not ev_by_field.get(f["field_name"])]
        for f in with_evidence[:6]:
            ev = ev_by_field.get(f["field_name"], [{}])[0]
            print(f"  {f['field_name']:22s} bbox={ev.get('bbox', '—'):24s} raw={(ev.get('raw_text') or '—')[:30]}")
        if missing_evidence:
            print(f"  NOTE: no evidence row for: {', '.join(missing_evidence)}")
        else:
            print("  every detected field carries evidence: OK")

        # --- human review: manual correction + note are audited, original preserved ---
        print("--- HUMAN REVIEW ---")
        target = next((f for f in detail["fields"] if f["display_value"].strip()), None)
        if target is not None:
            r = client.post(
                f"/inspections/{insp_id}/review/field",
                headers=h,
                json={
                    "action": "EDIT_FIELD",
                    "field_name": target["field_name"],
                    "corrected_value": target["display_value"],
                    "reason": "E2E: re-affirming value read from the package",
                },
            )
            assert r.status_code == 200, r.text
            print(f"  field '{target['field_name']}' reviewed -> state={r.json()['state']}")

        r = client.post(
            f"/inspections/{insp_id}/review/note",
            headers=h,
            json={"note": "E2E: checked declarations against the uploaded images"},
        )
        assert r.status_code == 200, r.text
        print("  review note recorded")

        # --- violation lifecycle ---
        # Safety principle made executable: a declaration that OCR simply cannot read is NOT a
        # violation. The only path from image evidence to a rule FAIL is a human-confirmed absence
        # (positive evidence). This step exercises that path, then the dismiss/confirm review.
        detail = client.get(f"/inspections/{insp_id}", headers=h).json()
        print(f"  before: decision={detail['final_decision']} violations={len(detail['violations'])}")
        r = client.post(
            f"/inspections/{insp_id}/review/field",
            headers=h,
            json={
                "action": "CONFIRM_ABSENT",
                "field_name": "mrp",
                "reason": "E2E mechanism test: inspector reports MRP absent after physical examination",
            },
        )
        assert r.status_code == 200, r.text
        detail = client.get(f"/inspections/{insp_id}", headers=h).json()
        raised = [v for v in detail["violations"] if v["rule_number"] == "6(1)(e)"]
        assert raised, "human-confirmed absence must produce an evidence-backed violation"
        v0 = raised[0]
        print(f"  confirmed absence -> violation rule {v0['rule_number']} status={v0['status']} decision={detail['final_decision']}")

        r = client.post(
            f"/inspections/{insp_id}/review/violation/{v0['id']}",
            headers=h,
            json={"action": "DISMISS", "reason": "E2E: MRP is present and legible on the label"},
        )
        assert r.status_code == 200, r.text
        print(f"  violation rule {v0['rule_number']} dismissed -> decision={r.json()['decision']}")
        r = client.post(
            f"/inspections/{insp_id}/review/violation/{v0['id']}",
            headers=h,
            json={"action": "CONFIRM", "reason": "E2E: re-confirming the finding"},
        )
        assert r.status_code == 200, r.text
        print(f"  violation rule {v0['rule_number']} confirmed -> decision={r.json()['decision']}")
        # restore the honest state for the rest of the demo: the label does carry an MRP
        r = client.post(
            f"/inspections/{insp_id}/review/field",
            headers=h,
            json={"action": "ADD_FIELD", "field_name": "mrp", "corrected_value": "Rs. 200", "reason": "E2E: MRP present on the physical label"},
        )
        assert r.status_code == 200, r.text

        # --- final human decision ---
        r = client.post(
            f"/inspections/{insp_id}/review/final",
            headers=h,
            json={"decision": "NEEDS_MANUAL_REVIEW", "reason": "E2E: pending physical verification"},
        )
        assert r.status_code == 200, r.text
        print(f"  finalized -> {r.json()['decision']}")

        # --- reporting + exports + audit ---
        r = client.post(f"/reports/generate/{insp_id}", headers=h)
        assert r.status_code == 200, r.text
        print("\nreport generated:", r.json()["stored_filename"])

        r = client.get(f"/inspections/{insp_id}/export?format=csv", headers=h)
        assert r.status_code == 200 and r.text.strip()
        print("export csv: OK")

        r = client.get(f"/inspections/{insp_id}/export?format=json", headers=h)
        assert r.status_code == 200
        payload = r.json()
        print(f"export json: OK ({len(payload.get('fields', []))} fields, {len(payload.get('rules', []))} rules)")

        r = client.get(f"/inspections/{insp_id}", headers=h)
        actions = r.json()["review_actions"]
        kinds = sorted({a["action"] for a in actions})
        print(f"audit trail: {len(actions)} review action(s) -> {', '.join(kinds) or 'none'}")

        # --- evidence access is auth-gated ---
        # The session also lives in an HttpOnly cookie set by the login above, so clear the cookie
        # jar to exercise the genuinely anonymous case.
        stored = next((i["stored_filename"] for i in detail["images"]), None)
        if stored:
            client.cookies.clear()
            assert client.get(f"/files/originals/{stored}").status_code == 401
            assert client.get(f"/files/originals/{stored}", headers=h).status_code == 200
            print("evidence file access: auth enforced: OK")

        print("\nE2E PIPELINE: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
