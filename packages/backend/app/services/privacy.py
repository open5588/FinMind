from datetime import datetime, timezone
import hashlib
import json
import logging

from ..extensions import db, redis_client
from ..models import (
    AdImpression,
    AuditLog,
    Bill,
    Category,
    Expense,
    RecurringExpense,
    Reminder,
    User,
    UserSubscription,
    WeeklyDigest,
)
from .cache import cache_delete_patterns

logger = logging.getLogger("finmind.privacy")


def compute_subject_hash(user_id: int, email: str | None = None) -> str:
    normalized_email = (email or "").strip().lower()
    raw = f"finmind:gdpr:subject:{user_id}:{normalized_email}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compute_audit_checksum(
    prev_checksum: str,
    action: str,
    subject_hash: str,
    created_iso: str,
    details_json: str,
) -> str:
    material = f"{prev_checksum}|{action}|{subject_hash}|{created_iso}|{details_json}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def record_audit_log(
    *,
    action: str,
    user_id: int | None,
    subject_hash: str,
    details: dict | None = None,
) -> AuditLog:
    last_log = db.session.query(AuditLog).order_by(AuditLog.id.desc()).first()
    prev_checksum = (last_log.checksum if last_log and last_log.checksum else None) or (
        "GENESIS"
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    created_iso = now.isoformat()
    details_payload = dict(details or {})
    details_payload["prev_checksum"] = prev_checksum
    details_json = json.dumps(details_payload, sort_keys=True, separators=(",", ":"))
    checksum = _compute_audit_checksum(
        prev_checksum=prev_checksum,
        action=action,
        subject_hash=subject_hash,
        created_iso=created_iso,
        details_json=details_json,
    )
    entry = AuditLog(
        user_id=user_id,
        subject_hash=subject_hash,
        action=action,
        details=details_json,
        checksum=checksum,
        created_at=now,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def verify_audit_chain() -> bool:
    logs = db.session.query(AuditLog).order_by(AuditLog.id.asc()).all()
    prev_checksum = "GENESIS"
    for entry in logs:
        if not entry.checksum or not entry.details or not entry.subject_hash:
            continue
        expected = _compute_audit_checksum(
            prev_checksum=prev_checksum,
            action=entry.action,
            subject_hash=entry.subject_hash,
            created_iso=entry.created_at.isoformat(),
            details_json=entry.details,
        )
        if entry.checksum != expected:
            return False
        prev_checksum = entry.checksum
    return True


def build_pii_export_package(uid: int) -> dict:
    user = db.session.get(User, uid)
    if not user:
        raise LookupError("user not found")

    subject_hash = compute_subject_hash(user.id, user.email)

    categories = (
        db.session.query(Category)
        .filter_by(user_id=uid)
        .order_by(Category.id.asc())
        .all()
    )
    expenses = (
        db.session.query(Expense)
        .filter_by(user_id=uid)
        .order_by(Expense.spent_at.desc(), Expense.id.desc())
        .all()
    )
    recurring_expenses = (
        db.session.query(RecurringExpense)
        .filter_by(user_id=uid)
        .order_by(RecurringExpense.id.asc())
        .all()
    )
    bills = (
        db.session.query(Bill)
        .filter_by(user_id=uid)
        .order_by(Bill.next_due_date.asc(), Bill.id.asc())
        .all()
    )
    reminders = (
        db.session.query(Reminder)
        .filter_by(user_id=uid)
        .order_by(Reminder.send_at.asc(), Reminder.id.asc())
        .all()
    )
    subscriptions = (
        db.session.query(UserSubscription)
        .filter_by(user_id=uid)
        .order_by(UserSubscription.id.asc())
        .all()
    )
    ad_impressions = (
        db.session.query(AdImpression)
        .filter_by(user_id=uid)
        .order_by(AdImpression.id.asc())
        .all()
    )
    digests = (
        db.session.query(WeeklyDigest)
        .filter_by(user_id=uid)
        .order_by(WeeklyDigest.week_start.desc(), WeeklyDigest.id.desc())
        .all()
    )

    counts = {
        "categories": len(categories),
        "expenses": len(expenses),
        "recurring_expenses": len(recurring_expenses),
        "bills": len(bills),
        "reminders": len(reminders),
        "subscriptions": len(subscriptions),
        "ad_impressions": len(ad_impressions),
        "weekly_digests": len(digests),
    }
    counts["total_records"] = sum(counts.values()) + 1

    audit_entry = record_audit_log(
        action="GDPR_PII_EXPORT",
        user_id=uid,
        subject_hash=subject_hash,
        details={"record_counts": counts},
    )
    db.session.commit()

    audit_logs = (
        db.session.query(AuditLog)
        .filter((AuditLog.user_id == uid) | (AuditLog.subject_hash == subject_hash))
        .order_by(AuditLog.id.asc())
        .all()
    )

    exported_at = datetime.now(timezone.utc).isoformat()
    user_payload = {
        "id": user.id,
        "email": user.email,
        "preferred_currency": user.preferred_currency,
        "role": user.role,
        "created_at": user.created_at.isoformat(),
    }

    data_section = {
        "user": user_payload,
        "profile": user_payload,
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                "created_at": c.created_at.isoformat(),
            }
            for c in categories
        ],
        "expenses": [
            {
                "id": e.id,
                "amount": float(e.amount),
                "currency": e.currency,
                "expense_type": e.expense_type,
                "category_id": e.category_id,
                "description": e.notes or "",
                "notes": e.notes or "",
                "date": e.spent_at.isoformat(),
                "spent_at": e.spent_at.isoformat(),
                "source_recurring_id": e.source_recurring_id,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in expenses
        ],
        "recurring_expenses": [
            {
                "id": r.id,
                "amount": float(r.amount),
                "currency": r.currency,
                "expense_type": r.expense_type,
                "category_id": r.category_id,
                "description": r.notes,
                "notes": r.notes,
                "cadence": r.cadence.value,
                "start_date": r.start_date.isoformat(),
                "end_date": r.end_date.isoformat() if r.end_date else None,
                "active": r.active,
                "created_at": r.created_at.isoformat(),
            }
            for r in recurring_expenses
        ],
        "bills": [
            {
                "id": b.id,
                "name": b.name,
                "amount": float(b.amount),
                "currency": b.currency,
                "next_due_date": b.next_due_date.isoformat(),
                "cadence": b.cadence.value,
                "autopay_enabled": b.autopay_enabled,
                "channel_email": b.channel_email,
                "channel_whatsapp": b.channel_whatsapp,
                "active": b.active,
                "created_at": b.created_at.isoformat(),
            }
            for b in bills
        ],
        "reminders": [
            {
                "id": rem.id,
                "bill_id": rem.bill_id,
                "message": rem.message,
                "send_at": rem.send_at.isoformat(),
                "sent": rem.sent,
                "channel": rem.channel,
            }
            for rem in reminders
        ],
        "subscriptions": [
            {
                "id": s.id,
                "plan_id": s.plan_id,
                "active": s.active,
                "started_at": s.started_at.isoformat(),
            }
            for s in subscriptions
        ],
        "ad_impressions": [
            {
                "id": a.id,
                "placement": a.placement,
                "created_at": a.created_at.isoformat(),
            }
            for a in ad_impressions
        ],
        "weekly_digests": [
            {
                "id": d.id,
                "week_start": d.week_start.isoformat(),
                "week_end": d.week_end.isoformat(),
                "currency": d.currency,
                "total_income": float(d.total_income),
                "total_expenses": float(d.total_expenses),
                "net_flow": float(d.net_flow),
                "wow_change_pct": d.wow_change_pct,
                "trend_direction": d.trend_direction,
                "spikes_count": d.spikes_count,
                "created_at": d.created_at.isoformat(),
            }
            for d in digests
        ],
        "audit_logs": [
            {
                "id": log.id,
                "action": log.action,
                "subject_hash": log.subject_hash,
                "details": json.loads(log.details) if log.details else None,
                "checksum": log.checksum,
                "created_at": log.created_at.isoformat(),
            }
            for log in audit_logs
        ],
    }

    canonical_bytes = json.dumps(
        data_section, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    package_checksum = hashlib.sha256(canonical_bytes).hexdigest()

    return {
        "schema_version": "1.0",
        "exported_at": exported_at,
        "gdpr_metadata": {
            "regulation": "EU GDPR",
            "articles": [
                "Article 15 (Right of access by the data subject)",
                "Article 20 (Right to data portability)",
            ],
            "controller": "FinMind",
            "subject_hash": subject_hash,
            "audit_log_id": audit_entry.id,
            "audit_checksum": audit_entry.checksum,
        },
        "record_counts": counts,
        "integrity": {
            "algorithm": "SHA-256",
            "checksum_sha256": package_checksum,
        },
        **data_section,
        "data": data_section,
    }


def execute_irreversible_delete(
    uid: int,
    *,
    reason: str | None = None,
    jti: str | None = None,
) -> dict:
    user = db.session.get(User, uid)
    if not user:
        raise LookupError("user not found")

    subject_hash = compute_subject_hash(user.id, user.email)

    purged_counts = {
        "expenses": db.session.query(Expense)
        .filter_by(user_id=uid)
        .delete(synchronize_session=False),
        "recurring_expenses": db.session.query(RecurringExpense)
        .filter_by(user_id=uid)
        .delete(synchronize_session=False),
        "reminders": db.session.query(Reminder)
        .filter_by(user_id=uid)
        .delete(synchronize_session=False),
        "bills": db.session.query(Bill)
        .filter_by(user_id=uid)
        .delete(synchronize_session=False),
        "categories": db.session.query(Category)
        .filter_by(user_id=uid)
        .delete(synchronize_session=False),
        "subscriptions": db.session.query(UserSubscription)
        .filter_by(user_id=uid)
        .delete(synchronize_session=False),
        "ad_impressions": db.session.query(AdImpression)
        .filter_by(user_id=uid)
        .delete(synchronize_session=False),
        "weekly_digests": db.session.query(WeeklyDigest)
        .filter_by(user_id=uid)
        .delete(synchronize_session=False),
    }

    # Anonymize foreign key on any historical audit logs while keeping them immutable
    prior_logs = db.session.query(AuditLog).filter_by(user_id=uid).all()
    for log in prior_logs:
        log.user_id = None

    db.session.delete(user)
    purged_counts["users"] = 1
    purged_counts["total_purged"] = sum(purged_counts.values())

    audit_entry = record_audit_log(
        action="GDPR_PII_DELETE",
        user_id=None,
        subject_hash=subject_hash,
        details={
            "irreversible": True,
            "purged_counts": purged_counts,
            "reason": (reason or "user_requested").strip()[:200],
        },
    )
    db.session.commit()

    # Purge cached data and revoke active JWT tokens for this user
    cache_delete_patterns(
        [
            f"user:{uid}:*",
            f"insights:{uid}:*",
            f"digests:{uid}:*",
        ]
    )
    redis_client.setex(f"auth:deleted_user:{uid}", 86400 * 30, "1")
    if jti:
        redis_client.setex(f"auth:revoked_token:{jti}", 86400 * 7, "1")

    logger.info(
        "Executed irreversible GDPR delete subject_hash=%s total_purged=%s",
        subject_hash,
        purged_counts["total_purged"],
    )

    return {
        "status": "deleted",
        "message": "All personal data has been permanently and irreversibly deleted.",
        "irreversible": True,
        "deleted_at": audit_entry.created_at.isoformat(),
        "subject_hash": subject_hash,
        "purged_counts": purged_counts,
        "audit_trail": {
            "audit_log_id": audit_entry.id,
            "action": audit_entry.action,
            "subject_hash": audit_entry.subject_hash,
            "checksum": audit_entry.checksum,
            "created_at": audit_entry.created_at.isoformat(),
        },
    }
