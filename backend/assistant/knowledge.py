"""The assistant's knowledge base — the ONLY place its application guidance comes from.

Every entry describes something the application actually does, with the route that proves it. Keep
entries short: the assistant is required to answer concisely, so a topic is one or two sentences,
optionally a short numbered list, and the page where the user can act on it.

``kind`` distinguishes the two things the assistant must never mix up:

``app_guidance``     how this application works;
``legal_reference``  what a legal instrument provides — always answered from the versioned rule
                     data (or the rule library), never paraphrased from memory;
``workflow``         a procedure the user should follow;
``glossary``         the meaning of a status or term shown in the UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field

KIND_APP = "app_guidance"
KIND_LEGAL = "legal_reference"
KIND_WORKFLOW = "workflow"
KIND_GLOSSARY = "glossary"


@dataclass(frozen=True)
class Topic:
    id: str
    title: str
    keywords: tuple[str, ...]
    answer: str
    steps: tuple[str, ...] = ()
    link: str = ""
    kind: str = KIND_APP
    roles: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "answer": self.answer,
            "steps": list(self.steps),
            "link": self.link,
            "kind": self.kind,
        }


TOPICS: tuple[Topic, ...] = (
    # ---------------- getting started ----------------
    Topic(
        "start",
        "Where do I start?",
        ("start", "begin", "first", "new user", "getting started", "how do i use", "use the app", "help", "guide"),
        "Sign in, then work from the left menu. Start with New Inspection to scan a package, and use the Compliance Repository to see every scan and its verdict.",
        ("Sign in with your account — your role decides which portals appear.",),
        "/inspections/new",
    ),
    Topic(
        "scan_workflow",
        "Product scanning workflow",
        ("scan", "scanning", "upload", "camera", "capture", "package photo", "new inspection", "how to scan"),
        "One inspection holds all the photos of one package: upload the images, assign each a side, then run the scan.",
        (
            "Open New Inspection (needs the inspections.manage capability).",
            "Add images — front, back, side or close-up — all for the same package.",
            "Press Start scan; the pipeline runs quality check, enhancement, OCR, extraction, classification and the rules.",
            "Review the result: declarations, rule outcomes, evidence crops and the automated compliance verdict.",
        ),
        "/inspections/new",
        KIND_WORKFLOW,
    ),
    Topic(
        "menu",
        "What the menu sections mean",
        ("menu", "sidebar", "sections", "navigation", "what pages", "options", "tabs"),
        "Main = dashboards, inspections, products, violations, reports. Tools = image analysis, bill scanner, grocery, complaints. Knowledge = the rule library. System = audit log, account and settings.",
        (),
        "/dashboard",
    ),
    Topic(
        "image_analysis",
        "Image Analysis (capture → enhance → OCR)",
        ("image analysis", "analysis page", "enhance", "full image", "text enhancement", "ocr page", "crop"),
        "Image Analysis is a standalone capture tool: it enhances an image in full-image mode and in text-only mode, runs OCR on both, and shows the extracted text with a confidence state — including 'No readable text detected in this image.'",
        (),
        "/analysis",
    ),
    Topic(
        "roles",
        "Roles and who can do what",
        ("role", "roles", "permissions", "who can", "access", "authorization", "entity", "officer", "compliance user"),
        "Six roles exist: ADMIN, INSPECTOR (staff), VIEWER (read-only), ENFORCEMENT_OFFICER, REGULATED_ENTITY and INTERNAL_COMPLIANCE. Each holds explicit permissions; the server enforces them, so changing a URL never grants access.",
        (),
        "/settings",
    ),
    Topic(
        "role_home",
        "Which portal will I land in?",
        ("my dashboard", "portal", "land", "home page", "after login", "which dashboard"),
        "Enforcement officers land in the Enforcement Portal, regulated entities in the Entity Portal, internal compliance in the Compliance Portal, and staff roles in the main Dashboard. The server decides this from your account, not from a login selection.",
        (),
        "/dashboard",
    ),
    Topic(
        "entity_scope",
        "Why do I only see some records?",
        ("only see", "missing records", "other organisation", "org scope", "isolation", "cant see", "restricted data"),
        "Regulated-entity and internal-compliance accounts are confined to their own organization. The filter is applied in the database query, so another organization's records are not returned at all.",
        (),
        "/settings",
    ),
    # ---------------- compliance review ----------------
    Topic(
        "ai_review",
        "How the AI compliance review works",
        ("ai review", "compliance review", "ai verdict", "how is score", "compliance score", "percentage", "ai result", "coverage", "basis", "calculation"),
        "Two numbers are reported. Compliance % is over the requirements the images actually decided — PASS earns full credit, FAIL none, UNCERTAIN nothing. Coverage % is how much of the applicable rule set that was. Weights come from the rule definitions, where a critical requirement weighs double; manual-only checks are never scored. The scan detail itemises the basis.",
        (),
        "/repository",
    ),
    Topic(
        "threshold",
        "The 85% threshold",
        ("85", "threshold", "percentage threshold", "pass mark", "why flagged", "below threshold", "coverage floor"),
        "Three conditions must all hold for a preliminary pass-oriented verdict: no failed requirement, compliance at or above 85%, and coverage at or above the evidence floor (70% by default). Anything else — a failure, a lower compliance score, too little of the rule set verifiable, or nothing decidable at all — flags the scan for official finalization. Only an authorized official turns it into a final decision.",
        (),
        "/repository",
    ),
    Topic(
        "preliminary_vs_final",
        "AI preliminary verdict vs official final decision",
        ("preliminary", "final decision", "difference", "official", "who decides", "pending finalization", "not final"),
        "The AI verdict is always PRELIMINARY. Only an authorized official writes the FINAL decision, with remarks. Until then the scan shows 'This compliance result is pending finalization by the higher officials.'",
        (),
        "/repository",
    ),
    Topic(
        "finalize_pending",
        "How to finalize a pending decision",
        ("finalize", "finalise", "pending decision", "approve", "record decision", "close the case", "official decision"),
        "Open the scan in the Compliance Repository, review the evidence and checks, then use Finalize to record the official decision and a remark.",
        (
            "Compliance Repository → filter Review status = PENDING_FINALIZATION.",
            "Open the scan: evidence image, extracted text, every check with its legal reference.",
            "Press Finalize, choose COMPLIANT / NON_COMPLIANT / NEEDS_MANUAL_REVIEW and write the remark.",
        ),
        "/repository",
        KIND_WORKFLOW,
    ),
    Topic(
        "checks",
        "What is checked for each product?",
        ("what is checked", "which checks", "requirements checked", "declarations checked", "rule checks"),
        "The checks are the versioned Legal Metrology requirements: manufacturer/packer/importer, common name, net quantity, date of manufacture/packing/import, retail sale price (MRP), consumer care details, and — when a scale is recorded — Rule 7 letter height.",
        (),
        "/rules",
    ),
    Topic(
        "violations",
        "Violations, findings and their lifecycle",
        ("violation", "non compliance", "finding", "confirm dismiss resolve", "flagged product"),
        "A violation appears only from positive evidence or a human-confirmed absence. OPEN is evidence-backed but unconfirmed, NEEDS_REVIEW means it could not be verified, and CONFIRM / DISMISS / RESOLVE record the reviewer's call — a dismissal needs a reason and removes it from the automated decision.",
        (),
        "/violations",
    ),
    Topic(
        "repository",
        "Product scan repository and history",
        ("repository", "history", "past scans", "previous scans", "scan record", "products page", "search scans"),
        "Every scan is stored as a repository entry with its identity, evidence image, extracted text, checks, score, verdict, decision and remarks. Search, filter by status/verdict/category/score and sort it, then open a product for its full compliance history.",
        (),
        "/repository",
    ),
    Topic(
        "product_history",
        "Viewing a product's compliance history",
        ("history of a product", "same product", "previous verdicts", "trend", "repeated scans"),
        "Open a product from the repository or click its name on a scan to see every scan of that product oldest-first, with each score, verdict, final decision and violated rule.",
        (),
        "/repository",
    ),
    Topic(
        "reports",
        "PDF and export reports",
        ("pdf", "report", "export", "csv", "print", "document"),
        "Generate a PDF report from an inspection page; it contains the images, declarations, rule outcomes, violations, review actions and the reason for the status. CSV and JSON exports of the same record are on the inspection page.",
        (),
        "/reports",
    ),
    Topic(
        "evidence",
        "Reading the evidence",
        ("evidence", "crop", "bbox", "highlight", "where did the value come", "source region"),
        "Selecting a field on the inspection page highlights the exact captured region it was read from, with the crop, the raw OCR text and the confidence retained for audit.",
        (),
        "/inspections",
    ),
    Topic(
        "review_fields",
        "Correcting a wrong value",
        ("correct", "wrong value", "edit field", "fix ocr", "reject value", "add field", "confirm absent"),
        "On the inspection page you can accept, correct or reject a value, add a missed one, or confirm a declaration is genuinely absent. Each action is audited, and the rules are re-evaluated deterministically.",
        (),
        "/inspections",
        KIND_WORKFLOW,
    ),
    Topic(
        "font_size",
        "Font-size (letter height) compliance",
        ("font size", "font-size", "letter height", "height of letters", "mm size", "rule 7", "type size", "calibration"),
        "Rule 7 of the LM (PC) Rules, 2011 fixes a minimum letter height in millimetres by the area of the principal display panel. Record a scale calibration (or the measured panel size) and PACKCHECK measures each declaration and compares it with the tabulated minimum.",
        (),
        "/repository",
        KIND_LEGAL,
    ),
    Topic(
        "calibration",
        "How to calibrate for a millimetre measurement",
        ("calibrate", "calibration", "px per mm", "how to measure mm", "scale", "pixel to mm"),
        "Give the system a physical scale: either px/mm, or a known printed length with its pixel span, or the panel width × height in millimeters. Without a scale the height check honestly reports that millimetres cannot be measured.",
        (
            "Open the inspection and the Rule 7 panel.",
            "Enter a known length in mm and its pixel span (or the panel width × height).",
            "Save: the rules are re-evaluated immediately and the scan score updates.",
        ),
        "/inspections",
        KIND_WORKFLOW,
    ),
    Topic(
        "bill_scanner",
        "Bill Scanner",
        ("bill", "receipt", "price difference", "billed price", "overcharge", "mrp compare"),
        "Bill Scanner reads a bill, compares the billed amount with the product's printed MRP using pure arithmetic, and labels any difference as a potential price difference requiring verification.",
        (),
        "/bills",
    ),
    Topic(
        "grocery",
        "Grocery tracker",
        ("grocery", "expiry", "best before", "my items", "tracking"),
        "Grocery tracks only the items you explicitly add, converting a 'best before 12 months' style declaration into a date only when a date to count from exists.",
        (),
        "/grocery",
    ),
    Topic(
        "complaints",
        "Complaint Center",
        ("complaint", "grievance", "file complaint", "status of complaint", "escalate"),
        "File a complaint against a product, bill or inspection; the timeline records every status change with actor and time, and staff move it through its lifecycle.",
        (),
        "/complaints",
    ),
    Topic(
        "audit",
        "Audit trail",
        ("audit", "audit log", "who did what", "history of actions", "trail"),
        "Every sensitive action (login, upload, review, finalization, calibration) is appended to an immutable audit log with actor, time and reason. Only administrators can read it, and nothing can edit it.",
        (),
        "/audit",
    ),
    Topic(
        "account_security",
        "Account and session security",
        ("password", "mfa", "two factor", "2fa", "session", "logout everywhere", "change password", "recovery code"),
        "Account & Security covers password change, TOTP multi-factor authentication with one-time recovery codes, and the list of active sessions you can revoke individually or all at once.",
        (),
        "/account",
    ),
    Topic(
        "vision_provider",
        "Is a vision model being used?",
        ("vision", "ai provider", "gemini", "api key", "which model", "ocr only"),
        "The status pill in the top bar says whether a vision provider is contributing. With no key configured the whole pipeline runs on the on-device OCR engine, and that is a supported configuration.",
        (),
        "/settings",
    ),
    Topic(
        "limitations",
        "What the system cannot do",
        ("limitation", "cannot", "not possible", "accuracy", "trust", "legal authority", "guarantee"),
        "It is inspection-assistance software, not a legal authority: an image cannot prove physical quantity, the price actually charged, or authenticity; missing OCR is never treated as absence; and millimetre sizes need a recorded scale.",
        (),
        "/settings",
    ),
    Topic(
        "admin_tasks",
        "Administrator tasks",
        ("admin", "administrator", "manage users", "system settings", "seeded accounts"),
        "Administrators hold every permission: all portals, the audit log, the rule library, repository backfill for older inspections, and every review action.",
        (),
        "/audit",
        KIND_WORKFLOW,
        ("ADMIN",),
    ),
    Topic(
        "inspector_tasks",
        "Inspector tasks",
        ("inspector tasks", "my work", "what should i do", "review queue", "awaiting review"),
        "Inspectors create inspections, scan packages, correct extracted values, decide violations, record calibrations and finalize decisions. The Dashboard lists what is still awaiting review.",
        (),
        "/dashboard",
        KIND_WORKFLOW,
        ("INSPECTOR", "ENFORCEMENT_OFFICER", "ADMIN"),
    ),
    Topic(
        "higher_official",
        "Higher-official (enforcement) tasks",
        ("higher official", "officer tasks", "enforcement tasks", "override", "escalation"),
        "The enforcement portal lists flagged products, compliance records and enforcement actions, and officers hold the finalization capability for scans below the threshold.",
        (),
        "/enforcement",
        KIND_WORKFLOW,
        ("ENFORCEMENT_OFFICER", "ADMIN"),
    ),
    Topic(
        "pending_visibility",
        "What a normal user sees for an unfinished case",
        ("normal user", "end user", "customer view", "what does the public see", "pending result"),
        "A non-reviewer never sees a pending case as approved or rejected: the scan reports 'This compliance result is pending finalization by the higher officials.' until an official decision exists.",
        (),
        "/entity",
    ),
    Topic(
        "offline",
        "Does it need internet?",
        ("internet", "offline", "no network", "local", "cost", "cloud"),
        "No. OCR, the rule engine, the database and the reports all run locally; a vision provider is optional and the application is fully functional without it.",
        (),
        "/settings",
    ),
)

GLOSSARY: dict[str, str] = {
    "detected": "A value was read from the supplied images with supporting evidence.",
    "missing": "Not found in the SUPPLIED images. This never means the declaration is absent from the package.",
    "uncertain": "A value or check could not be established reliably — it is offered for manual confirmation, never asserted, and never turned into a legal failure.",
    "conflicting": "More than one candidate value was read; all candidates are retained until a human resolves them.",
    "manually_corrected": "A reviewer corrected or added the value; the original extraction stays in the audit trail.",
    "human_confirmed_absent": "A reviewer confirmed by physical examination that the declaration is absent — the only image-side path to a rule failure.",
    "pass": "The requirement was satisfied on the available evidence.",
    "fail": "The requirement failed with sufficient evidence (positive evidence or human confirmation).",
    "not_applicable": "The rule does not apply to this package, and the reason is recorded.",
    "open": "An evidence-backed potential violation awaiting human confirmation.",
    "needs_review": "Manual verification required. This is not a violation.",
    "confirmed": "A reviewer agreed the violation is real.",
    "dismissed": "A reviewer determined it is not a violation; the reason is recorded.",
    "resolved": "The issue was addressed or rectified.",
    "ai_preliminary": "The automated verdict and score, produced before any human finalization.",
    "pending_finalization": "The automated review is complete but awaiting an official final decision.",
    "finalized": "An authorized official recorded the final decision, with remarks.",
    "evidence": "The stored image region, crop and OCR text behind a value.",
    "principal display panel": "The panel of the package that is normally presented for display; Rule 7(4) defines how its area is determined.",
    "mrp": "The retail sale price declared on the package, inclusive of all taxes.",
    "unit sale price": "The price per unit of measurement, which is NOT a substitute for MRP.",
    "inspection": "One package scan: the images, extracted declarations, rule outcomes, review actions and decision.",
    "scan": "A repository entry created from a processed inspection, with its score, verdict and final decision.",
    "compliance score": "The percentage of the requirements that could be decided that were met: PASS counts in full, FAIL not at all, over weighted checks only.",
    "coverage score": "The share of applicable, automatically checkable requirements that the evidence actually allowed us to decide.",
}


SUGGESTED_QUESTIONS: tuple[str, ...] = (
    "How do I scan a product?",
    "What does the AI compliance score mean?",
    "How do I finalize a pending decision?",
    "How do I measure the font size of a label?",
    "What does UNCERTAIN mean?",
)


def topic_by_id(topic_id: str) -> Topic | None:
    return next((t for t in TOPICS if t.id == topic_id), None)
