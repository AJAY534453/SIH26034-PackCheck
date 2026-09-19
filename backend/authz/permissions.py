"""Role-based access control — the single source of truth for permissions.

Roles (see `backend.models.enums.UserRole`):

  ROLE 1  ENFORCEMENT_OFFICER   regulatory/enforcement personnel (regulator side)
  ROLE 2  REGULATED_ENTITY      a business subject to regulation (commercial side)
  ROLE 3  INTERNAL_COMPLIANCE   internal corporate compliance personnel

  ADMIN / INSPECTOR / VIEWER    the original internal roles, preserved unchanged

Nothing in the codebase may hard-code a role check for an authorization decision; every protected
operation resolves a PERMISSION here. Permissions are explicit — a role is granted exactly what it
needs, never "everything except …".
"""
from __future__ import annotations

from backend.models.enums import UserRole


class Permission:
    """Explicit, named capabilities."""

    DASHBOARD_VIEW = "dashboard.view"

    INSPECTIONS_VIEW = "inspections.view"
    INSPECTIONS_MANAGE = "inspections.manage"      # create / upload / run pipeline
    INSPECTION_REVIEW = "inspections.review"       # edit fields, decide violations, finalize

    ANALYSIS_RUN = "analysis.run"                  # capture/upload -> enhance -> OCR

    # Automated compliance review + its official finalization. Finalizing is the "higher official"
    # capability: a preliminary AI verdict is NEVER presented as a final decision without it.
    COMPLIANCE_VIEW = "compliance.view"            # read the scan repository and its history
    COMPLIANCE_FINALIZE = "compliance.finalize"    # record the official final decision

    ASSISTANT_USE = "assistant.use"                # in-application AI assistant / helpdesk

    # Online listing / e-commerce: capture a listing, read its declarations, compare it with the
    # package. Kept separate from the compliance-repository permission because reading a listing is
    # a distinct activity from reading a scan record.
    LISTINGS_USE = "listings.use"

    # ROLE 1 — enforcement
    ENFORCEMENT_VIEW = "enforcement.view"          # dashboard, flagged products, compliance info
    ENFORCEMENT_MANAGE = "enforcement.manage"      # enforcement records / decisions

    # ROLE 2 — regulated entity
    ENTITY_VIEW = "entity.view"                    # own organization dashboard / records
    ENTITY_MANAGE = "entity.manage"                # compliance submissions, documents

    # ROLE 3 — internal compliance
    INTERNAL_VIEW = "internal.view"                # internal compliance dashboard / records
    INTERNAL_MANAGE = "internal.manage"            # internal review workflows, audit prep

    REPORTS_VIEW = "reports.view"
    REPORTS_GENERATE = "reports.generate"

    RULES_VIEW = "rules.view"

    AUDIT_VIEW = "audit.view"
    USERS_MANAGE = "users.manage"

    TOOLS_USE = "tools.use"                        # bill scanner, grocery, complaint filing
    TOOLS_ORG_VIEW = "tools.org_view"              # see tools records owned by someone else in scope
    COMPLAINTS_MANAGE = "complaints.manage"        # move a complaint through its lifecycle


_ALL = frozenset(
    v for k, v in vars(Permission).items() if not k.startswith("_") and isinstance(v, str)
)

_REGULATOR_READS = {
    Permission.DASHBOARD_VIEW,
    Permission.INSPECTIONS_VIEW,
    Permission.ENFORCEMENT_VIEW,
    Permission.REPORTS_VIEW,
    Permission.RULES_VIEW,
    Permission.TOOLS_USE,
    Permission.COMPLIANCE_VIEW,
    Permission.ASSISTANT_USE,
}

PERMISSIONS_BY_ROLE: dict[str, frozenset[str]] = {
    # ---- existing internal roles (behaviour preserved) ----
    UserRole.ADMIN.value: _ALL,
    UserRole.INSPECTOR.value: frozenset(
        {
            Permission.DASHBOARD_VIEW,
            Permission.INSPECTIONS_VIEW,
            Permission.INSPECTIONS_MANAGE,
            Permission.INSPECTION_REVIEW,
            Permission.ANALYSIS_RUN,
            Permission.ENFORCEMENT_VIEW,
            Permission.ENFORCEMENT_MANAGE,
            Permission.REPORTS_VIEW,
            Permission.REPORTS_GENERATE,
            Permission.RULES_VIEW,
            Permission.TOOLS_USE,
            Permission.TOOLS_ORG_VIEW,
            Permission.COMPLAINTS_MANAGE,
            Permission.COMPLIANCE_VIEW,
            Permission.COMPLIANCE_FINALIZE,
            Permission.ASSISTANT_USE,
            Permission.LISTINGS_USE,
        }
    ),
    UserRole.VIEWER.value: frozenset(_REGULATOR_READS),
    # ---- ROLE 1: regulatory enforcement ----
    UserRole.ENFORCEMENT_OFFICER.value: frozenset(
        {
            Permission.DASHBOARD_VIEW,
            Permission.INSPECTIONS_VIEW,
            Permission.INSPECTIONS_MANAGE,
            Permission.INSPECTION_REVIEW,
            Permission.ANALYSIS_RUN,
            Permission.ENFORCEMENT_VIEW,
            Permission.ENFORCEMENT_MANAGE,
            Permission.REPORTS_VIEW,
            Permission.REPORTS_GENERATE,
            Permission.RULES_VIEW,
            Permission.TOOLS_USE,
            Permission.TOOLS_ORG_VIEW,
            Permission.COMPLAINTS_MANAGE,
            Permission.COMPLIANCE_VIEW,
            Permission.COMPLIANCE_FINALIZE,
            Permission.ASSISTANT_USE,
            Permission.LISTINGS_USE,
        }
    ),
    # ---- ROLE 2: regulated entity (own organization only; no enforcement) ----
    UserRole.REGULATED_ENTITY.value: frozenset(
        {
            Permission.DASHBOARD_VIEW,
            Permission.INSPECTIONS_VIEW,
            Permission.ANALYSIS_RUN,
            Permission.ENTITY_VIEW,
            Permission.ENTITY_MANAGE,
            Permission.REPORTS_VIEW,
            Permission.RULES_VIEW,
            Permission.TOOLS_USE,
            Permission.COMPLIANCE_VIEW,
            Permission.ASSISTANT_USE,
            Permission.LISTINGS_USE,
        }
    ),
    # ---- ROLE 3: internal corporate compliance (own organization; no enforcement) ----
    UserRole.INTERNAL_COMPLIANCE.value: frozenset(
        {
            Permission.DASHBOARD_VIEW,
            Permission.INSPECTIONS_VIEW,
            Permission.ANALYSIS_RUN,
            Permission.INTERNAL_VIEW,
            Permission.INTERNAL_MANAGE,
            Permission.REPORTS_VIEW,
            Permission.REPORTS_GENERATE,
            Permission.RULES_VIEW,
            Permission.TOOLS_USE,
            Permission.TOOLS_ORG_VIEW,
            Permission.COMPLAINTS_MANAGE,
            Permission.COMPLIANCE_VIEW,
            Permission.COMPLIANCE_FINALIZE,
            Permission.ASSISTANT_USE,
            Permission.LISTINGS_USE,
        }
    ),
}

ALL_PERMISSIONS = _ALL

# Roles whose data access is confined to their own organization. A user in one of these roles
# never sees another organization's rows — enforced in the query, not merely hidden in the UI.
ORG_SCOPED_ROLES = frozenset(
    {UserRole.REGULATED_ENTITY.value, UserRole.INTERNAL_COMPLIANCE.value}
)

ROLE_LABELS: dict[str, str] = {
    UserRole.ADMIN.value: "Administrator",
    UserRole.INSPECTOR.value: "Inspector",
    UserRole.VIEWER.value: "Viewer",
    UserRole.ENFORCEMENT_OFFICER.value: "Enforcement Officer",
    UserRole.REGULATED_ENTITY.value: "Regulated Entity",
    UserRole.INTERNAL_COMPLIANCE.value: "Internal Compliance",
}

# Where an authenticated user lands after login.
ROLE_HOME: dict[str, str] = {
    UserRole.ADMIN.value: "/dashboard",
    UserRole.INSPECTOR.value: "/dashboard",
    UserRole.VIEWER.value: "/dashboard",
    UserRole.ENFORCEMENT_OFFICER.value: "/enforcement",
    UserRole.REGULATED_ENTITY.value: "/entity",
    UserRole.INTERNAL_COMPLIANCE.value: "/internal",
}


def permissions_for(role: str | None) -> frozenset[str]:
    return PERMISSIONS_BY_ROLE.get(role or "", frozenset())


def has_permission(user, permission: str) -> bool:
    """True when the user's role grants the permission (wildcard ``*`` allowed for ADMIN)."""
    if user is None:
        return False
    perms = permissions_for(getattr(user, "role", None))
    return permission in perms or "*" in perms


def role_home(role: str | None) -> str:
    return ROLE_HOME.get(role or "", "/dashboard")


def scope_org_id(user) -> int | None:
    """The organization id that MUST be applied for this user, or None when unrestricted.

    For an org-scoped role we return the user's organization id; when it is missing we return a
    sentinel (-1) so the query matches nothing rather than silently widening to all rows.
    """
    if getattr(user, "role", None) in ORG_SCOPED_ROLES:
        return getattr(user, "organization_id", None) or -1
    return None


def org_scope_filter(query, model, user):
    """Apply organization isolation to a query for a model that has ``organization_id``."""
    if not hasattr(model, "organization_id"):
        return query
    scoped = scope_org_id(user)
    if scoped is None:
        return query
    return query.filter(model.organization_id == scoped)
