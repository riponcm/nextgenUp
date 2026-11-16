"""Admin audit logging service for recording privileged operations.

Enforces redaction of sensitive credentials, tokens, and secrets from audit entries.
"""

from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from demerivo.db.models import AdminAuditLogModel, utcnow


SENSITIVE_KEYS = {
    "password", "secret", "token", "key", "authorization",
    "access_token", "refresh_token", "admin_secret", "jwt",
    "card", "cvv", "api_key", "secret_key"
}


def sanitize_audit_metadata(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Recursively scrub any sensitive keys from audit log metadata dictionaries."""
    if not data or not isinstance(data, dict):
        return {}

    cleaned: Dict[str, Any] = {}
    for k, v in data.items():
        lower_k = k.lower()
        if any(s in lower_k for s in SENSITIVE_KEYS):
            cleaned[k] = "[REDACTED]"
        elif isinstance(v, dict):
            cleaned[k] = sanitize_audit_metadata(v)
        else:
            cleaned[k] = v
    return cleaned


def record_audit_log(
    db: Session,
    actor_email: str,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    reason: Optional[str] = None,
    status: str = "success",
    metadata: Optional[Dict[str, Any]] = None,
    actor_id: Optional[str] = None,
) -> AdminAuditLogModel:
    """Record an immutable administrative audit trail event."""
    safe_metadata = sanitize_audit_metadata(metadata)

    log_entry = AdminAuditLogModel(
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        status=status,
        metadata_json=safe_metadata,
        created_at=utcnow(),
    )
    db.add(log_entry)
    db.flush()
    return log_entry
