"""Activity logging. Every important action is recorded with before/after data."""
import json
import logging

from .database import append_record, new_uuid, now_iso

logger = logging.getLogger("wecare.audit")


def _to_json(data: object) -> str:
    if data in (None, ""):
        return ""
    try:
        return json.dumps(data, default=str, ensure_ascii=False)
    except Exception:
        return str(data)


def log_activity(
    *,
    user: dict | None,
    action: str,
    entity_type: str = "",
    entity_id: str = "",
    description: str = "",
    before: object = None,
    after: object = None,
    ip: str = "",
) -> dict:
    record = {
        "log_id": new_uuid("LOG-"),
        "timestamp": now_iso(),
        "username": str((user or {}).get("username", "system")),
        "role": str((user or {}).get("role", "")),
        "action": action,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "description": description,
        "before_data": _to_json(before),
        "after_data": _to_json(after),
        "ip": ip or "",
    }
    try:
        append_record("activity_logs", record)
    except Exception:
        logger.exception("Failed to write activity log: %s", action)
    return record
