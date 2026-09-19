"""Import all ORM models into this package so SQLAlchemy metadata sees every table."""
from backend.models.audit_log import AuditLog
from backend.models.auth_session import AuthSession, AuthToken
from backend.models.classification import Classification
from backend.models.evidence import Evidence
from backend.models.extracted_field import ExtractedField
from backend.models.field_candidate import FieldCandidate
from backend.models.image_analysis import ImageAnalysis
from backend.models.inspection import Inspection
from backend.models.inspection_image import InspectionImage
from backend.models.inspection_revision import InspectionRevision
from backend.models.ocr_result import OcrResult
from backend.models.organization import Organization
from backend.models.product import Product
from backend.models.product_listing import ProductListing
from backend.models.product_scan import ProductScan
from backend.models.report import Report
from backend.models.review_action import ReviewAction
from backend.models.rule_evaluation import RuleEvaluation
from backend.models.rules import Rule, RuleVersion
from backend.models.tools import Bill, Complaint, GroceryItem
from backend.models.user import User
from backend.models.violation import Violation
from backend.models.vision_region import VisionRegion

__all__ = [
    "AuditLog",
    "AuthSession",
    "AuthToken",
    "Bill",
    "Classification",
    "Complaint",
    "GroceryItem",
    "Evidence",
    "ExtractedField",
    "FieldCandidate",
    "ImageAnalysis",
    "Inspection",
    "InspectionImage",
    "InspectionRevision",
    "OcrResult",
    "Organization",
    "Product",
    "ProductScan",
    "Report",
    "ReviewAction",
    "Rule",
    "RuleVersion",
    "RuleEvaluation",
    "User",
    "Violation",
    "VisionRegion",
]
