from datetime import date, timedelta
import pytest

from app.extensions import db
from app.models import (
    AuditLog,
    Bill,
    Category,
    Expense,
    RecurringExpense,
    Reminder,
    User,
    WeeklyDigest,
)
from app.services.privacy import verify_audit_chain


def _register_and_login(client, email: str, password: str = "password123") -> dict:
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code in (200, 201, 409)
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    token = r.get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_gdpr_pii_export_returns_complete_package_and_logs_audit(client, auth_header):
    today = date.today()

    r = client.post("/categories", json={"name": "Healthcare"}, headers=auth_header)
    assert r.status_code == 201
    cat_id = r.get_json()["id"]

    r = client.post(
        "/expenses",
        json={
            "amount": 125.50,
            "description": "Pharmacy",
            "date": today.isoformat(),
            "category_id": cat_id,
            "expense_type": "EXPENSE",
        },
        headers=auth_header,
    )
    assert r.status_code == 201

    r = client.post(
        "/expenses/recurring",
        json={
            "amount": 49.99,
            "description": "Gym Membership",
            "cadence": "MONTHLY",
            "start_date": today.isoformat(),
            "category_id": cat_id,
        },
        headers=auth_header,
    )
    assert r.status_code == 201

    r = client.post(
        "/bills",
        json={
            "name": "Electric Bill",
            "amount": 85.00,
            "next_due_date": (today + timedelta(days=5)).isoformat(),
            "cadence": "MONTHLY",
        },
        headers=auth_header,
    )
    assert r.status_code == 201

    r = client.post(
        "/reminders",
        json={
            "message": "Pay electric bill",
            "send_at": f"{today.isoformat()}T09:00:00",
            "channel": "email",
        },
        headers=auth_header,
    )
    assert r.status_code == 201

    r = client.post("/api/digests/generate", json={}, headers=auth_header)
    assert r.status_code == 201

    r = client.get("/api/privacy/export", headers=auth_header)
    assert r.status_code == 200
    assert "attachment; filename=" in r.headers.get("Content-Disposition", "")
    assert r.headers.get("X-GDPR-Checksum-SHA256")

    pkg = r.get_json()
    assert pkg["schema_version"] == "1.0"
    assert pkg["user"]["email"] == "test@example.com"
    assert pkg["record_counts"]["categories"] == 1
    assert pkg["record_counts"]["expenses"] == 1
    assert pkg["record_counts"]["recurring_expenses"] == 1
    assert pkg["record_counts"]["bills"] == 1
    assert pkg["record_counts"]["reminders"] == 1
    assert pkg["record_counts"]["weekly_digests"] == 1
    assert len(pkg["integrity"]["checksum_sha256"]) == 64
    assert any(log["action"] == "GDPR_PII_EXPORT" for log in pkg["audit_logs"])


def test_gdpr_delete_rejects_unconfirmed_or_invalid_password(client, auth_header):
    r = client.post(
        "/api/privacy/delete",
        json={"confirm": False},
        headers=auth_header,
    )
    assert r.status_code == 400

    r = client.post(
        "/api/privacy/delete",
        json={"confirmation": "CANCEL"},
        headers=auth_header,
    )
    assert r.status_code == 400

    r = client.post(
        "/api/privacy/delete",
        json={"confirm": True, "password": "wrong-password"},
        headers=auth_header,
    )
    assert r.status_code == 401

    me_resp = client.get("/auth/me", headers=auth_header)
    assert me_resp.status_code == 200


def test_gdpr_irreversible_delete_purges_all_pii_and_preserves_audit_chain(
    client, app_fixture
):
    auth_a = _register_and_login(client, "alice.gdpr@example.com")
    auth_b = _register_and_login(client, "bob.keep@example.com")

    today = date.today()
    client.post("/categories", json={"name": "AliceCat"}, headers=auth_a)
    client.post(
        "/expenses",
        json={"amount": 75, "description": "Alice lunch", "date": today.isoformat()},
        headers=auth_a,
    )
    client.post(
        "/bills",
        json={
            "name": "Alice Rent",
            "amount": 1000,
            "next_due_date": today.isoformat(),
            "cadence": "MONTHLY",
        },
        headers=auth_a,
    )
    client.post(
        "/reminders",
        json={"message": "Alice reminder", "send_at": f"{today.isoformat()}T10:00:00"},
        headers=auth_a,
    )
    client.post("/api/digests/generate", json={}, headers=auth_a)

    client.post(
        "/expenses",
        json={"amount": 42, "description": "Bob coffee", "date": today.isoformat()},
        headers=auth_b,
    )

    exp_resp = client.get("/api/privacy/export", headers=auth_a)
    assert exp_resp.status_code == 200
    alice_id = exp_resp.get_json()["user"]["id"]

    del_resp = client.delete(
        "/api/privacy/delete",
        json={
            "confirm": True,
            "confirmation": "DELETE",
            "reason": "exercising right to erasure",
        },
        headers=auth_a,
    )
    assert del_resp.status_code == 200
    receipt = del_resp.get_json()
    assert receipt["status"] == "deleted"
    assert receipt["irreversible"] is True
    assert receipt["purged_counts"]["users"] == 1
    assert receipt["purged_counts"]["expenses"] == 1
    assert receipt["purged_counts"]["bills"] == 1
    assert receipt["purged_counts"]["reminders"] == 1
    assert receipt["purged_counts"]["categories"] == 1
    assert receipt["purged_counts"]["weekly_digests"] == 1
    assert receipt["audit_trail"]["action"] == "GDPR_PII_DELETE"
    assert len(receipt["audit_trail"]["checksum"]) == 64

    # Deleted user's token must be immediately rejected
    post_me = client.get("/auth/me", headers=auth_a)
    assert post_me.status_code == 401

    # Bob's data must remain intact
    bob_expenses = client.get("/expenses", headers=auth_b)
    assert bob_expenses.status_code == 200
    assert len(bob_expenses.get_json()) == 1

    # Database verification for Alice's purged records and immutable audit chain
    with app_fixture.app_context():
        assert db.session.get(User, alice_id) is None
        assert db.session.query(Expense).filter_by(user_id=alice_id).count() == 0
        assert (
            db.session.query(RecurringExpense).filter_by(user_id=alice_id).count() == 0
        )
        assert db.session.query(Bill).filter_by(user_id=alice_id).count() == 0
        assert db.session.query(Reminder).filter_by(user_id=alice_id).count() == 0
        assert db.session.query(Category).filter_by(user_id=alice_id).count() == 0
        assert db.session.query(WeeklyDigest).filter_by(user_id=alice_id).count() == 0

        logs = (
            db.session.query(AuditLog)
            .filter_by(subject_hash=receipt["subject_hash"])
            .order_by(AuditLog.id.asc())
            .all()
        )
        assert len(logs) == 2
        assert [log.action for log in logs] == ["GDPR_PII_EXPORT", "GDPR_PII_DELETE"]
        for log in logs:
            assert log.user_id is None
            assert "alice.gdpr@example.com" not in (log.details or "")
        assert verify_audit_chain() is True


def test_audit_log_immutability_enforced(client, auth_header, app_fixture):
    r = client.get("/api/privacy/export", headers=auth_header)
    assert r.status_code == 200

    with app_fixture.app_context():
        log_entry = db.session.query(AuditLog).first()
        assert log_entry is not None

        log_entry.action = "TAMPERED_ACTION"
        with pytest.raises(ValueError, match="immutable"):
            db.session.commit()
        db.session.rollback()

        log_entry = db.session.query(AuditLog).first()
        db.session.delete(log_entry)
        with pytest.raises(ValueError, match="immutable"):
            db.session.commit()
        db.session.rollback()
