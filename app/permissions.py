"""Role-based access control."""
import json

ALL_PERMISSIONS = [
    "view_dashboard",
    "view_patients",
    "create_patient",
    "edit_patient",
    "view_services",
    "manage_services",
    "view_equipment",
    "manage_equipment",
    "create_bill",
    "edit_bill",
    "cancel_bill",
    "duplicate_bill",
    "view_payments",
    "create_payment",
    "refund_payment",
    "view_reports",
    "view_profit_report",
    "manage_quotations",
    "print_documents",
    "generate_pdf",
    "share_whatsapp",
    "manage_staff",
    "manage_settings",
    "view_activity_logs",
    "allow_backdate_billing",
]

PERMISSION_LABELS = {
    "view_dashboard": "View Dashboard",
    "view_patients": "View Patients",
    "create_patient": "Create Patient",
    "edit_patient": "Edit Patient",
    "view_services": "View Services",
    "manage_services": "Manage Services (create/edit)",
    "view_equipment": "View Equipment",
    "manage_equipment": "Manage Equipment (create/edit/issue/return)",
    "create_bill": "Create Bill",
    "edit_bill": "Edit Bill",
    "cancel_bill": "Cancel Bill",
    "duplicate_bill": "Duplicate Bill",
    "view_payments": "View Payments",
    "create_payment": "Create Payment",
    "refund_payment": "Refund Payment",
    "view_reports": "View Reports",
    "view_profit_report": "View Profit Report",
    "manage_quotations": "Manage Quotations",
    "print_documents": "Print Documents",
    "generate_pdf": "Generate PDF",
    "share_whatsapp": "Share on WhatsApp",
    "manage_staff": "Manage Staff & Permissions",
    "manage_settings": "Manage Settings & Backup",
    "view_activity_logs": "View Activity Logs",
    "allow_backdate_billing": "Allow Back-date Billing",
}

# Default permissions for the built-in "staff" role. Admin has everything.
DEFAULT_STAFF_PERMISSIONS = [
    "view_dashboard",
    "view_patients",
    "create_patient",
    "edit_patient",
    "view_services",
    "view_equipment",
    "create_bill",
    "view_payments",
    "create_payment",
    "view_reports",
    "manage_quotations",
    "print_documents",
    "generate_pdf",
    "share_whatsapp",
]


def parse_permissions(raw: object) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(p) for p in raw if str(p) in ALL_PERMISSIONS]
    try:
        data = json.loads(str(raw))
        if isinstance(data, list):
            return [str(p) for p in data if str(p) in ALL_PERMISSIONS]
    except Exception:
        pass
    return []


def dump_permissions(perms: list[str]) -> str:
    return json.dumps([p for p in perms if p in ALL_PERMISSIONS])


def effective_permissions(user: dict | None, role_permissions: list[str] | None = None) -> set[str]:
    """Resolve the permission set for a user dict."""
    if not user:
        return set()
    if str(user.get("role", "")).lower() == "admin":
        return set(ALL_PERMISSIONS)
    explicit = parse_permissions(user.get("permissions"))
    if explicit:
        return set(explicit)
    return set(role_permissions or DEFAULT_STAFF_PERMISSIONS)


def has_permission(user: dict | None, permission: str, role_permissions: list[str] | None = None) -> bool:
    return permission in effective_permissions(user, role_permissions)


def is_admin(user: dict | None) -> bool:
    return bool(user) and str(user.get("role", "")).lower() == "admin"
