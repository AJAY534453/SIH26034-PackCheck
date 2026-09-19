"""Inspection service: orchestrates the complete pipeline for one inspection.

UPLOAD VALIDATION → ORIGINAL PRESERVATION → QUALITY → PREPROCESSING VARIANTS → OCR
→ REGION OCR (crop re-check) → CANDIDATE GENERATION → FIELD EXTRACTION → NORMALIZATION
→ CANDIDATE SCORING → CROSS-IMAGE FUSION → CONFLICT DETECTION → CLASSIFICATION
→ RULE APPLICABILITY → RULE VALIDATION → VIOLATION ANALYSIS → EVIDENCE → DECISION.

Every stage updates inspection.stage_status so the UI shows live progress. Any stage failure
degrades safely (partial results retained, status FAILED with reason) — it never crashes silently
and never fabricates downstream results.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from backend import audit
from backend.classification import classify_from_ocr
from backend.config import settings
from backend.extraction import pick_winner, run_extraction
from backend.models import (
    Classification,
    Evidence,
    ExtractedField,
    FieldCandidate,
    Inspection,
    InspectionImage,
    OcrResult,
    RuleEvaluation,
    Violation,
    VisionRegion,
)
from backend.models.enums import (
    FieldState,
    InspectionStatus,
    RuleStatus,
    Severity,
    ViolationStatus,
)
from backend.normalization.service import normalize_field
from backend.normalization.validation import validate_field
from backend.normalization.validators import corroborate_email_domain
from backend.ocr import best_engine_name, ocr_available
from backend.ocr import recognize, recognize_multivariant
from backend.ocr.base import OcrLine
from backend.preprocessing.enhance import build_variants
from backend.rules.applicability import evaluate_applicability
from backend.rules.placement import build_panel_evidence
from backend.rules.decision import decide_inspection
from backend.rules.engine import evaluate_rule
from backend.rules.registry import get_active_versions
from backend.services.evidence_service import create_evidence, save_crop
from backend.services.image_service import validate_and_store, UploadValidationError
from backend.version import ENGINE_VERSION

STAGES = [
    "upload_validation",
    "original_preservation",
    "quality_assessment",
    "preprocessing",
    "text_detection_ocr",
    "region_ocr",
    "visual_analysis",
    "provider_extraction",
    "candidate_generation",
    "field_extraction",
    "normalization",
    "classification",
    "measurement",
    "rule_applicability",
    "rule_validation",
    "evidence_generation",
    "inspection_result",
    "compliance_review",
]

# Declaration fields that always get an explicit row. A field with no extracted winner is
# recorded with state=MISSING and confidence=0 — NOT omitted, and never given a fake value.
# MISSING means "not found in the SUPPLIED images", never "absent from the package".
TRACKED_DECLARATION_FIELDS = [
    "product_name", "brand", "common_name",
    "manufacturer", "manufacturer_address", "packer", "packer_address", "importer", "importer_address",
    "country_of_origin", "net_quantity", "mrp", "unit_sale_price",
    "date_manufacturing", "date_packing", "date_import", "date_expiry", "date_best_before",
    "batch_lot", "consumer_care_phone", "consumer_care_email", "website", "fssai_license",
]

MISSING_NOT_FOUND_REASON = (
    "Not found in the supplied images by OCR/extraction. This does NOT imply the declaration is "
    "absent from the package — it may appear on an unphotographed side or have been missed. "
    "Manual verification required."
)


def _set_stage(inspection: Inspection, db: Session, stage: str, state: str) -> None:
    inspection.set_stage(stage, state)
    db.commit()


def _timing(inspection: Inspection, db: Session, stage: str, started: float) -> int:
    """Record the wall-clock milliseconds this stage took on THIS run.

    Stored on the record itself so a slow scan can be diagnosed from the inspection, not from
    server logs — and so the progress UI can show real elapsed time instead of a spinner.
    """
    ms = int((time.perf_counter() - started) * 1000)
    timings = dict(inspection.stage_timings or {})
    timings[stage] = ms
    inspection.stage_timings = timings
    db.commit()
    return ms


def add_image(db: Session, inspection: Inspection, data: bytes, filename: str, role: str) -> InspectionImage:
    """Validate + store an uploaded image and attach it to the inspection."""
    existing = db.query(InspectionImage).filter(InspectionImage.inspection_id == inspection.id).all()
    hashes = [i.phash for i in existing if i.phash]
    try:
        info = validate_and_store(data, filename, hashes)
    except UploadValidationError as e:
        audit.log_action(inspection.inspector, "image_upload_rejected", inspection.inspection_number, reason=str(e))
        raise
    image = InspectionImage(
        inspection_id=inspection.id,
        role=role or "ADDITIONAL_EVIDENCE",
        original_filename=filename,
        stored_filename=info["stored_filename"],
        mime_type=info["mime_type"],
        size_bytes=info["size_bytes"],
        width=info["width"],
        height=info["height"],
        phash=info["phash"],
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    audit.log_action(inspection.inspector, "image_uploaded", inspection.inspection_number, after=filename)
    return image


def _ocr_image(db: Session, inspection: Inspection, image: InspectionImage) -> list:
    """Run multi-variant OCR on one image; store per-line results."""
    path = settings.STORAGE_DIR / "originals" / image.stored_filename
    import cv2

    img = cv2.imread(str(path))
    if img is None:
        return []
    variants = build_variants(img, max_variants=2 if image.quality_status in ("GOOD", "ACCEPTABLE") else 3)
    lines = recognize_multivariant(img, variants)
    for i, line in enumerate(lines):
        db.add(
            OcrResult(
                image_id=image.id,
                engine=line.engine,
                variant=line.variant,
                line_index=i,
                text=line.text,
                confidence=line.confidence,
                bbox=line.bbox_str,
            )
        )
    image.ocr_text = "\n".join(l.text for l in lines)
    image.ocr_line_count = len(lines)
    db.commit()
    return lines


def _region_recheck(img, lines: list) -> list:
    """Second-pass OCR over tight crops around promising regions (e.g. currency-looking bboxes)."""
    if not ocr_available() or not lines:
        return []
    extra = []
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    for line in lines:
        # re-OCR lines containing currency-like tokens to verify them at higher effective resolution
        if not any(tok in line.text.upper() for tok in ("₹", "RS", "MRP", "NET", "MFD", "EXP", "FSSAI")):
            continue
        x1, y1, x2, y2 = line.bbox
        pad_x, pad_y = int((x2 - x1) * 0.15) + 6, int((y2 - y1) * 0.5) + 6
        cx1, cy1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        cx2, cy2 = min(w, x2 + pad_x), min(h, y2 + pad_y)
        crop = img[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            continue
        crop = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        recheck = recognize(crop, variant="region_crop")
        for rl in recheck:
            extra.append(
                type(rl)(
                    text=rl.text,
                    confidence=rl.confidence * 0.98,
                    bbox=(cx1 + rl.bbox[0] // 2, cy1 + rl.bbox[1] // 2, cx1 + rl.bbox[2] // 2, cy1 + rl.bbox[3] // 2),
                    engine=rl.engine,
                    variant="region_crop",
                )
            )
    return extra


def process_inspection(db: Session, inspection_id: int, refresh_ocr: bool = False) -> Inspection:
    """Run the complete pipeline for an inspection. Safe to re-run (reprocess).

    `refresh_ocr` forces the recogniser to run again over every uploaded image. By default the
    OCR evidence already stored for the image is reused, so reprocessing is deterministic (same
    evidence in, same declarations out) and does not repeat the pipeline's slowest step.
    """
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise ValueError("Inspection not found")

    inspection.status = InspectionStatus.PROCESSING.value
    inspection.stage_status = {}
    inspection.stage_timings = {}
    inspection.vision_status = ""
    inspection.vision_engine = ""
    inspection.vision_note = ""
    inspection.provider_status = ""
    inspection.provider_note = ""
    inspection.duration_ms = 0
    inspection.final_decision = None
    inspection.summary = ""
    db.commit()
    t_total = time.perf_counter()

    try:
        images = db.query(InspectionImage).filter(InspectionImage.inspection_id == inspection.id).all()

        # ---------- quality assessment ----------
        _set_stage(inspection, db, "quality_assessment", "running")
        t_quality = time.perf_counter()
        quality_results = []
        for image in images:
            path = settings.STORAGE_DIR / "originals" / image.stored_filename
            q = _quality_for_file(path)
            image.quality_score = q["score"]
            image.quality_status = q["status"]
            image.quality_metrics = q
            quality_results.append(q)
        overall_score = sum(q["score"] for q in quality_results) / len(quality_results) if quality_results else 0.0
        inspection.overall_quality_score = overall_score
        overall_quality = (
            max((q["status"] for q in quality_results), key=lambda s: ["GOOD", "ACCEPTABLE", "POOR", "UNUSABLE"].index(s))
            if quality_results
            else "UNUSABLE"
        )
        inspection.overall_quality = overall_quality
        _set_stage(inspection, db, "quality_assessment", "done")
        _timing(inspection, db, "quality_assessment", t_quality)

        # mark early stages done (they were performed during upload)
        for stage in ("upload_validation", "original_preservation"):
            inspection.set_stage(stage, "done")
        db.commit()

        if not images:
            inspection.status = InspectionStatus.FAILED.value
            inspection.set_stage("text_detection_ocr", "skipped")
            inspection.summary = "No images attached to this inspection. Upload package images and process again."
            db.commit()
            return inspection

        if not ocr_available():
            inspection.status = InspectionStatus.FAILED.value
            inspection.set_stage("text_detection_ocr", "failed")
            inspection.duration_ms = int((time.perf_counter() - t_total) * 1000)
            inspection.summary = (
                "OCR engine is unavailable in this environment. No results were fabricated; "
                "install the OCR engine and reprocess."
            )
            db.commit()
            audit.log_action(inspection.inspector, "processing_failed", inspection.inspection_number, reason="OCR unavailable")
            return inspection

        # ---------- on-device vision, phase 1 (pixels only — submitted BEFORE OCR starts) ----------
        # The vision engine needs neither OCR nor the network, so its pixel phase is handed to a
        # worker thread and collected once the recogniser has finished. That overlap is the
        # pipeline's one genuine parallelism win: a three-image scan pays for vision and OCR
        # concurrently instead of end to end. No database session is touched on that thread.
        from concurrent.futures import ThreadPoolExecutor

        from backend.ai.provider import provider_status
        from backend.vision import (
            KIND_PANEL, KIND_TEXT_BLOCK, bbox_str as vision_bbox_str,
            fact_rows as vision_fact_rows, finish_all as vision_finish_all, hero_note_for,
            panel_facts as vision_panel_facts, prepare_all as vision_prepare_all,
            status_for as vision_status_for, status_text as vision_status_text,
            summarise as vision_summarise,
        )

        t_vision = time.perf_counter()
        vision_provider = provider_status()
        vision_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="packcheck-vision")
        try:
            vision_preps_future = vision_pool.submit(vision_prepare_all, images)
        except Exception:  # pragma: no cover - defensive
            vision_preps_future = None

        # ---------- preprocessing + OCR + targeted multi-pass + region recheck ----------
        from backend.ocr.multipass import pass_summary, run_multipass_ocr

        _set_stage(inspection, db, "preprocessing", "running")
        _set_stage(inspection, db, "text_detection_ocr", "running")
        t_ocr = time.perf_counter()
        lines_by_image: dict[int, list] = {}
        all_passes: list[str] = []
        reused_images = 0
        for image in images:
            path = settings.STORAGE_DIR / "originals" / image.stored_filename
            import cv2

            img = cv2.imread(str(path))
            if img is None:
                lines_by_image[image.id] = []
                continue
            # OCR EVIDENCE REUSE. Recognised lines are stored per image as evidence, and the
            # original image is immutable (content-hashed on upload), so the same evidence yields
            # the same lines. Re-running the recogniser on every reprocess made downstream results
            # drift between runs and cost the most expensive step in the pipeline again. Stored
            # evidence is therefore reused by default; pass refresh_ocr=True to recognise again.
            if not refresh_ocr:
                stored = (
                    db.query(OcrResult)
                    .filter(OcrResult.image_id == image.id)
                    .order_by(OcrResult.line_index)
                    .all()
                )
                reused = []
                for row in stored:
                    box = _parse_bbox(row.bbox or "")
                    if box is None:
                        continue
                    reused.append(
                        OcrLine(
                            text=row.text,
                            confidence=row.confidence or 0.0,
                            bbox=box,
                            engine=row.engine or "",
                            variant=row.variant or "original",
                        )
                    )
                if reused:
                    image.ocr_text = "\n".join(l.text for l in reused)
                    image.ocr_line_count = len(reused)
                    db.commit()
                    lines_by_image[image.id] = reused
                    reused_images += 1
                    audit.log_action(
                        inspection.inspector,
                        "ocr_evidence_reused",
                        inspection.inspection_number,
                        after=f"image {image.id}: {len(reused)} stored OCR lines",
                    )
                    continue
            lines = _ocr_image(db, inspection, image)
            # PASS 2: targeted passes only where PASS-1 left evidence gaps (routing is
            # conditional — good PASS-1 coverage runs nothing extra).
            lines, passes = run_multipass_ocr(img, lines, image.quality_status or "GOOD")
            all_passes.extend(passes)
            extra = _region_recheck(img, lines)
            if extra:
                lines = lines + extra
            # persist every non-base line (targeted-pass + region-recheck) with provenance
            base_count = image.ocr_line_count or 0
            for i, line in enumerate(lines[base_count:], start=base_count):
                db.add(
                    OcrResult(
                        image_id=image.id,
                        engine=line.engine,
                        variant=line.variant,
                        line_index=i,
                        text=line.text,
                        confidence=line.confidence,
                        bbox=line.bbox_str,
                    )
                )
            image.ocr_text = "\n".join(l.text for l in lines)
            image.ocr_line_count = len(lines)
            db.commit()
            lines_by_image[image.id] = lines
        if all_passes:
            inspection.notes = ((inspection.notes or "") + " | " if inspection.notes else "") + pass_summary(all_passes)
        _set_stage(inspection, db, "preprocessing", "done")
        _set_stage(inspection, db, "text_detection_ocr", "done")
        _set_stage(inspection, db, "region_ocr", "done")
        _timing(inspection, db, "text_detection_ocr", t_ocr)
        # ---------- on-device vision, phase 2 (always runs — no key, no network) ----------
        _set_stage(inspection, db, "visual_analysis", "running")
        try:
            vision_preps = vision_preps_future.result() if vision_preps_future is not None else []
        except Exception as exc:  # a failed pixel phase is reported, never fabricated
            vision_preps = []
            audit.log_action(
                inspection.inspector, "vision_prepare_failed", inspection.inspection_number,
                reason=f"{type(exc).__name__}: {exc}"[:200],
            )
        vision_result = vision_finish_all(vision_preps, lines_by_image)
        vision_ms = _timing(inspection, db, "visual_analysis", t_vision)

        # Persist the visual observations. Idempotent: a reprocess replaces its own regions and
        # the crops that belong to them, so regions never accumulate across runs.
        db.query(VisionRegion).filter(VisionRegion.inspection_id == inspection.id).delete(synchronize_session=False)
        db.query(Evidence).filter(
            Evidence.inspection_id == inspection.id, Evidence.related_type == "vision"
        ).delete(synchronize_session=False)
        for row in vision_fact_rows(vision_result):
            db.add(VisionRegion(inspection_id=inspection.id, **row))
        db.commit()

        # The prominent block and the candidate display region keep a crop, so "view evidence"
        # works on a VISUAL observation exactly as it does on a field value.
        for vision in vision_result.images:
            image_row = next((i for i in images if i.id == vision.image_id), None)
            if image_row is None:
                continue
            seen_boxes: set[str] = set()
            for region in vision.regions:
                is_panel = region.kind == KIND_PANEL
                is_hero = region.kind == KIND_TEXT_BLOCK and region.bbox is not None and region.bbox == vision.hero_bbox
                if region.bbox is None or not (is_panel or is_hero):
                    continue
                key = vision_bbox_str(region.bbox)
                if key in seen_boxes:
                    continue
                seen_boxes.add(key)
                stored = save_crop(
                    settings.STORAGE_DIR / "originals" / image_row.stored_filename,
                    region.bbox,
                    f"{uuid.uuid4().hex}.png",
                )
                if not stored:
                    continue
                create_evidence(
                    db,
                    inspection.id,
                    kind="crop",
                    field_name="",
                    related_type="vision",
                    related_id=None,
                    image_id=vision.image_id,
                    bbox=key,
                    stored_filename=stored,
                    original_filename=image_row.original_filename,
                    raw_text=region.text,
                    normalized_value="",
                    confidence=region.confidence,
                    extraction_method=f"{vision_result.engine} · {region.kind}",
                    note=region.note,
                )
        db.commit()
        _set_stage(inspection, db, "visual_analysis", "done" if vision_result.ok else "failed")

        # ---------- optional vision provider (ADDITIVE; the pipeline never depends on it) ----------
        # The provider contributes ADDITIONAL candidates which pass through exactly the same
        # ranking, fusion, conflict, normalization and validation stages — a provider reading is
        # evidence, never a decision. A failure is recorded verbatim and the run continues.
        from backend.ai import ai_field_candidates

        _set_stage(inspection, db, "provider_extraction", "running")
        vision_candidates: list = []
        vision_note = ""
        t_provider = time.perf_counter()
        try:
            vision_candidates, vision_note = ai_field_candidates(images)
        except Exception as exc:  # never let an optional provider break the pipeline
            vision_candidates, vision_note = [], f"Vision provider error (ignored): {exc}"
        provider_used = bool(vision_candidates)
        provider_error = vision_note if (vision_provider.enabled and not provider_used) else ""
        inspection.provider_status = (
            "USED" if provider_used
            else "FAILED" if provider_error
            else "NOT_CONFIGURED" if not vision_provider.enabled
            else "NO_READING"
        )
        inspection.provider_note = vision_note or vision_provider.reason
        _timing(inspection, db, "provider_extraction", t_provider)
        _set_stage(inspection, db, "provider_extraction", "done" if provider_used else "skipped")

        # ---------- the honest perception status (UI, report and PRO all read this verbatim) ----
        inspection.vision_engine = vision_result.engine
        inspection.vision_status = vision_status_for(
            vision_result, vision_provider.enabled, provider_used, provider_error
        )
        inspection.vision_note = (
            f"{vision_summarise(vision_result, vision_ms)} {vision_status_text(inspection.vision_status)}"
            + (f" Provider note: {vision_note}" if vision_note else "")
        )
        if vision_note:
            inspection.notes = ((inspection.notes or "") + " | " if inspection.notes else "") + vision_note
        if vision_result.error:
            inspection.notes = (
                (inspection.notes or "") + " | " if inspection.notes else ""
            ) + f"On-device vision: {vision_result.error}"
        _set_stage(inspection, db, "candidate_generation", "done")
        db.commit()

        # ---------- field extraction + normalization ----------
        from backend.extraction.association import associate_label_values

        _set_stage(inspection, db, "field_extraction", "running")
        t_extraction = time.perf_counter()
        # Label-value association: rebuild split 'label' / 'value' fragments from OCR
        # geometry before extraction (generic — no fixed coordinates, no product names).
        associated_lines_by_image = {
            image_id: associate_label_values(lines) for image_id, lines in lines_by_image.items()
        }
        per_field = run_extraction(associated_lines_by_image)
        # Vision readings join the candidate pool under the same rules as OCR candidates.
        for c in vision_candidates:
            per_field.setdefault(c.field_name, []).append(c)

        # Reprocess hygiene: clear previous AI-generated field rows so repeated processing
        # never duplicates rows. Human-touched fields (manually corrected / human-confirmed
        # absent) are PRESERVED — original AI output remains in field_candidates + audit log.
        preserved_manual = {
            r.field_name: r
            for r in db.query(ExtractedField)
            .filter(ExtractedField.inspection_id == inspection.id, ExtractedField.manually_corrected.is_(True))
            .all()
        }
        db.query(ExtractedField).filter(
            ExtractedField.inspection_id == inspection.id,
            ExtractedField.manually_corrected.is_(False),
        ).delete(synchronize_session=False)
        db.query(FieldCandidate).filter(FieldCandidate.inspection_id == inspection.id).delete(
            synchronize_session=False
        )
        db.commit()

        field_rows: dict[str, ExtractedField] = dict(preserved_manual)
        conflicting_fields: list[str] = []
        for field_name, cands in per_field.items():
            if field_name in field_rows:
                continue  # human-touched field wins over re-extraction
            winner, conflict = pick_winner(cands)
            for c in cands:
                db.add(
                    FieldCandidate(
                        inspection_id=inspection.id,
                        field_name=field_name,
                        value=c.value,
                        raw_value=c.raw_value,
                        confidence=c.confidence,
                        score=c.score,
                        source_image_id=c.source_image_id,
                        source_engine=c.engine,
                        variant=c.variant,
                        bbox=str(c.bbox) if c.bbox else "",
                        source_text=c.source_text,
                        reason=c.reason,
                        conflict=conflict and c is not winner,
                        is_winner=c is winner,
                    )
                )
            if winner is None:
                continue
            normalized = normalize_field(field_name, winner.value)
            validation = validate_field(field_name, normalized)
            display = str(normalized.get("display") or winner.value or "").strip()
            # Empty-value candidates (e.g. a date anchor with no parseable date) are recorded
            # as UNCERTAIN with NO confidence — never as a DETECTED empty value with a score.
            if not display and not (winner.value or "").strip():
                row = ExtractedField(
                    inspection_id=inspection.id,
                    field_name=field_name,
                    state=FieldState.UNCERTAIN.value,
                    raw_value=winner.raw_value,
                    normalized_value=json.dumps(normalized, ensure_ascii=False),
                    display_value="",
                    confidence=0.0,
                    source="ocr",
                    source_image_id=winner.source_image_id,
                    source_engine=winner.engine,
                    preprocessing_variant=winner.variant,
                    bbox=str(winner.bbox) if winner.bbox else "",
                    source_text=winner.source_text,
                    extraction_reason=winner.reason,
                    uncertainty_reason=(
                        (winner.reason + ". ") if winner.reason else ""
                    )
                    + ("; ".join(validation["issues"]) if validation.get("issues") else "").strip()
                    or "Label/anchor detected but no reliable value could be extracted. Manual verification required.",
                    conflict_status="NONE",
                )
                db.add(row)
                field_rows[field_name] = row
                continue
            # An AMBIGUOUS date is NOT a detected fact. The value is offered (so the inspector
            # can act on it) but the state is UNCERTAIN and the reason states exactly why — the
            # day/month order is an assumption until a human confirms it.
            ambiguous_date = bool(normalized.get("ambiguous")) and field_name.startswith("date_")
            # A value inferred from layout/typography (a logo read as a brand, a title split into
            # brand+product) is offered for review, never asserted as a detected fact.
            inferred_value = bool(getattr(winner, "inferred", False)) and winner.confidence < 0.7
            # A company entity printed with no role anchor is a real declaration whose ROLE is
            # unresolved — the value is offered and the review state says exactly why.
            role_uncertain = bool(getattr(winner, "role_uncertain", False))
            role_uncertain_note = (
                " Declaration role unresolved: no 'Manufactured by' / 'Packed by' / 'Marketed "
                "by' anchor was read on the package, so the entity is not asserted to be the "
                "manufacturer until the inspector confirms it."
                if role_uncertain
                else ""
            )
            row = ExtractedField(
                inspection_id=inspection.id,
                field_name=field_name,
                state=(
                    FieldState.CONFLICTING.value
                    if conflict
                    else FieldState.UNCERTAIN.value
                    if (ambiguous_date or inferred_value or role_uncertain)
                    else FieldState.DETECTED.value
                ),
                raw_value=winner.raw_value,
                normalized_value=json.dumps(normalized, ensure_ascii=False),
                display_value=display,
                confidence=winner.confidence,
                source="ocr",
                source_image_id=winner.source_image_id,
                source_engine=winner.engine,
                preprocessing_variant=winner.variant,
                bbox=str(winner.bbox) if winner.bbox else "",
                source_text=winner.source_text,
                extraction_reason=winner.reason,
                uncertainty_reason=(
                    (
                        "Date value present but the day/month order is an assumption "
                        "(Indian DD/MM convention) — manual confirmation required."
                        if ambiguous_date
                        else ""
                    )
                    + (
                        " "
                        "Value inferred from layout/typography, not from an explicit declaration "
                        "label — manual confirmation required."
                        if inferred_value
                        else ""
                    )
                    + role_uncertain_note
                    + (
                        " " + "; ".join(validation["issues"])
                        if not validation["valid"] and validation.get("issues")
                        else ""
                    )
                ).strip(),
                conflict_status="CONFLICTING" if conflict else "NONE",
            )
            db.add(row)
            field_rows[field_name] = row
            if conflict:
                conflicting_fields.append(field_name)

        # ---------- marketer promotion (transparent, evidence-cited) ----------
        # "Marketed by X" alone does not identify a manufacturer/packer/importer. If ONLY a
        # marketer entity was found, promote it to a manufacturer candidate with full
        # provenance — never silently, and never by inventing a company name.
        role_entities = [
            f for f in ("manufacturer", "packer", "importer")
            if f in field_rows and (field_rows[f].display_value or "").strip()
        ]
        if "marketer" in field_rows and (field_rows["marketer"].display_value or "").strip() and not role_entities:
            mk = field_rows["marketer"]
            promoted = ExtractedField(
                inspection_id=inspection.id,
                field_name="manufacturer",
                state=mk.state,
                raw_value=mk.raw_value,
                normalized_value=mk.normalized_value,
                display_value=mk.display_value,
                confidence=round(mk.confidence * 0.8, 3),
                source="ocr",
                source_image_id=mk.source_image_id,
                source_engine=mk.source_engine,
                preprocessing_variant=mk.preprocessing_variant,
                bbox=mk.bbox,
                source_text=mk.source_text,
                extraction_reason=(
                    "promoted from 'Marketed by' declaration — no manufacturer/packer/importer "
                    "declaration was detected in the supplied images"
                ),
                uncertainty_reason="Company declared as marketer; legal role (manufacturer/packer/importer) requires manual confirmation.",
                conflict_status="NONE",
            )
            db.add(promoted)
            field_rows["manufacturer"] = promoted

        # ---------- contact-domain corroboration (honest review flag) ----------
        # A syntactically valid address is not automatically a correct reading: OCR turns
        # 'britindia' into 'briindio' while leaving the '@domain.tld' shape intact. Where the
        # package's own declarations give us something to check the domain against (brand,
        # product, manufacturer/packer entity, website), a domain that matches none of them is
        # offered for review instead of being published as a detected contact. Public mail
        # providers are exempt — a small brand genuinely uses gmail/yahoo.
        email_row = field_rows.get("consumer_care_email")
        if email_row is not None and (email_row.display_value or "").strip():
            corroboration_text = " ".join(
                (field_rows.get(f).display_value or "")
                for f in ("brand", "product_name", "common_name", "manufacturer", "packer",
                          "importer", "marketer", "website")
                if field_rows.get(f) is not None
            )
            check = corroborate_email_domain(email_row.display_value, corroboration_text)
            if check["corroborated"] is False:
                email_row.state = FieldState.UNCERTAIN.value
                email_row.uncertainty_reason = check["note"]
                conflicting_fields.append("consumer_care_email")
            elif check["corroborated"] is True and check["note"].startswith("domain"):
                # corroboration is real evidence: record where it came from
                email_row.uncertainty_reason = check["note"]

        # ---------- explicit MISSING rows for tracked fields with no detection ----------
        for name in TRACKED_DECLARATION_FIELDS:
            if name in field_rows:
                continue
            row = ExtractedField(
                inspection_id=inspection.id,
                field_name=name,
                state=FieldState.MISSING.value,
                display_value="",
                confidence=0.0,
                source="ocr",
                extraction_reason="no reliable candidate detected by extraction",
                uncertainty_reason=MISSING_NOT_FOUND_REASON,
                conflict_status="NONE",
            )
            db.add(row)
            field_rows[name] = row

        db.commit()
        _set_stage(inspection, db, "field_extraction", "done")
        _set_stage(inspection, db, "normalization", "done")

        # ---------- classification ----------
        _set_stage(inspection, db, "classification", "running")
        all_text = "\n".join(i.ocr_text or "" for i in images)
        cls = classify_from_ocr(all_text)
        db.add(
            Classification(
                inspection_id=inspection.id,
                category=cls["category"],
                state=cls["state"],
                confidence=cls["confidence"],
                signals=cls["signals"],
            )
        )
        inspection.category = cls["category"]
        inspection.category_state = cls["state"]
        inspection.category_confidence = cls["confidence"]
        db.commit()
        _set_stage(inspection, db, "classification", "done")

        # ---------- physical measurement (Rule 7 font-size checking) ----------
        # A millimetre figure needs a scale. The measurement layer reports exactly what it has
        # (calibration source, panel area source) and reports UNAVAILABLE rather than guessing.
        from backend.rules.registry import get_active_versions
        from backend.services.font_service import build_font_measurement

        _timing(inspection, db, "field_extraction", t_extraction)

        # ---------- vision fusion (traceability only — it never invents or alters a value) --------
        # Where a field's retained region IS the most prominent printed block on its face, that
        # fact is recorded on the field. No value, state or score changes: it tells the inspector
        # why the region sits where it does, and it is reproducible from the stored regions.
        for row in field_rows.values():
            hero_note = hero_note_for(row.source_image_id, _parse_bbox(row.bbox or ""), vision_result)
            if hero_note and hero_note not in (row.extraction_reason or ""):
                row.extraction_reason = ((row.extraction_reason or "").strip() + " " + hero_note).strip()
        db.commit()

        font_params = next(
            (rv.params or {} for rv in get_active_versions(db).values() if rv.check_type == "font_size"),
            {},
        )
        font_measurement = build_font_measurement(
            inspection,
            field_rows,
            images,
            declaration_fields=font_params.get("declaration_fields"),
            ocr_lines=lines_by_image,
        )
        _set_stage(inspection, db, "measurement", "done" if font_measurement.get("available") else "skipped")

        # ---------- rules ----------
        _set_stage(inspection, db, "rule_applicability", "running")
        t_rules = time.perf_counter()
        context = {
            "category": inspection.category,
            "category_state": inspection.category_state,
            "overall_quality": overall_quality,
            "image_count": len(images),
            "packaging": "retail_package",
            "is_import_evidence": "imported" in (all_text or "").lower(),
            "font_measurement": font_measurement,
            # Which view each declaration was read from — the placement check's evidence — plus
            # the visual observations (prominent block, candidate display region, readability)
            # measured by the on-device vision engine. Facts only: the engine interprets them.
            "panel_evidence": build_panel_evidence(images, field_rows, vision_facts=vision_panel_facts(vision_result)),
        }
        outcomes = evaluate_and_persist_rules(db, inspection, field_rows, context)
        db.commit()
        _set_stage(inspection, db, "rule_applicability", "done")
        _set_stage(inspection, db, "rule_validation", "done")
        _timing(inspection, db, "rule_validation", t_rules)

        # ---------- evidence ----------
        _set_stage(inspection, db, "evidence_generation", "running")
        t_evidence = time.perf_counter()
        for field_name, row in field_rows.items():
            # A field gets a retained source region when it either asserts a value (DETECTED /
            # CONFLICTING / MANUALLY_CORRECTED) or OFFERS one for review (UNCERTAIN with a value —
            # e.g. an ambiguous date code or a logo read as a brand). A review candidate must be
            # inspectable in the same way a detected value is: the inspector has to see WHERE the
            # candidate came from before confirming or rejecting it.
            review_candidate = row.state == FieldState.UNCERTAIN.value and bool(
                (row.display_value or row.raw_value or "").strip()
            )
            if (
                (
                    row.state in (FieldState.DETECTED.value, FieldState.CONFLICTING.value, FieldState.MANUALLY_CORRECTED.value)
                    or review_candidate
                )
                and row.bbox and row.source_image_id
            ):
                img_row = next((i for i in images if i.id == row.source_image_id), None)
                if img_row is None:
                    continue
                crop_name = f"{uuid.uuid4().hex}.png"
                bbox_int = _parse_bbox(row.bbox)
                stored = save_crop(settings.STORAGE_DIR / "originals" / img_row.stored_filename, bbox_int, crop_name) if bbox_int else None
                if stored:
                    ev = create_evidence(
                        db,
                        inspection.id,
                        kind="crop",
                        field_name=field_name,
                        related_type="field",
                        related_id=row.id,
                        image_id=row.source_image_id,
                        bbox=row.bbox,
                        stored_filename=stored,
                        original_filename=img_row.original_filename,
                        raw_text=row.source_text,
                        normalized_value=row.display_value,
                        confidence=row.confidence,
                        extraction_method=f"{row.source_engine} + {row.preprocessing_variant}",
                        note=(
                            "review candidate — value offered for manual confirmation, not asserted"
                            if review_candidate
                            else ""
                        ),
                    )
                    row.evidence_id = ev.id
        db.commit()
        _set_stage(inspection, db, "evidence_generation", "done")
        _timing(inspection, db, "evidence_generation", t_evidence)

        # ---------- date-consistency analysis (advisory, stored for the reviewer) ----------
        from backend.normalization.consistency import check_consistency, findings_summary

        date_map = {
            name: row.normalized_value
            for name, row in field_rows.items()
            if name.startswith("date_") and row.normalized_value
        }
        consistency_findings = check_consistency(date_map)
        date_summary = findings_summary(consistency_findings)
        if date_summary:
            # attach to affected date fields' uncertainty reasons WITHOUT overwriting content
            for f in consistency_findings:
                pass  # findings summarized at inspection level (below); per-field rows keep their own reasons
            inspection.notes = ((inspection.notes or "") + " | " if inspection.notes else "") + date_summary

        # ---------- decision ----------
        from backend.rules.engine import RuleOutcome as _RO

        rule_outcomes = outcomes
        decision, summary = decide_inspection(rule_outcomes, inspection.category_state, conflicting_fields)
        if date_summary:
            summary = f"{summary} {date_summary}"
        inspection.final_decision = decision
        inspection.summary = summary[:1500] if summary else ""
        inspection.status = InspectionStatus.AWAITING_REVIEW.value
        inspection.processed_at = _utcnow()
        # Provenance of THIS result: which engine produced it. A record carrying an older value is
        # listed for reprocessing rather than being silently left on superseded analysis.
        inspection.pipeline_version = ENGINE_VERSION
        inspection.duration_ms = int((time.perf_counter() - t_total) * 1000)
        _set_stage(inspection, db, "inspection_result", "done")
        db.commit()
        # ---------- product scan repository + automated compliance review ----------
        _set_stage(inspection, db, "compliance_review", "running")
        t_compliance = time.perf_counter()
        try:
            from backend.services.repository_service import refresh_scan

            scan = refresh_scan(db, inspection)
            _set_stage(inspection, db, "compliance_review", "done")
            inspection.notes = ((inspection.notes or "") + " | " if inspection.notes else "") + (
                f"automated compliance review: {scan.compliance_score}% / {scan.ai_verdict} "
                f"({scan.review_status})"
            )
        except Exception as exc:  # the repository must never break the inspection itself
            db.rollback()
            inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
            _set_stage(inspection, db, "compliance_review", "failed")
            audit.log_action(
                inspection.inspector, "compliance_review_failed", inspection.inspection_number,
                reason=f"{type(exc).__name__}: {exc}"[:200],
            )
        _timing(inspection, db, "compliance_review", t_compliance)
        inspection.duration_ms = int((time.perf_counter() - t_total) * 1000)
        db.commit()
        audit.log_action(
            inspection.inspector,
            "processing_completed",
            inspection.inspection_number,
            after=f"{decision}; fields={len(field_rows)}; rules={len(outcomes)}",
        )
        return inspection

    except Exception as e:
        db.rollback()
        inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
        if inspection:
            inspection.status = InspectionStatus.FAILED.value
            inspection.duration_ms = int((time.perf_counter() - locals().get("t_total", time.perf_counter())) * 1000)
            inspection.summary = f"Processing failed: {type(e).__name__}. No partial results were presented as final."
            _set_stage(inspection, db, "inspection_result", "failed")
            db.commit()
            audit.log_action(inspection.inspector, "processing_failed", inspection.inspection_number, reason=str(e)[:200])
        return inspection


def _fields_view(field_rows: dict[str, ExtractedField]) -> dict:
    return {
        name: {
            "state": row.state,
            "display_value": row.display_value,
            "normalized_value": row.normalized_value,
            "confidence": row.confidence,
            # Provenance of the winning value: which image it was read from. Used by the
            # placement check to report the VIEW a declaration appeared on.
            "source_image_id": row.source_image_id,
        }
        for name, row in field_rows.items()
    }


def evaluate_and_persist_rules(
    db: Session, inspection: Inspection, field_rows: dict[str, ExtractedField], context: dict
) -> list:
    """Run applicability + rule evaluation for all active rule versions and persist results.

    Reused by the pipeline and by review actions (e.g. human-confirmed absence) so the
    deterministic evaluation logic exists in exactly one place. Violations are created with
    evidence-matched status: FAIL → OPEN (evidence-backed potential violation),
    non-manual UNCERTAIN → NEEDS_REVIEW (never a confirmed violation).
    Returns the list of RuleOutcome objects.
    """
    versions = get_active_versions(db)
    # Human decisions on violations are DURABLE: a reviewer's CONFIRM/DISMISS/RESOLVE is a
    # recorded determination about that rule's outcome and must survive re-evaluation (which
    # deletes and recreates rule evaluations + violations). Capture them by rule_number first
    # and re-apply after the new rows are created; when the rule no longer FAILs, no violation
    # exists and the stale decision is simply irrelevant.
    human_violation_decisions = {
        v.rule_number: v.status
        for v in db.query(Violation).filter(Violation.inspection_id == inspection.id).all()
        if v.status in (
            ViolationStatus.CONFIRMED.value,
            ViolationStatus.DISMISSED.value,
            ViolationStatus.RESOLVED.value,
        )
    }
    # Violations reference rule_evaluations, so violations must be deleted FIRST on reprocess.
    # synchronize_session="fetch" also evicts any already-loaded rows from the identity map, so
    # re-creating rows never leaves a stale instance behind (e.g. from a review action).
    db.query(Violation).filter(Violation.inspection_id == inspection.id).delete(synchronize_session="fetch")
    db.query(RuleEvaluation).filter(RuleEvaluation.inspection_id == inspection.id).delete(synchronize_session="fetch")
    db.flush()

    outcomes = []
    for rule_id, rv in versions.items():
        applicability = evaluate_applicability(rv, context)
        outcome = evaluate_rule(rv, _fields_view(field_rows), context, applicability)
        outcome.rule_id = rule_id
        outcome.rule_number = _rule_number(db, rule_id)
        eval_row = RuleEvaluation(
            inspection_id=inspection.id,
            rule_id=_rule_db_id(db, rule_id),
            rule_version_id=rv.id,
            rule_number=outcome.rule_number,
            title=outcome.title,
            check_type=outcome.check_type,
            status=outcome.status,
            reason=outcome.reason,
            observed=outcome.observed,
            expected=outcome.expected,
            confidence=outcome.confidence,
            critical=outcome.critical,
            # Pinned at evaluation time so a later rule-library edit cannot move this scan's score.
            weight=outcome.weight,
            applicability_kind=outcome.applicability_kind,
            detail=json.dumps(outcome.detail or {}, ensure_ascii=False),
        )
        db.add(eval_row)
        db.flush()
        outcome._eval_row_id = eval_row.id
        outcomes.append(outcome)
        if outcome.status == RuleStatus.FAIL.value:
            # A FAIL only arises from positive evidence (e.g. human-confirmed absence) or an
            # inspector's physical examination. Human-confirmed → CONFIRMED; otherwise an
            # evidence-backed potential violation → OPEN, pending confirmation.
            violation_status = (
                ViolationStatus.CONFIRMED.value
                if getattr(outcome, "human_confirmed", False)
                else ViolationStatus.OPEN.value
            )
            # a previously recorded human determination stands (never silently reset to OPEN)
            violation_status = human_violation_decisions.get(outcome.rule_number, violation_status)
            db.add(
                Violation(
                    inspection_id=inspection.id,
                    rule_evaluation_id=eval_row.id,
                    rule_number=outcome.rule_number,
                    title=outcome.title,
                    description=outcome.reason,
                    observed=outcome.observed,
                    expected=outcome.expected,
                    severity=Severity.MEDIUM.value if not outcome.critical else Severity.HIGH.value,
                    status=violation_status,
                    confidence=outcome.confidence,
                )
            )
        elif outcome.status == RuleStatus.UNCERTAIN.value and outcome.check_type != "manual_only":
            # Uncertainty is NOT a violation: NEEDS_REVIEW records what requires manual
            # verification without implying non-compliance.
            db.add(
                Violation(
                    inspection_id=inspection.id,
                    rule_evaluation_id=eval_row.id,
                    rule_number=outcome.rule_number,
                    title=outcome.title,
                    description=outcome.reason,
                    observed=outcome.observed,
                    expected=outcome.expected,
                    severity=Severity.LOW.value,
                    status=ViolationStatus.NEEDS_REVIEW.value,
                    confidence=outcome.confidence,
                )
            )
    db.flush()
    return outcomes


def _rule_db_id(db: Session, rule_id: str) -> int:
    from backend.models import Rule

    rule = db.query(Rule).filter(Rule.rule_id == rule_id).first()
    return rule.id if rule else 0


def _rule_number(db: Session, rule_id: str) -> str:
    from backend.models import Rule

    rule = db.query(Rule).filter(Rule.rule_id == rule_id).first()
    return rule.rule_number if rule else ""


def _parse_bbox(bbox_str: str) -> tuple[int, int, int, int] | None:
    try:
        cleaned = bbox_str.strip("()[]").replace(" ", "")
        parts = [int(p) for p in cleaned.split(",") if p != ""]
        if len(parts) == 4:
            return tuple(parts)
    except Exception:
        pass
    return None


def _quality_for_file(path: Path) -> dict:
    from backend.services.image_service import assess_image_quality

    return assess_image_quality(path)


def _utcnow():
    from backend.models.user import utcnow

    return utcnow()


def create_inspection(
    db: Session, inspector: str, notes: str = "", organization_id: int | None = None
) -> Inspection:
    """Create an inspection. ``organization_id`` is set from the trusted user record (never from
    the request body) and is what confines a regulated entity / internal-compliance user to their
    own organization's records."""
    from datetime import datetime

    count = db.query(Inspection).count() + 1
    inspection = Inspection(
        inspection_number=f"INS-{datetime.now().year}-{count:06d}",
        inspector=inspector,
        notes=notes,
        status=InspectionStatus.CREATED.value,
        stage_status={},
        organization_id=organization_id,
    )
    db.add(inspection)
    db.commit()
    db.refresh(inspection)
    audit.log_action(inspector, "inspection_created", inspection.inspection_number)
    return inspection
