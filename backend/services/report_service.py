"""PDF report generation (ReportLab). Distinguishes AI extraction from legal evaluation from human decision.

Never fabricates content: every row comes from stored data.
"""
from __future__ import annotations

import html
import uuid
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import (
    Evidence,
    ExtractedField,
    Inspection,
    InspectionImage,
    Report,
    ReviewAction,
    RuleEvaluation,
    Violation,
)

STATUS_COLORS = {
    "COMPLIANT": colors.HexColor("#166534"),
    "NON_COMPLIANT": colors.HexColor("#991b1b"),
    "NEEDS_MANUAL_REVIEW": colors.HexColor("#92400e"),
    "PASS": colors.HexColor("#166534"),
    "FAIL": colors.HexColor("#991b1b"),
    "UNCERTAIN": colors.HexColor("#92400e"),
    "NOT_APPLICABLE": colors.HexColor("#64748b"),
}


def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("PTitle", parent=ss["Title"], fontSize=22, textColor=colors.HexColor("#1e3a5f")),
        "subtitle": ParagraphStyle("PSub", parent=ss["Normal"], fontSize=11, textColor=colors.HexColor("#475569"), alignment=1),
        "h2": ParagraphStyle("PH2", parent=ss["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1e3a5f")),
        "body": ParagraphStyle("PBody", parent=ss["BodyText"], fontSize=9, leading=12),
        "small": ParagraphStyle("PSmall", parent=ss["BodyText"], fontSize=8, leading=10, textColor=colors.HexColor("#64748b")),
        "disclaimer": ParagraphStyle("PDisc", parent=ss["BodyText"], fontSize=8, leading=10, textColor=colors.HexColor("#92400e")),
    }


# A4 (210 mm) minus the 18 mm side margins = 174 mm of usable width. Every column width is
# scaled to this budget so a table can never be wider than the page. The previous hard-coded
# widths summed to 190 mm, pushing the last column past the sheet edge.
USABLE_WIDTH = 174 * mm


def _cell_styles() -> tuple[ParagraphStyle, ParagraphStyle]:
    head = ParagraphStyle(
        "CellHead", fontName="Helvetica-Bold", fontSize=8, leading=10,
        textColor=colors.white, splitLongWords=1,
    )
    body = ParagraphStyle(
        "CellBody", fontName="Helvetica", fontSize=8, leading=10.5, splitLongWords=1,
    )
    return head, body


def _cell(value: object, style: ParagraphStyle) -> Paragraph:
    """A table cell that WRAPS.

    ReportLab only wraps text inside a Paragraph flowable. A plain string is laid out on a single
    line, so long declarations, long URLs and multi-line values either overflowed the column or
    collided with the neighbouring cell. Every cell therefore flows through here: the text is
    XML-escaped (so printed ``&``/``<``/``>`` can never break the build) and explicit newlines
    become ``<br/>``, letting the row grow downward with the content instead of overlapping.
    """
    text = "" if value is None else str(value)
    safe = xml_escape(text).replace("\r\n", "\n").replace("\n", "<br/>")
    return Paragraph(safe, style)


def _table(data: list[list[str]], widths: list[float] | None = None, header: bool = True) -> Table:
    """Build a wrapping, auto-height table.

    No fixed row/cell height is ever set: a row is exactly as tall as its tallest wrapped cell.
    ``repeatRows=1`` re-prints the header on every page and ReportLab splits between rows (never
    through the middle of one), so a table spanning several pages keeps its layout. Column
    widths are normalised to the usable page width so no column is clipped.
    """
    head_style, body_style = _cell_styles()
    wrapped: list[list[Paragraph]] = []
    for r, row in enumerate(data):
        style = head_style if (header and r == 0) else body_style
        wrapped.append([_cell(c, style) for c in row])

    # Always constrain the width, even with no hints: otherwise ReportLab auto-sizes columns
    # from content and a single long token widens the table past the page edge.
    cols = max((len(row) for row in wrapped), default=1)
    effective = list(widths) if widths else [1.0] * cols
    if len(effective) != cols:
        effective = [1.0] * cols
    total = float(sum(effective)) or 1.0
    col_widths = [w * (USABLE_WIDTH / total) for w in effective]

    t = Table(wrapped, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")))
    t.setStyle(TableStyle(style))
    return t


def generate_report(db: Session, inspection_id: int, generated_by: str) -> Report:
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise ValueError("Inspection not found")

    # product display name from the extracted product_name field (may be absent)
    product_name = ""
    pf = (
        db.query(ExtractedField)
        .filter(ExtractedField.inspection_id == inspection_id, ExtractedField.field_name == "product_name")
        .first()
    )
    if pf:
        product_name = pf.display_value

    settings.ensure_dirs()
    S = _styles()
    story: list = []

    # ---------- cover ----------
    story.append(Spacer(1, 30 * mm))
    story.append(Paragraph("POCKET", S["title"]))
    story.append(Paragraph("AI-Assisted Legal Metrology Inspection System", S["subtitle"]))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Legal Metrology Inspection Report", S["h2"]))
    decision = inspection.final_decision or "PENDING REVIEW"
    color = STATUS_COLORS.get(decision, colors.black)
    decision_style = ParagraphStyle("PDec", parent=S["h2"], textColor=color, fontSize=15)
    # The automated verdict and the human final decision are printed as two separate lines: the
    # report must never let an AI preliminary result read as a final official determination.
    story.append(Paragraph(f"AI Preliminary Verdict: {decision}", decision_style))
    official = inspection.official_decision
    official_style = ParagraphStyle(
        "POfficial", parent=S["h2"], fontSize=13, textColor=colors.HexColor("#1e3a5f")
    )
    story.append(Paragraph(
        f"Official Final Decision: {official if official else 'NOT YET FINALIZED'}", official_style
    ))
    if not official:
        from backend.models.product_scan import PENDING_MESSAGE

        story.append(Paragraph(PENDING_MESSAGE, S["small"]))
    story.append(Spacer(1, 5 * mm))
    story.append(_table(
        [
            ["Inspection ID", inspection.inspection_number],
            ["Date", str(inspection.created_at)],
            ["Inspector", inspection.inspector or "—"],
            ["Product", product_name or "—"],
            ["Category", f"{inspection.category or '—'} ({inspection.category_state or '—'})"],
            ["Image Quality", f"{inspection.overall_quality or '—'} ({inspection.overall_quality_score:.0%})"],
        ],
        widths=[45 * mm, 110 * mm],
        header=False,
    ))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        "This is an AI-assisted inspection support document. It does not replace physical inspection by an "
        "authorized Legal Metrology officer. Image evidence cannot prove physical quantity, the actual sale "
        "price charged, or authenticity; a letter height in millimetres is only reported when a scale "
        "calibration has been recorded, and the calibration used is printed with the result.", S["disclaimer"]))
    story.append(PageBreak())

    # ---------- automated compliance review (AI preliminary) ----------
    from backend.models import ProductScan
    from backend.services import compliance_service

    review = compliance_service.build_ai_review(db, inspection)
    scan = db.query(ProductScan).filter(ProductScan.inspection_id == inspection.id).first()
    review_status = scan.review_status if scan else "NOT_EVALUATED"
    official_decision = scan.official_decision if scan else inspection.official_decision
    story.append(Paragraph("1. Automated Compliance Review (AI Preliminary)", S["h2"]))
    story.append(Paragraph(
        "Scoring: PASS = full credit, UNCERTAIN = half credit, FAIL = no credit; critical requirements "
        "carry double weight; not-applicable and manual-only checks are excluded from the denominator. "
        "The threshold decides whether the automated review may issue a pass-oriented PRELIMINARY verdict "
        "or must be flagged for official finalization.", S["small"]))
    story.append(_table(
        [
            ["Weighted compliance score", f"{review['score']}%"],
            ["Decision threshold", f"{review['threshold']:g}%"],
            ["AI preliminary verdict", review["verdict"]],
            ["Review status", review_status],
            ["Official final decision", official_decision or "NOT YET FINALIZED"],
            ["Recommended action", review["recommended_action"]],
            ["Summary", review["summary"]],
        ],
        widths=[52 * mm, 103 * mm],
        header=False,
    ))
    story.append(Spacer(1, 2 * mm))
    check_rows = [["Rule", "Requirement", "Result", "Required", "Detected", "Legal reference"]]
    for c in review["checks"]:
        required = "" if c.get("required_value") is None else f"{c['required_value']} mm"
        detected = "" if c.get("detected_value") is None else f"{c['detected_value']} mm"
        check_rows.append([
            c["rule_number"], c["title"], c["status"], required, detected, c.get("legal_reference") or "",
        ])
    story.append(_table(check_rows, widths=[18*mm, 40*mm, 20*mm, 18*mm, 18*mm, 41*mm]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        f"Checks: {review['counts']['pass']} passed, {review['counts']['fail']} failed, "
        f"{review['counts']['uncertain']} unverified, {review['counts']['not_applicable']} not applicable, "
        f"{review['counts']['manual_only']} manual-only. Every check above cites the versioned rule "
        "version it was evaluated against.", S["small"]))

    # ---------- Rule 7 measurement (only when the check ran) ----------
    font_check = next((c for c in review["checks"] if c.get("check_type") == "font_size"), None)
    if font_check is not None:
        detail = font_check.get("detail") or {}
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("1a. Rule 7 Letter-Height Measurement", S["h3"] if "h3" in S else S["h2"]))
        story.append(_table(
            [
                ["Result", font_check["status"]],
                ["Required minimum", "unavailable" if detail.get("required_mm") is None else f"{detail['required_mm']} mm"],
                ["Detected (smallest declaration)", "unavailable" if detail.get("detected_mm") is None else f"{detail['detected_mm']} mm"],
                ["Legal reference", detail.get("required_reference") or font_check.get("legal_reference") or ""],
                ["Panel area (Rule 7(4))", "unavailable" if detail.get("panel_area_cm2") is None else f"{detail['panel_area_cm2']} cm2 ({detail.get('panel_area_source')})"],
                ["Scale calibration", detail.get("px_per_mm_source") or "unavailable"],
                ["Measurement method", detail.get("method") or ""],
                ["Exemption", detail.get("exempt_note") or ""],
            ],
            widths=[52 * mm, 103 * mm],
            header=False,
        ))
        measurements = detail.get("measurements") or []
        if measurements:
            story.append(Spacer(1, 1.5 * mm))
            story.append(_table(
                [["Declaration", "Text", "Letter height", "Line bbox height", "Width ratio"]]
                + [
                    [
                        m.get("field_name", ""), (m.get("text") or "")[:60],
                        f"{m.get('letter_height_mm')} mm", f"{m.get('line_height_mm')} mm",
                        "" if m.get("width_ratio") is None else str(m["width_ratio"]),
                    ]
                    for m in measurements
                ],
                widths=[34*mm, 56*mm, 24*mm, 24*mm, 17*mm],
            ))
        story.append(Paragraph(font_check["explanation"], S["small"]))
    story.append(PageBreak())

    # ---------- images ----------
    images = db.query(InspectionImage).filter(InspectionImage.inspection_id == inspection.id).all()
    story.append(Paragraph("2. Package Images (Original Evidence)", S["h2"]))
    if images:
        img_rows = [["Role", "File", "Resolution", "Quality", "Score", "OCR Lines"]]
        for img in images:
            img_rows.append([
                img.role, img.original_filename or "", f"{img.width}x{img.height}",
                img.quality_status or "—", f"{img.quality_score:.0%}", str(img.ocr_line_count),
            ])
        story.append(_table(img_rows, widths=[26*mm, 52*mm, 24*mm, 24*mm, 18*mm, 20*mm]))
        story.append(Spacer(1, 4 * mm))
        shown = 0
        for img in images:
            p = settings.STORAGE_DIR / "originals" / img.stored_filename
            if p.exists() and shown < 4:
                try:
                    story.append(RLImage(str(p), width=70*mm, height=52*mm))
                    story.append(Spacer(1, 2 * mm))
                    shown += 1
                except Exception:
                    pass
    else:
        story.append(Paragraph("No images attached.", S["body"]))

    # ---------- extracted fields ----------
    story.append(Paragraph("3. Extracted Declarations (AI Extraction Layer)", S["h2"]))
    story.append(Paragraph(
        "State meanings: DETECTED = extracted from supplied images with evidence; MISSING = not found "
        "in the SUPPLIED images (does NOT imply absence from the package); UNCERTAIN = extraction "
        "unreliable or unparseable; CONFLICTING = multiple values detected (retained for review); "
        "HUMAN_CONFIRMED_ABSENT = inspector confirmed absence after manual examination.",
        S["small"]))
    fields = db.query(ExtractedField).filter(ExtractedField.inspection_id == inspection.id).all()
    if fields:
        rows = [["Field", "Value", "Confidence", "State", "Source", "Conflict"]]
        for f in sorted(fields, key=lambda x: x.field_name):
            # Confidence is only meaningful for an actually extracted value; missing/empty
            # fields must not display a numeric confidence.
            has_value = bool((f.display_value or "").strip())
            conf_cell = f"{f.confidence:.0%}" if has_value and f.state != "HUMAN_CONFIRMED_ABSENT" else "—"
            rows.append([
                f.field_name, f.display_value or "—", conf_cell, f.state,
                f"{f.source_engine or f.source}", f.conflict_status,
            ])
        story.append(_table(rows, widths=[34*mm, 62*mm, 18*mm, 30*mm, 26*mm, 20*mm]))
    else:
        story.append(Paragraph("No fields extracted.", S["body"]))

    # ---------- rule evaluations ----------
    story.append(Paragraph("4. Rule Evaluations (Deterministic Engine)", S["h2"]))
    evals = db.query(RuleEvaluation).filter(RuleEvaluation.inspection_id == inspection.id).all()
    if evals:
        rows = [["Rule", "Requirement", "Result", "Reason"]]
        for e in evals:
            color = STATUS_COLORS.get(e.status, colors.black)
            rows.append([
                f"{e.rule_number}\n{e.title}", e.expected or "",
                e.status, e.reason or "",
            ])
        t = _table(rows, widths=[40*mm, 60*mm, 22*mm, 68*mm])
        story.append(t)
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("Rule versions used: each evaluation is pinned to the rule version in the database "
                               "at evaluation time; later rule edits do not alter this report.", S["small"]))
    else:
        story.append(Paragraph("No rule evaluations recorded.", S["body"]))

    # ---------- violations ----------
    story.append(Paragraph("5. Potential Violations", S["h2"]))
    violations = db.query(Violation).filter(Violation.inspection_id == inspection.id).all()
    if violations:
        story.append(Paragraph(
            "Status meanings: OPEN = evidence-backed potential violation pending human confirmation; "
            "NEEDS_REVIEW = manual verification required (NOT a violation — the declaration could not "
            "be verified from the supplied images); CONFIRMED = confirmed by a reviewer.",
            S["small"]))
        rows = [["Rule", "Title", "Severity", "Confidence", "Status", "Description"]]
        for v in violations:
            rows.append([v.rule_number, v.title, v.severity, f"{v.confidence:.0%}", v.status, v.description or ""])
        story.append(_table(rows, widths=[18*mm, 36*mm, 18*mm, 20*mm, 22*mm, 76*mm]))
    else:
        story.append(Paragraph("No potential violations identified from the evaluated evidence.", S["body"]))

    # ---------- manual review ----------
    story.append(Paragraph("6. Manual Review Actions (Human Decision Layer)", S["h2"]))
    reviews = db.query(ReviewAction).filter(ReviewAction.inspection_id == inspection.id).all()
    if reviews:
        rows = [["Time", "Reviewer", "Action", "Original", "Corrected", "Reason"]]
        for r in reviews:
            rows.append([str(r.created_at)[:19], r.reviewer, r.action,
                         r.original_value or "", r.corrected_value or "", r.reason or ""])
        story.append(_table(rows, widths=[28*mm, 22*mm, 20*mm, 26*mm, 26*mm, 68*mm]))
    else:
        story.append(Paragraph("No manual review actions recorded.", S["body"]))

    # ---------- reason for status ----------
    story.append(Paragraph("7. Reason for Status", S["h2"]))
    story.append(Paragraph(
        f"Finalization: {('recorded by ' + inspection.finalized_by + ' on ' + str(inspection.finalized_at)) if inspection.official_decision else 'not yet finalized by an authorized official'}",
        S["small"]))
    if inspection.finalization_remarks:
        story.append(Paragraph(f"Finalization remarks: {inspection.finalization_remarks}", S["small"]))
    story.append(Paragraph(inspection.summary or "Not yet reviewed.", S["body"]))

    # ---------- evidence note ----------
    evidence_count = db.query(Evidence).filter(Evidence.inspection_id == inspection.id).count()
    story.append(Paragraph(f"Evidence records stored for this inspection: {evidence_count} "
                           f"(viewable in the application with bounding boxes and crops).", S["small"]))

    # ---------- write file ----------
    stored_name = f"{uuid.uuid4().hex}.pdf"
    out_path = settings.STORAGE_DIR / "reports" / stored_name
    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm)
    doc.build(story)

    report = Report(inspection_id=inspection.id, format="pdf", stored_filename=stored_name, generated_by=generated_by)
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


# --------------------------------------------------------------------------- HTML report
#
# The application's PDF is produced by ReportLab (above). This HTML view renders the SAME stored
# rows as a semantic table so the browser can print/save it as a PDF — and so a table with long
# dynamic JSON/CSV-derived content is guaranteed to wrap instead of overlapping. The CSS below
# is the contract: no fixed row/cell height, no nowrap, no overflow clipping.

_REPORT_CSS = """
* { box-sizing: border-box; }
body { font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif; color: #1c1c1a;
       margin: 24px; font-size: 13px; line-height: 1.45; }
h1 { font-size: 20px; margin: 0 0 2px; }
h2 { font-size: 14px; margin: 22px 0 8px; border-bottom: 1px solid #e4e4e1; padding-bottom: 4px; }
.sub { color: #71716c; margin: 0 0 4px; }
.meta { display: grid; grid-template-columns: 170px 1fr; gap: 2px 12px; margin: 12px 0 4px; font-size: 12.5px; }
.meta dt { color: #71716c; }
.meta dd { margin: 0; overflow-wrap: anywhere; }
.note { color: #b54708; font-size: 11.5px; margin: 10px 0 0; }

/* --- the wrapping table contract (long text grows the row, never overlaps) --- */
.pdf-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
  margin: 8px 0 16px;
}
.pdf-table thead { display: table-header-group; }   /* repeat header on every printed page */
.pdf-table tr { display: table-row; height: auto; break-inside: avoid; page-break-inside: avoid; }
.pdf-table td,
.pdf-table th {
  height: auto;
  min-height: 0;
  white-space: normal;
  overflow-wrap: anywhere;
  word-wrap: break-word;
  word-break: normal;
  vertical-align: top;
  line-height: 1.4;
  padding: 6px 8px;
  border: 1px solid #cbd5e1;
  font-size: 11.5px;
}
.pdf-table th { background: #1e3a5f; color: #fff; text-align: left; font-weight: 600; }
.pdf-table td.empty { color: #71716c; }
@media print {
  body { margin: 12mm; }
  h2 { break-after: avoid; page-break-after: avoid; }
}
"""


def _e(value: object) -> str:
    """HTML-escape a stored value; empty values render as an explicit dash."""
    text = "" if value is None else str(value)
    if not text.strip():
        return '<span class="empty">—</span>'
    return html.escape(text)


def render_report_html(db: Session, inspection_id: int) -> str:
    """Render an inspection report as a self-contained, print-ready HTML document."""
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise ValueError("Inspection not found")

    def rows(headers: list[str], body: list[list[object]]) -> str:
        head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
        if not body:
            body_html = f'<tr><td class="empty" colspan="{len(headers)}">None recorded.</td></tr>'
        else:
            body_html = "".join(
                "<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in body
            )
        return (
            '<table class="pdf-table"><thead><tr>'
            + head
            + "</tr></thead><tbody>"
            + body_html
            + "</tbody></table>"
        )

    product_name = ""
    pf = (
        db.query(ExtractedField)
        .filter(ExtractedField.inspection_id == inspection_id, ExtractedField.field_name == "product_name")
        .first()
    )
    if pf:
        product_name = pf.display_value

    fields_body = [
        [f.field_name, f.display_value, (f"{f.confidence:.0%}" if (f.display_value or "").strip() else "—"),
         f.state, f.source_engine or f.source, f.conflict_status, f.uncertainty_reason]
        for f in sorted(
            db.query(ExtractedField).filter(ExtractedField.inspection_id == inspection_id).all(),
            key=lambda x: x.field_name,
        )
    ]
    rules_body = [
        [e.rule_number, e.title, e.status, e.reason, e.expected, e.observed]
        for e in db.query(RuleEvaluation).filter(RuleEvaluation.inspection_id == inspection_id).all()
    ]
    violations_body = [
        [v.rule_number, v.title, v.severity, v.status, f"{v.confidence:.0%}", v.description]
        for v in db.query(Violation).filter(Violation.inspection_id == inspection_id).all()
    ]
    reviews_body = [
        [str(r.created_at)[:19], r.reviewer, r.action, r.original_value, r.corrected_value, r.reason]
        for r in db.query(ReviewAction).filter(ReviewAction.inspection_id == inspection_id).all()
    ]
    images = db.query(InspectionImage).filter(InspectionImage.inspection_id == inspection_id).all()
    images_body = [
        [img.role, img.original_filename, f"{img.width}x{img.height}", img.quality_status,
         f"{img.quality_score:.0%}", str(img.ocr_line_count)]
        for img in images
    ]
    evidence_count = db.query(Evidence).filter(Evidence.inspection_id == inspection_id).count()

    # Automated compliance review, rendered from the same stored rows the PDF uses.
    from backend.models import ProductScan
    from backend.services import compliance_service

    review = compliance_service.build_ai_review(db, inspection)
    scan = db.query(ProductScan).filter(ProductScan.inspection_id == inspection_id).first()
    official_decision = (scan.official_decision if scan else None) or inspection.official_decision
    review_rows = [
        ["Weighted compliance score", f"{review['score']}%"],
        ["Decision threshold", f"{review['threshold']:g}%"],
        ["AI preliminary verdict", review["verdict"]],
        ["Review status", scan.review_status if scan else "NOT_EVALUATED"],
        ["Official final decision", official_decision or "NOT YET FINALIZED"],
        ["Recommended action", review["recommended_action"]],
        ["Summary", review["summary"]],
    ]
    checks_body = [
        [
            c["rule_number"], c["title"], c["status"],
            "" if c.get("required_value") is None else f"{c['required_value']} mm",
            "" if c.get("detected_value") is None else f"{c['detected_value']} mm",
            c.get("legal_reference") or "", c.get("explanation") or "",
        ]
        for c in review["checks"]
    ]
    font_check = next((c for c in review["checks"] if c.get("check_type") == "font_size"), None)
    font_detail = (font_check or {}).get("detail") or {}

    decision = inspection.final_decision or "PENDING REVIEW"
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        f"<title>Inspection report — {html.escape(inspection.inspection_number)}</title>",
        f"<style>{_REPORT_CSS}</style></head><body>",
        "<h1>PACKCHECK AI — Inspection Report</h1>",
        '<p class="sub">AI-Assisted Legal Metrology Inspection System · SIH26034</p>',
        f"<h2>AI preliminary verdict: {html.escape(decision)}</h2>",
        f"<h2>Official final decision: {html.escape(official_decision or 'NOT YET FINALIZED')}</h2>",
        '<dl class="meta">',
        f"<dt>Inspection</dt><dd>{html.escape(inspection.inspection_number)}</dd>",
        f"<dt>Date</dt><dd>{_e(str(inspection.created_at))}</dd>",
        f"<dt>Inspector</dt><dd>{_e(inspection.inspector)}</dd>",
        f"<dt>Product</dt><dd>{_e(product_name)}</dd>",
        f"<dt>Category</dt><dd>{_e(inspection.category)} ({_e(inspection.category_state)})</dd>",
        f"<dt>Image quality</dt><dd>{_e(inspection.overall_quality)} ",
        f"({inspection.overall_quality_score:.0%})</dd>",
        "</dl>",
        f'<p class="note">Summary: {_e(inspection.summary)}</p>',
        "<h2>1. Package images</h2>",
        rows(["Role", "File", "Resolution", "Quality", "Score", "OCR lines"], images_body),
        "<h2>2. Extracted declarations</h2>",
        rows(["Field", "Value", "Confidence", "State", "Source", "Conflict", "Note"], fields_body),
        "<h2>3. Rule evaluations</h2>",
        rows(["Rule", "Title", "Result", "Reason", "Expected", "Observed"], rules_body),
        "<h2>4. Potential violations</h2>",
        rows(["Rule", "Title", "Severity", "Status", "Confidence", "Description"], violations_body),
        "<h2>5. Manual review actions</h2>",
        rows(["Time", "Reviewer", "Action", "Original", "Corrected", "Reason"], reviews_body),
        '<p class="note">This is an AI-assisted inspection support document and does not replace '
        "physical inspection by an authorized Legal Metrology officer. MISSING means “not found in "
        "the supplied images”, never proof of absence. "
        f"Evidence records stored: {evidence_count}.</p>",
        "</body></html>",
    ]
    # The compliance review and its Rule 7 measurement are inserted before the closing tag so the
    # section numbering of the extracted rows above stays stable.
    review_html = "".join([
        "<h2>Automated compliance review (AI preliminary)</h2>",
        '<p class="note">Scoring: PASS = full credit, UNCERTAIN = half credit, FAIL = no credit; '
        "critical requirements carry double weight; not-applicable and manual-only checks are excluded. "
        "The threshold decides whether the automated review may issue a pass-oriented PRELIMINARY "
        "verdict or must be flagged for official finalization.</p>",
        rows(["Field", "Value"], review_rows),
        "<h3>Requirement checks</h3>",
        rows(
            ["Rule", "Title", "Result", "Required", "Detected", "Legal reference", "Explanation"],
            checks_body,
        ),
        "<h3>Rule 7 letter-height measurement</h3>",
        rows(
            ["Field", "Value"],
            [
                ["Result", (font_check or {}).get("status", "not evaluated")],
                ["Required minimum", _plain(font_detail.get("required_mm"), " mm")],
                ["Detected (smallest declaration)", _plain(font_detail.get("detected_mm"), " mm")],
                ["Legal reference", font_detail.get("required_reference") or (font_check or {}).get("legal_reference")],
                ["Panel area (Rule 7(4))", _plain(font_detail.get("panel_area_cm2"), " cm2", suffix=f" ({font_detail.get('panel_area_source')})")],
                ["Scale calibration", font_detail.get("px_per_mm_source") or "unavailable"],
                ["Method", font_detail.get("method")],
                ["Rule 7(5) exemption note", font_detail.get("exempt_note")],
            ],
        ),
    ])
    parts = parts[:-2] + [review_html] + parts[-2:]
    return "".join(parts)


def _plain(value: object, unit: str = "", *, suffix: str = "") -> str:
    """Format a stored numeric measurement, or the honest 'not available' wording."""
    if value is None or value == "":
        return "not available"
    return f"{value}{unit}{suffix}"
