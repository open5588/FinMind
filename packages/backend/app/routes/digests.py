from datetime import date
import json
import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from ..extensions import db
from ..models import WeeklyDigest
from ..services.digest import generate_and_store_weekly_digest, get_weekly_digest

bp = Blueprint("digests", __name__)
logger = logging.getLogger("finmind.digests")


def _parse_optional_date(raw: str | None, field_name: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}, expected YYYY-MM-DD") from exc


@bp.get("/weekly")
@jwt_required()
def weekly_digest():
    uid = int(get_jwt_identity())
    try:
        week_start = _parse_optional_date(request.args.get("week_start"), "week_start")
        week_end = _parse_optional_date(request.args.get("week_end"), "week_end")
        ref_date = _parse_optional_date(
            request.args.get("date") or request.args.get("as_of"), "date"
        )
        digest = get_weekly_digest(
            uid,
            week_start=week_start,
            week_end=week_end,
            reference_date=ref_date,
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(digest), 200


@bp.post("/generate")
@jwt_required()
def generate_weekly_digest():
    uid = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    try:
        week_start = _parse_optional_date(
            data.get("week_start") or request.args.get("week_start"), "week_start"
        )
        week_end = _parse_optional_date(
            data.get("week_end") or request.args.get("week_end"), "week_end"
        )
        ref_date = _parse_optional_date(
            data.get("date")
            or data.get("as_of")
            or request.args.get("date")
            or request.args.get("as_of"),
            "date",
        )
        notify = bool(data.get("notify", False))
        channel = str(data.get("channel") or "email").lower().strip()
        digest = generate_and_store_weekly_digest(
            uid,
            week_start=week_start,
            week_end=week_end,
            reference_date=ref_date,
            notify=notify,
            channel=channel,
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(digest), 201


@bp.get("/history")
@jwt_required()
def list_digest_history():
    uid = int(get_jwt_identity())
    rows = (
        db.session.query(WeeklyDigest)
        .filter_by(user_id=uid)
        .order_by(WeeklyDigest.week_start.desc(), WeeklyDigest.id.desc())
        .limit(52)
        .all()
    )
    return (
        jsonify(
            [
                {
                    "id": r.id,
                    "week_start": r.week_start.isoformat(),
                    "week_end": r.week_end.isoformat(),
                    "currency": r.currency,
                    "total_income": float(r.total_income),
                    "total_expenses": float(r.total_expenses),
                    "net_flow": float(r.net_flow),
                    "wow_change_pct": r.wow_change_pct,
                    "trend_direction": r.trend_direction,
                    "spikes_count": r.spikes_count,
                    "created_at": r.created_at.isoformat(),
                    "digest": json.loads(r.payload_json) if r.payload_json else None,
                }
                for r in rows
            ]
        ),
        200,
    )
