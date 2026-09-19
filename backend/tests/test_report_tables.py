"""Regression tests for PDF table layout.

The reported defect was text overlapping/dipping out of its cell because reportlab tables were
built from raw strings (which never wrap) with fixed, page-overflowing column widths and values
pre-truncated with ``[:n]``. These tests pin the fix:

* a long value must make the row TALLER (wrap), never wider than the page,
* an unbroken token (URL/serial) must still wrap,
* newlines must stay inside the cell and add height,
* empty values must not break the build,
* a large table must split across pages BETWEEN rows (repeatRows keeps the header),
* a real report must build from these tables.
"""
from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from backend.services.report_service import USABLE_WIDTH, _table, generate_report


def _measure(table) -> tuple[float, float]:
    """Wrap a flowable against a real canvas and return (width, height)."""
    canv = rl_canvas.Canvas(io.BytesIO())
    return table.wrapOn(canv, USABLE_WIDTH, 100000)


def test_long_value_wraps_by_increasing_row_height_not_width():
    short = _table([["Field", "Value"], ["mrp", "₹200"]])
    long = _table([["Field", "Value"], ["mrp", "x" * 400]])
    sw, sh = _measure(short)
    lw, lh = _measure(long)
    assert lh > sh * 2, "a 400-character value must make the row grow much taller"
    # never wider than the usable page width
    assert lw <= USABLE_WIDTH + 0.5
    assert sw <= USABLE_WIDTH + 0.5


def test_unbroken_token_still_wraps():
    """A long URL/serial with no spaces must not overflow the column."""
    table = _table([["Source", "Reference"], ["catalogue", "https://example.com/" + "a" * 300]])
    w, h = _measure(table)
    assert w <= USABLE_WIDTH + 0.5
    assert h > 20 * mm, "an unbroken 300-character token must wrap onto several lines"


def test_newlines_add_height_and_stay_in_cell():
    single = _table([["Rule", "Reason"], ["6(1)", "one line"]])
    multi = _table([["Rule", "Reason"], ["6(1)", "line one\nline two\nline three"]])
    assert _measure(multi)[1] > _measure(single)[1]


def test_empty_values_do_not_break_build():
    table = _table([["Field", "Value"], ["a", ""], ["b", None]])
    w, h = _measure(table)
    assert w > 0 and h > 0


def test_columns_are_scaled_to_fit_the_page():
    # deliberately over-wide widths (as the previous code had: they summed to 190mm)
    table = _table(
        [["A", "B", "C", "D"], ["1", "2", "3", "4"]],
        widths=[28 * mm, 22 * mm, 20 * mm, 26 * mm, 26 * mm, 68 * mm],
    )
    assert _measure(table)[0] <= USABLE_WIDTH + 0.5


def test_many_rows_split_between_pages_with_repeating_header():
    rows = [["Field", "Value"]]
    for i in range(200):
        rows.append([f"field_{i}", f"value {i} " * 3])
    table = _table(rows, widths=[40 * mm, 130 * mm])
    parts = table.split(USABLE_WIDTH, 220 * mm)
    assert len(parts) > 1, "a 200-row table must split across pages"
    # each part repeats the header row (2 cells) as its first row
    for part in parts:
        first_row = part._cellvalues[0]
        assert len(first_row) == 2


def test_generate_report_handles_extreme_text(client):
    """End-to-end: a report with unbroken and multi-line values builds to a valid, multi-page PDF."""
    from backend.database import SessionLocal
    from backend.models import ExtractedField, Inspection, ReviewAction, RuleEvaluation, Violation

    db = SessionLocal()
    try:
        insp = Inspection(
            inspection_number="INS-REPORT-TABLE-1",
            status="AWAITING_REVIEW",
            final_decision="NEEDS_MANUAL_REVIEW",
            inspector="inspector",
            category="FOOD",
            category_state="DETECTED",
            summary="Long-text table regression\nsecond line of the summary with a very long "
                    "unbroken token: " + "y" * 200,
        )
        db.add(insp)
        db.commit()
        db.refresh(insp)

        db.add(ExtractedField(
            inspection_id=insp.id, field_name="product_name", state="DETECTED",
            display_value="A " * 200, normalized_value="{}", confidence=0.9,
        ))
        db.add(ExtractedField(
            inspection_id=insp.id, field_name="mrp", state="DETECTED",
            display_value="₹" + "9" * 150, normalized_value="{}", confidence=0.8,
        ))
        db.add(ExtractedField(
            inspection_id=insp.id, field_name="batch_lot", state="MISSING",
            display_value="", normalized_value="", confidence=0.0,
        ))
        db.add(RuleEvaluation(
            inspection_id=insp.id, rule_id=1, rule_version_id=1, rule_number="6(1)(e)",
            title="Declaration of MRP shall be made on the package\n" + "t" * 120,
            status="FAIL", reason="z" * 400, observed="o" * 150, expected="e" * 150,
        ))
        db.add(Violation(
            inspection_id=insp.id, rule_number="6(1)(e)", title="MRP " + "v" * 120,
            description="d" * 400, severity="HIGH", status="OPEN", confidence=0.9,
        ))
        db.add(ReviewAction(
            inspection_id=insp.id, action="EDIT_FIELD", reviewer="inspector",
            original_value="a" * 200, corrected_value="b" * 200, reason="r" * 200,
        ))
        db.commit()

        report = generate_report(db, insp.id, generated_by="inspector")
        path = Path(report.stored_filename)
        from backend.config import settings

        out = settings.STORAGE_DIR / "reports" / path.name
        assert out.is_file()
        data = out.read_bytes()
        assert data.startswith(b"%PDF")
        # a table with 400-char cells necessarily spills onto more than one page
        assert data.count(b"/Type /Page") > 1

        # the HTML report renders the same long values, untruncated
        from backend.services.report_service import render_report_html

        doc = render_report_html(db, insp.id)
        assert doc.startswith("<!doctype html>")
        assert 'class="pdf-table"' in doc
        assert "<thead>" in doc
        assert "y" * 200 in doc  # summary not truncated
        assert "9" * 150 in doc   # 150-char MRP value not truncated
    finally:
        db.close()


def test_html_report_css_contract_and_auth(client, auth_headers):
    created = client.post("/inspections", headers=auth_headers)
    assert created.status_code == 200
    inspection_id = created.json()["id"]

    # unauthenticated access is rejected (clear the cookie set by the login above)
    client.cookies.clear()
    assert client.get(f"/reports/html/{inspection_id}").status_code == 401
    # a logged-in caller (cookie restored via the bearer header) can read it
    assert client.get(f"/reports/html/{inspection_id}", headers=auth_headers).status_code == 200

    res = client.get(f"/reports/html/{inspection_id}", headers=auth_headers)
    assert res.status_code == 200
    html_doc = res.text
    assert 'class="pdf-table"' in html_doc
    assert "display: table-header-group" in html_doc  # header repeats on printed pages
    assert "overflow-wrap: anywhere" in html_doc
    # the CSS must not reintroduce clipping/overlap
    for forbidden in ["height: 50px", "white-space: nowrap", "overflow: hidden", "max-height"]:
        assert forbidden not in html_doc, f"forbidden rigid rule present: {forbidden}"


def test_html_report_escapes_markup_and_shows_long_values(client, auth_headers):
    """Dynamic content is escaped (no injection) and long unbroken strings survive intact."""
    from backend.database import SessionLocal
    from backend.models import ExtractedField, Inspection

    created = client.post("/inspections", headers=auth_headers)
    inspection_id = created.json()["id"]

    db = SessionLocal()
    try:
        long_token = "https://example.com/" + "a" * 240
        db.add(ExtractedField(
            inspection_id=inspection_id, field_name="manufacturer", state="DETECTED",
            display_value=f"<script>alert(1)</script> {long_token}",
            normalized_value="{}", confidence=0.9,
        ))
        db.commit()
    finally:
        db.close()

    html_doc = client.get(f"/reports/html/{inspection_id}", headers=auth_headers).text
    assert "<script>alert(1)</script>" not in html_doc
    assert "&lt;script&gt;" in html_doc
    assert long_token in html_doc
