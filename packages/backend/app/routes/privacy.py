import json
import logging

from flask import Blueprint, jsonify, make_response, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from werkzeug.security import check_password_hash

from ..extensions import db
from ..models import AuditLog, User
from ..services.privacy import (
    build_pii_export_package,
    compute_subject_hash,
    execute_irreversible_delete,
    verify_audit_chain,
)

bp = Blueprint("privacy", __name__)
logger = logging.getLogger("finmind.privacy")

VALID_CONFIRMATIONS = {
    "DELETE",
    "PERMANENTLY DELETE",
    "CONFIRM",
    "TRUE",
    "YES",
    "1",
}


@bp.route("/export", methods=["GET", "POST"])
@jwt_required()
def export_user_data():
    uid = int(get_jwt_identity())
    try:
        package = build_pii_export_package(uid)
    except LookupError:
        return jsonify(error="user not found"), 404

    resp = make_response(jsonify(package), 200)
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="finmind-pii-export-{uid}.json"'
    )
    resp.headers["X-GDPR-Checksum-SHA256"] = package["integrity"]["checksum_sha256"]
    return resp


@bp.route("/delete", methods=["POST", "DELETE"])
@jwt_required()
def delete_user_data():
    uid = int(get_jwt_identity())
    user = db.session.get(User, uid)
    if not user:
        return jsonify(error="user not found"), 404

    data = request.get_json(silent=True) or {}

    if "confirm" in data:
        confirm_val = data.get("confirm")
        if confirm_val is False or str(confirm_val).strip().upper() in {
            "FALSE",
            "NO",
            "0",
            "",
        }:
            return (
                jsonify(
                    error="explicit confirmation required for irreversible deletion"
                ),
                400,
            )

    if "confirmation" in data:
        token = str(data.get("confirmation") or "").strip().upper()
        if token not in VALID_CONFIRMATIONS:
            return (
                jsonify(error="invalid confirmation value, expected DELETE"),
                400,
            )

    if "password" in data and data.get("password") is not None:
        if not check_password_hash(user.password_hash, str(data.get("password"))):
            return jsonify(error="invalid password"), 401

    claims = get_jwt()
    jti = claims.get("jti")
    reason = data.get("reason")

    try:
        receipt = execute_irreversible_delete(uid, reason=reason, jti=jti)
    except LookupError:
        return jsonify(error="user not found"), 404

    return jsonify(receipt), 200


@bp.get("/audit-logs")
@jwt_required()
def list_privacy_audit_logs():
    uid = int(get_jwt_identity())
    user = db.session.get(User, uid)
    subject_hash = compute_subject_hash(uid, user.email if user else None)
    logs = (
        db.session.query(AuditLog)
        .filter((AuditLog.user_id == uid) | (AuditLog.subject_hash == subject_hash))
        .order_by(AuditLog.id.asc())
        .all()
    )
    return jsonify(
        {
            "chain_valid": verify_audit_chain(),
            "audit_logs": [
                {
                    "id": log.id,
                    "action": log.action,
                    "subject_hash": log.subject_hash,
                    "details": json.loads(log.details) if log.details else None,
                    "checksum": log.checksum,
                    "created_at": log.created_at.isoformat(),
                }
                for log in logs
            ],
        }
    )
