"""
Audit Service - SQLAlchemy event listeners for automatic audit logging.

Tracks changes to sensitive models (ChemicalAnalysis, MechanicalTest, Pipe, PipeStage).
"""
from sqlalchemy import event, inspect
from flask import g, request
from flask_login import current_user


# Models to track
TRACKED_MODELS = set()


def get_current_user_id():
    """Get current user ID safely"""
    try:
        if current_user and current_user.is_authenticated:
            return current_user.id
    except Exception:
        pass
    return None


def get_request_reason():
    """Reason supplied by the current request's form (set in before_request)."""
    try:
        reason = getattr(g, 'audit_reason', None)
        if reason:
            return reason
    except Exception:
        pass
    return None


def _short(value, limit=60):
    text = str(value)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def build_auto_reason(action, table_name, changes=None):
    """Human-readable fallback reason when the request supplied none.

    Example: "Auto: decision Accept → Reject; stage_date 2026-07-13 → 2026-07-18
    via stages.update_stage"
    """
    try:
        endpoint = request.endpoint or 'unknown'
    except Exception:
        endpoint = 'unknown'

    if action == 'UPDATE' and changes:
        parts = []
        for field, (old, new) in list(changes.items())[:5]:
            if old is None:
                parts.append(f"{field} set to {_short(new)}")
            elif new is None:
                parts.append(f"{field} cleared (was {_short(old)})")
            else:
                parts.append(f"{field} {_short(old)} → {_short(new)}")
        summary = '; '.join(parts)
        if len(changes) > 5:
            summary += f" (+{len(changes) - 5} more)"
        return f"Auto: {summary} via {endpoint}"

    return f"Auto: {action.lower()} via {endpoint}"


def get_model_changes(instance):
    """Get changed fields with old and new values"""
    changes = {}
    insp = inspect(instance)

    for attr in insp.attrs:
        hist = attr.history
        if hist.has_changes():
            key = attr.key
            # Skip relationship and internal fields
            if key.startswith('_') or key in ('created_at', 'updated_at'):
                continue
            old = hist.deleted[0] if hist.deleted else None
            new = hist.added[0] if hist.added else None
            if old != new:
                changes[key] = (old, new)

    return changes


def after_insert_listener(mapper, connection, target):
    """Log record creation"""
    from app.models.audit import AuditLog
    from app import db

    table_name = target.__tablename__
    record_id = target.id if hasattr(target, 'id') else 0

    try:
        AuditLog.log_create(
            table_name=table_name,
            record_id=record_id,
            user_id=get_current_user_id(),
            reason=get_request_reason() or build_auto_reason('CREATE', table_name)
        )
    except Exception:
        pass  # Don't break the main operation


def after_update_listener(mapper, connection, target):
    """Log record updates with field-level changes"""
    from app.models.audit import AuditLog

    changes = get_model_changes(target)
    if not changes:
        return

    table_name = target.__tablename__
    record_id = target.id if hasattr(target, 'id') else 0

    try:
        AuditLog.log_update(
            table_name=table_name,
            record_id=record_id,
            changes=changes,
            user_id=get_current_user_id(),
            reason=get_request_reason() or build_auto_reason('UPDATE', table_name, changes)
        )
    except Exception:
        pass


def after_delete_listener(mapper, connection, target):
    """Log record deletion"""
    from app.models.audit import AuditLog

    table_name = target.__tablename__
    record_id = target.id if hasattr(target, 'id') else 0

    try:
        AuditLog.log_delete(
            table_name=table_name,
            record_id=record_id,
            user_id=get_current_user_id(),
            reason=get_request_reason() or build_auto_reason('DELETE', table_name)
        )
    except Exception:
        pass


def register_audit_listeners(app):
    """Register SQLAlchemy event listeners on tracked models"""
    from app.models.chemical import ChemicalAnalysis
    from app.models.mechanical import MechanicalTest
    from app.models.pipe import Pipe, PipeStage
    from app.models.production_order import ProductionOrder
    from app.models.product import Mold, Product, Customer
    from app.models.stage_decision_type import StageDecisionType

    tracked = [
        ChemicalAnalysis,
        MechanicalTest,
        Pipe,
        PipeStage,
        ProductionOrder,
        Mold,
        Product,
        Customer,
        StageDecisionType,
    ]

    for model in tracked:
        if model not in TRACKED_MODELS:
            event.listen(model, 'after_insert', after_insert_listener)
            event.listen(model, 'after_update', after_update_listener)
            event.listen(model, 'after_delete', after_delete_listener)
            TRACKED_MODELS.add(model)
