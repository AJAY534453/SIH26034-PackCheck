// Shared API types mirroring backend schemas.

export type Decision = 'COMPLIANT' | 'NON_COMPLIANT' | 'NEEDS_MANUAL_REVIEW' | null

export type Role =
  | 'ADMIN'
  | 'INSPECTOR'
  | 'VIEWER'
  | 'ENFORCEMENT_OFFICER'
  | 'REGULATED_ENTITY'
  | 'INTERNAL_COMPLIANCE'

export interface OrganizationRef {
  id: number
  name: string
  kind: 'REGULATOR' | 'BUSINESS' | 'CORPORATE'
}

export interface User {
  id: number
  username: string
  full_name: string
  email: string
  role: Role
  status: 'ACTIVE' | 'DISABLED' | 'LOCKED' | 'PENDING_VERIFICATION'
  is_active: boolean
  organization_id: number | null
  organization: OrganizationRef | null
  mfa_enabled: boolean
  must_change_password: boolean
  permissions: string[]
  home: string
}

export interface LoginResponse {
  // Present once a session is issued; empty while an MFA challenge is pending.
  access_token: string
  username: string
  role: Role
  full_name: string
  home: string
  permissions: string[]
  mfa_required?: boolean
  mfa_token?: string | null
  message?: string
}

export interface SessionInfo {
  session_id: string
  current: boolean
  created_at: string
  last_used_at: string
  expires_at: string
  revoked: boolean
  revoked_at: string
  revoke_reason: string
  ip_address: string
  user_agent: string
  mfa_verified: boolean
}

// ---------- image analysis (capture -> enhance -> OCR -> structure) ----------
export interface AnalysisLine {
  text: string
  confidence: number
  bbox: number[]
  variant: string
}

export interface StructuredField {
  value: string
  raw_value: string
  confidence: number
  state: 'DETECTED' | 'UNCERTAIN' | 'CONFLICTING'
  reason: string
  bbox: number[] | null
}

export interface ImageAnalysis {
  id: number
  created_by: string
  created_at: string
  original_filename: string
  original_url: string
  enhanced_full_url: string
  enhanced_text_url: string
  state: 'NO_TEXT' | 'LOW_CONFIDENCE' | 'TEXT_EXTRACTED'
  message: string
  mode: string
  quality: { metrics?: Record<string, string>; values?: Record<string, number>; score?: number; status?: string }
  quality_score: number
  quality_status: string
  text_present: boolean
  line_count: number
  mean_confidence: number
  engine: string
  ocr_lines: AnalysisLine[]
  structured: Record<string, StructuredField>
  raw_text: string
  full_ops: string[]
  text_ops: string[]
}

/** Grocery card = the tracked item plus its image + scan reference (progressive disclosure). */
export interface GroceryCard extends GroceryItemOut {
  category?: string
  image_url?: string
  inspection_number?: string
}

export interface GroceryProductField {
  field_name: string
  display_value: string
  state: string
  confidence: number
  source: string
  note: string
}

export interface GroceryDetail {
  item: GroceryCard
  product: {
    fields: GroceryProductField[]
    detected_text: string
    scan: {
      inspection_id: number
      inspection_number: string
      created_at: string
      inspector: string
      status: string
      decision: Decision
      category: string
      category_state: string
      overall_quality: string
      overall_quality_score: number
      image_count: number
    } | null
  }
}

export interface InspectionSummary {
  id: number
  inspection_number: string
  product_id: number | null
  status: string
  final_decision: Decision
  inspector: string
  category: string
  category_state: string
  overall_quality: string
  created_at: string
  processed_at: string | null
  completed_at: string | null
}

export interface ImageOut {
  id: number
  role: string
  original_filename: string
  stored_filename: string
  mime_type: string
  size_bytes: number
  width: number
  height: number
  quality_score: number
  quality_status: string
  quality_metrics: { metrics?: Record<string, string>; values?: Record<string, number | string>; score?: number; status?: string }
  ocr_line_count: number
}

export interface FieldOut {
  id: number
  field_name: string
  state: string
  raw_value: string
  normalized_value: string
  display_value: string
  // 0 means "no confidence applies" (MISSING/UNCERTAIN with no extracted value) — UI shows —
  confidence: number
  source: string
  source_engine: string
  preprocessing_variant: string
  bbox: string
  source_text: string
  extraction_reason: string
  uncertainty_reason: string
  conflict_status: string
  manually_corrected: boolean
}

export interface RuleEvalOut {
  id: number
  rule_number: string
  title: string
  status: 'PASS' | 'FAIL' | 'UNCERTAIN' | 'NOT_APPLICABLE'
  reason: string
  observed: string
  expected: string
  confidence: number
  critical: boolean
  check_type: string
  detail: Record<string, unknown>
}

/** The retained crop(s) a rule conclusion rests on — rule → detected value → image region. */
export interface RuleEvidence {
  id: number
  field_name: string
  image_id: number | null
  stored_filename: string
  bbox: string
  raw_text: string
  confidence: number
  extraction_method: string
  kind: string
}

export interface ViolationOut {
  id: number
  rule_number: string
  title: string
  description: string
  observed: string
  expected: string
  severity: string
  status: string
  confidence: number
}

/** GET /inspections/{id}/progress — the running pipeline's real state and per-stage timings. */
export interface InspectionProgressOut {
  status: string
  stage_status: Record<string, string>
  stage_timings: Record<string, number>
  duration_ms: number
  vision_status: string
  vision_engine: string
  provider_status: string
  final_decision: string | null
  official_decision: string | null
}

export interface EvidenceOut {
  id: number
  field_name: string
  kind: string
  bbox: string
  stored_filename: string
  image_id: number | null
  raw_text: string
  confidence: number
  extraction_method: string
  note?: string
  /** "field" | "rule" | "vision" | "image" — separates a visual observation from a declaration crop. */
  related_type?: string
}

export interface ReviewActionOut {
  id: number
  action: string
  reviewer: string
  original_value: string
  corrected_value: string
  reason: string
  created_at: string
}

export interface InspectionDetail extends InspectionSummary {
  stage_status: Record<string, string>
  summary: string
  notes: string
  category_confidence: number
  overall_quality_score: number
  images: ImageOut[]
  fields: FieldOut[]
  official_decision: Decision
  finalized_by: string
  finalized_at: string | null
  finalization_remarks: string
  rules: RuleEvalOut[]
  violations: ViolationOut[]
  review_actions: ReviewActionOut[]
  reports: { id: number; format: string; created_at: string; generated_by: string; stored_filename: string }[]
  evidence: EvidenceOut[]
  /** Rule evaluation id → the evidence rows that support it (empty list when none retained). */
  rule_evidence: Record<string, RuleEvidence[]>
  /** Which engine produced the active result, and whether it is still the current engine. */
  provenance: {
    engine_version: string
    current_engine: string
    up_to_date: boolean
    rule_fingerprint: string
    rule_count: number
    reprocess_count: number
    last_reprocessed_at: string
  }
  classification_signals: string
  /** Real wall-clock milliseconds per pipeline stage for the run that produced this result. */
  stage_timings: Record<string, number>
  /** Which perception sources actually ran (on-device vision always; a provider is additive). */
  vision_status: string
  vision_engine: string
  provider_status: string
  duration_ms: number
  vision: VisionPayload
  compliance_review: ScanDetailPayload | Record<string, never>
  font_size: { status: string; reason: string; detail: Record<string, unknown> }
  calibration: {
    px_per_mm: number | null
    source: string
    note: string
    panel_width_mm: number | null
    panel_height_mm: number | null
    pdp_area_cm2: number | null
    packaging_form: string
  }
}

// ---------- on-device vision ----------
/**
 * One region or measurement retained by the on-device vision engine. These are OBSERVATIONS of the
 * image (bbox, printed height, prominence, contrast, readability) — never declaration values, and
 * never a legal conclusion.
 */
export interface VisionRegionOut {
  id: number
  image_id: number | null
  kind: 'PANEL' | 'TEXT_BLOCK' | 'SYMBOL' | 'LEGIBILITY' | string
  label: string
  bbox: string
  text: string
  confidence: number
  prominence: number
  contrast: number
  sharpness: number
  text_density: number
  engine: string
  note: string
}

export interface VisionPayload {
  status: string
  engine: string
  note: string
  provider_status: string
  provider_note: string
  regions: VisionRegionOut[]
}

/** GET /health/vision — the always-on engine plus what the last real run recorded. */
export interface VisionHealth {
  engine: string
  status: string
  on_device: { available: boolean; detail: string }
  provider: string
  model: string
  configured: boolean
  reachable: boolean | null
  reachable_note: string
  last_successful_run: {
    inspection_number: string
    at: string
    vision_status: string
    vision_status_text: string
    engine: string
    provider_status: string
    vision_ms: number | null
    provider_ms: number | null
    duration_ms: number
  } | null
  last_error: string
  latency_ms: number | null
}

/** POST /vision/test — a live check of both perception sources over one image. */
export interface VisionTestResult {
  provider: string
  model: string
  configured: boolean
  provider_reason: string
  reachable?: boolean
  errors: string[]
  on_device: {
    engine?: string
    status?: string
    error?: string
    readability?: string
    ocr_lines_used?: number
    hero_text?: string
    hero_bbox?: number[] | null
    latency_ms?: number
    image?: { width: number; height: number }
    metrics?: { sharpness: number; contrast: number; glare: number; shadow: number }
    regions?: { kind: string; label: string; bbox: number[] | null; confidence: number; prominence: number }[]
  }
  provider_call: {
    attempted: boolean
    status: string
    latency_ms: number | null
    observations: number
    error: string
    detail?: string
    fields?: { field: string; value: string; confidence: number; source_image: string }[]
  }
}

// ---------- vision provider ----------
export interface AIStatus {
  enabled: boolean
  provider: string
  model: string
  reason: string
  mode: 'vision' | 'ocr_only'
  fields: string[]
}

// ---------- bill scanner ----------
/** How a stored value was obtained. Sample data is always labelled `demo`. */
export type ExtractionSource = 'vision' | 'manual' | 'demo' | 'vision+manual'

export interface BillOut {
  id: number
  created_by: string
  created_at: string
  product_id: number | null
  inspection_id: number | null
  store_name: string
  bill_number: string
  bill_date: string
  product_name: string
  brand: string
  quantity: string
  line_items: string
  billed_price: number | null
  mrp: number | null
  price_difference: number | null
  price_difference_pct: number | null
  comparison_status: 'INSUFFICIENT_DATA' | 'PRICE_AT_OR_BELOW_MRP' | 'POTENTIAL_PRICE_DIFFERENCE'
  stored_filename: string
  extraction_source: ExtractionSource
  extraction_confidence: number
  extraction_detail: string
  notes: string
}

export interface BillScanResponse {
  bill: BillOut
  readings: Record<string, { value: string; confidence: number; evidence_text: string }>
  ai: AIStatus
  message: string
}

// ---------- grocery ----------
export interface GroceryItemOut {
  id: number
  owner: string
  product_id: number | null
  inspection_id: number | null
  product_name: string
  brand: string
  quantity: string
  mrp: string
  batch_lot: string
  purchase_date: string
  expiry_date: string
  best_before_text: string
  expiry_basis: string
  notes: string
  created_at: string
  days_remaining: number | null
  alert: 'FRESH' | 'EXPIRING_SOON' | 'EXPIRED' | 'NO_EXPIRY_DATA'
  detail: string
}

export interface GroceryListResponse {
  items: GroceryCard[]
  total: number
  alerts: GroceryCard[]
}

// ---------- complaints ----------
export interface ComplaintTimelineEntry {
  at: string
  status: string
  actor: string
  note: string
}

export interface ComplaintOut {
  id: number
  complaint_number: string
  created_by: string
  created_at: string
  updated_at: string
  product_id: number | null
  inspection_id: number | null
  bill_id: number | null
  product_name: string
  store_name: string
  category: string
  severity: string
  issue: string
  status: string
  timeline: ComplaintTimelineEntry[]
  resolution: string
  stages: string[]
}

export interface ComplaintListResponse {
  items: ComplaintOut[]
  total: number
  by_status: Record<string, number>
  stages: string[]
}

export interface DashboardStats {
  pipeline: {
    images_scanned: number
    fields_detected: number
    fields_uncertain: number
    awaiting_review: number
  }
  ai: AIStatus
  bills: number
  bills_total: number
  potential_price_differences: {
    id: number
    product: string
    store: string
    billed: number | null
    mrp: number | null
    difference: number | null
    status: string
    source: ExtractionSource
  }[]
  grocery_items: number
  grocery_alert_count: number
  grocery_alerts: GroceryItemOut[]
  complaints: number
  open_complaints: number
  recent_complaints: ComplaintOut[]
  total_inspections: number
  compliant: number
  non_compliant: number
  needs_review: number
  products: number
  violations: number
  reports: number
  conflicts: number
  open_violations: number
  confirmed_violations: number
  common_violations: { type: string; count: number }[]
  trend: { date: string; count: number }[]
  recent_inspections: {
    id: number
    inspection_number: string
    product: string
    date: string
    inspector: string
    decision: Decision
    status: string
  }[]
  review_queue: { id: number; inspection_number: string; decision: Decision }[]
  conflict_queue: { id: number; inspection_number: string; fields: string[] }[]
  product_scans: number
  pending_finalization: number
  finalized_scans: number
  scan_facets: { review_status: Record<string, number>; ai_verdict: Record<string, number>; category: Record<string, number> }
  average_compliance_score: number | null
  average_coverage_score: number | null
  scans_below_threshold: number
  scans_below_coverage_floor: number
  compliance_threshold: number
  coverage_floor: number
  pending_finalization_queue: {
    id: number
    inspection_id: number
    inspection_number: string
    product: string
    score: number
    status_text: string
  }[]
}

export interface AuditLogRow {
  id: number
  actor: string
  action: string
  inspection_id: string | null
  before: string
  after: string
  reason: string
  created_at: string
}

export interface RuleOut {
  id: number
  rule_id: string
  rule_number: string
  title: string
  description: string
  source_reference: string
  current_version: number
  applicability: string
  status: string
  requirement: string
  effective_from: string | null
  check_type: string
  amendment: string
  font_size_tables: { id: string; title: string; rows: { serial: number; label: string; normal_mm: number; moulded_mm: number }[] }[]
  method: string
  exempt_note: string
}

export interface ProductOut {
  id: number
  name: string
  brand: string
  category: string
  manufacturer: string
  known_net_quantities: string
  mrp_values: string
  first_seen: string
  last_seen: string
  inspections: number
  scan_count?: number
  latest_score?: number | null
  latest_ai_verdict?: string
  latest_review_status?: string
  latest_official_decision?: string | null
  pending_finalization_count?: number
}

// ---------- product scan repository + automated compliance review ----------
/**
 * The compliance lifecycle of one scan. The AI verdict is ALWAYS preliminary; only a human
 * finalization writes an official decision — the two are never the same field.
 */
export type ReviewStatus = 'AI_PRELIMINARY' | 'PENDING_FINALIZATION' | 'FINALIZED' | 'NOT_EVALUATED'

export interface ComplianceCheck {
  rule_number: string
  title: string
  check_type: string
  status: 'PASS' | 'FAIL' | 'UNCERTAIN' | 'NOT_APPLICABLE'
  critical: boolean
  // Scoring basis, as computed by the backend — never re-derived in the UI.
  weight?: number
  applicability_kind?: 'APPLIES' | 'INAPPLICABLE' | 'EVIDENCE_MISSING' | string
  counted_in_score?: boolean
  requirement: string
  observed: string
  explanation: string
  confidence: number
  legal_reference: string
  // Present for measurable checks (Rule 7 font size): the numbers behind the status.
  required_value?: number | null
  required_reference?: string | null
  detected_value?: number | null
  detected_field?: string | null
  satisfied?: boolean | null
  detail?: Record<string, unknown>
}

export interface ScanViolation {
  id: number
  rule_number: string
  title: string
  severity: string
  status: string
  description: string
  observed: string
  expected: string
  confidence: number
  legal_reference: string
}

export interface ScanCard {
  id: number
  inspection_id: number
  inspection_number: string
  product_id: number | null
  product_name: string
  brand: string
  category: string
  scanned_at: string
  scanned_by: string
  image_filename: string
  compliance_score: number
  /** Share of the applicable requirements that could actually be decided from the evidence. */
  coverage_score: number
  ai_verdict: string
  review_status: ReviewStatus
  official_decision: Decision
  status_message: string
  status_text: string
  /** Derived report status + its human label (see docs/ARCHITECTURE.md). */
  report_status: string
  report_status_label: string
}

export interface ScanHistoryEntry {
  id: number
  inspection_number: string
  scanned_at: string
  compliance_score: number
  coverage_score: number
  ai_verdict: string
  review_status: ReviewStatus
  official_decision: Decision
  status_message: string
}

export interface ScanDetailPayload extends ScanCard {
  manufacturer: string
  net_quantity: string
  mrp: string
  batch_lot: string
  category_state: string
  image_count: number
  image_url: string
  extracted_text: string
  structured_fields: Record<string, { value: string; state: string; confidence: number; source: string; uncertainty_reason: string; manually_corrected: boolean }>
  ai_confidence: number
  ai_summary: string
  recommended_action: string
  checks: ComplianceCheck[]
  violations: ScanViolation[]
  legal_references: string[]
  rules_snapshot: { rule_number: string; title: string; status: string; legal_reference: string; confidence: number }[]
  threshold: number
  ai_verdict_is_preliminary: boolean
  finalized_by: string
  finalized_at: string | null
  remarks: string
  can_finalize: boolean
  coverage_floor: number
  /** The complete scoring basis (reviewers only — a non-reviewer never receives it). */
  scoring?: ScanScoring
  history?: ScanHistoryEntry[]
  analyses?: { id: number; state: string; message: string; created_at: string; original_url: string; enhanced_text_url: string }[]
}

/**
 * The documented basis of a scan's percentages. Everything the UI shows about scoring comes from
 * here, so the display can never disagree with the arithmetic that produced the numbers.
 */
export interface ScanScoring {
  method?: string
  score?: number
  coverage?: number
  threshold?: number
  coverage_floor?: number
  decided_weight?: number
  applicable_weight?: number
  numerator_credit?: number
  nothing_decided?: boolean
  counted?: { rule_number: string; status: string; weight: number; credit: number; contribution: number }[]
  unverified?: { rule_number: string; reason: string; kind: string }[]
  excluded?: { rule_number: string; reason: string; kind: string }[]
}

export interface RepositoryList {
  items: ScanCard[]
  total: number
  page: number
  page_size: number
  facets: {
    review_status: Record<string, number>
    ai_verdict: Record<string, number>
    category: Record<string, number>
    below_coverage_floor: number
  }
  /** Gates the repository displays without hard-coding them. */
  threshold: number
  coverage_floor: number
  pending_finalization: number
  finalized: number
}

export interface ProductHistory {
  product: {
    id: number
    name: string
    brand: string
    category: string
    manufacturer: string
    known_net_quantities: string
    mrp_values: string
    first_seen: string
    last_seen: string
    scan_count: number
    latest_score: number | null
    latest_ai_verdict: string
    latest_review_status: string
    latest_official_decision: Decision
    pending_finalization_count: number
  }
  scans: {
    id: number
    inspection_id: number
    inspection_number: string
    scanned_at: string
    scanned_by: string
    compliance_score: number
    ai_verdict: string
    review_status: ReviewStatus
    official_decision: Decision
    status_message: string
    violations: number
    checks_failed: number
    checks_passed: number
    remarks: string
    finalized_by: string
    finalized_at: string | null
  }[]
  trend: { scanned_at: string; score: number }[]
}

// ---------- Rule 7 font-size compliance + calibration ----------
export interface FontMeasurement {
  field_name: string
  text: string
  state: string
  bbox_px: number[]
  line_height_px: number
  line_height_mm: number
  letter_height_mm: number
  avg_char_width_mm: number | null
  width_ratio: number | null
  cap_height_ratio: number
  image_id: number | null
}

export interface FontSizeCompliance {
  inspection_id: number
  inspection_number: string
  status: 'PASS' | 'FAIL' | 'UNCERTAIN' | 'NOT_APPLICABLE' | 'NOT_EVALUATED'
  reason: string
  observed: string
  confidence: number
  required_mm: number | null
  required_reference: string | null
  detected_mm: number | null
  detected_field: string | null
  detected_text: string | null
  satisfied: boolean | null
  measurements: FontMeasurement[]
  smallest_line: { text: string; line_height_mm: number; letter_height_mm: number } | null
  method: string
  px_per_mm: number | null
  px_per_mm_source: string | null
  px_per_mm_note: string
  panel_area_cm2: number | null
  panel_area_source: string | null
  panel_area_note: string
  packaging_form: string
  quantity_family: string | null
  applicable_table: { id?: string; title?: string; form_column?: string; rows?: { serial: number; label: string; minimum_mm: number }[] }
  required_table: { table_id?: string; serial?: number; bracket_label?: string; required_mm?: number; reference?: string } | null
  width_ratio_ok: boolean | null
  min_width_ratio: number | null
  cap_height_ratio: number
  exempt_note: string
  tables: { id: string; title: string; rows: { serial: number; label: string; normal_mm: number; moulded_mm: number }[] }[]
  calibration: {
    px_per_mm: number | null
    source: string
    note: string
    panel_width_mm: number | null
    panel_height_mm: number | null
    pdp_area_cm2: number | null
    packaging_form: string
  }
  source_reference: string
}

// ---------- PRO: in-application assistant ----------
/** Which record the user has open. A hint only — the server resolves it inside the caller's scope. */
export interface AssistantContext {
  path?: string
  inspection_id?: number | null
  scan_id?: number | null
}

export interface AssistantReply {
  answer: string
  points: string[]
  kind: 'app_guidance' | 'legal_reference' | 'workflow' | 'glossary'
  intent: string
  sources: { label: string; kind: string; reference: string; link: string }[]
  link: string
  followups: string[]
  engine: 'retrieval' | 'provider_rephrased' | 'retrieval_guard'
  /** Always "PRO". */
  name: string
  /** One short, role-aware sign-off, e.g. "Have a good day, Admin." */
  closing: string
  /** One sentence naming the open record, when the caller has one open. */
  context_line: string
}

export interface AssistantWelcome {
  name: string
  greeting: string
  closing: string
  suggestions: string[]
  role_focus: string[]
  context_line: string
  context_link: string
  can_finalize: boolean
  can_run_analysis: boolean
  note: string
}

// ---------- reprocessing / migration ----------
export interface ReprocessPreview {
  engine_version: string
  engine_label: string
  rule_fingerprint: string
  rule_count: number
  total_inspections: number
  reprocessable_total: number
  legacy_count: number
  current_count: number
  max_batch: number
  items: {
    inspection_id: number
    inspection_number: string
    product: string
    brand: string
    status: string
    pipeline_version: string
    processed_at: string
    score: number | null
    coverage: number | null
    verdict: string
    review_status: string
    official_decision: string
    reprocess_count: number
  }[]
}

export interface ReprocessDiff {
  changed: Record<string, number | boolean>
  headline: string[]
  fields: {
    field_name: string
    label: string
    kind: 'newly_detected' | 'no_longer_detected' | 'value_changed' | 'state_changed'
    before_value: string
    after_value: string
    before_state: string
    after_state: string
    manually_corrected: boolean
    after_uncertainty_reason: string
  }[]
  rules: {
    rule_number: string
    title: string
    before_status: string
    after_status: string
    before_observed: string
    after_observed: string
    critical: boolean
  }[]
  classification: { before: string; before_state: string; after: string; after_state: string }
  score: { before: number | null; after: number | null }
  coverage: { before: number | null; after: number | null }
  verdict: { before: string; after: string }
  review_status: { before: string; after: string }
  official_decision: { before: string; after: string }
  violations: { before_total: number; after_total: number; newly_flagged: string[]; no_longer_flagged: string[] }
  unresolved: { before: number; after: number }
}

export interface InspectionRevision {
  id: number
  revision_no: number
  kind: string
  actor: string
  reason: string
  engine_version: string
  rule_fingerprint: string
  created_at: string
  fields_changed: number
  rules_changed: number
  score_before: number | null
  score_after: number | null
  coverage_before: number | null
  coverage_after: number | null
  verdict_before: string
  verdict_after: string
  review_status_before: string
  review_status_after: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  changes: ReprocessDiff
}

export interface ReprocessResult {
  ok: boolean
  error: string
  inspection_id: number
  inspection_number: string
  revision_id: number
  revision_no: number
  engine_version: string
  rule_fingerprint: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  changes: ReprocessDiff
}

export interface MigrationJob {
  id: string
  state: 'QUEUED' | 'RUNNING' | 'DONE' | 'FAILED'
  actor: string
  reason: string
  refresh_ocr: boolean
  started_at: string
  finished_at: string
  total: number
  done: number
  succeeded: number
  failed: number
  current: string
  error: string
  engine_version: string
  limit: number
  results: {
    inspection_id: number
    inspection_number: string
    ok: boolean
    error: string
    fields_changed: number
    rules_changed: number
    score_before: number | null
    score_after: number | null
    verdict_before: string
    verdict_after: string
    headline: string[]
  }[]
}

export interface ViolationRow extends ViolationOut {
  inspection_id: number
  inspection_number: string
  product: string
  created_at: string
}

export interface ReportRow {
  id: number
  inspection_id: number
  inspection_number: string
  format: string
  generated_by: string
  created_at: string
  stored_filename: string
}

// ---------------------------------------------------------------- online listings
// A captured listing is a second source of declarations about a product. Its checks carry the
// LEGAL STATUS of the catalog entry that governs them, so a citation the application has not
// confidently verified can never look like settled law in the UI.

export interface ListingFieldValue {
  display_value: string
  normalized_value?: string
  raw_value?: string
  confidence?: number
  source_text?: string
  method?: string
  inferred?: boolean
  role_uncertain?: boolean
  quantity?: string
  unit?: string
  quantity_type?: string
}

export interface ListingExtraction {
  engine: string
  source: string
  line_count: number
  fields: Record<string, ListingFieldValue>
  listing_only: Record<string, ListingFieldValue>
  notes: string[]
}

export interface ListingDeclaration {
  field: string
  status: 'FOUND' | 'ABSENT_FROM_CAPTURED_TEXT'
  value?: string
  confidence?: number
  source_text?: string
  inferred?: boolean
  role_uncertain?: boolean
  applicability_note?: string
  note?: string
  related_price_evidence?: string
}

export interface ListingDeclarationCheck {
  check_id: string
  rule_number?: string
  title?: string
  requirement?: string
  status: string
  reason: string
  confidence?: number
  coverage?: number
  minimum_ratio?: number
  declarations?: ListingDeclaration[]
  legal_status?: string
  legal_status_label?: string
  legal_status_note?: string
  source_reference?: string
  source_url?: string
}

export interface ListingComparison {
  field: string
  verdict: 'MATCH' | 'INCONSISTENCY_POTENTIAL' | 'PACKAGE_ONLY' | 'LISTING_ONLY' | 'NOT_COMPARABLE'
  package_value: string
  listing_value: string
  package_state?: string
  method: string
  note?: string
}

export interface ListingConsistency {
  status: string
  reason?: string
  inspection_id?: number
  inspection_number?: string
  comparisons: ListingComparison[]
  summary: {
    compared: number
    matched: number
    potential_inconsistencies: number
    one_sided: number
    not_comparable: number
    agreement_ratio: number | null
    headline: string
  } | null
  legal_status?: string
  legal_status_label?: string
  legal_status_note?: string
  source_reference?: string
  not_a_violation_note?: string
}

export interface ListingOut {
  id: number
  inspection_id: number | null
  source: string
  source_url: string
  platform: string
  title: string
  raw_text: string
  created_by: string
  created_at: string
  updated_at: string
  extraction: ListingExtraction
  consistency: ListingConsistency | null
}

export interface ListingRow {
  /** Present on repository cards as well: the derived report status of the scan. */
  report_status?: string
  report_status_label?: string
  id: number
  inspection_id: number | null
  source: string
  source_url: string
  platform: string
  title: string
  created_by: string
  created_at: string
  fields_read: number
  consistency: ListingConsistency['summary']
}
