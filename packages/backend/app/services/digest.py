from datetime import date, datetime, timedelta, timezone
import json
import logging
import math

from ..extensions import db
from ..models import Bill, Category, Expense, Reminder, User, WeeklyDigest
from .cache import cache_delete_patterns, cache_get, cache_set

logger = logging.getLogger("finmind.digest")


def weekly_digest_cache_key(uid: int, week_start: date, week_end: date) -> str:
    return f"digests:{uid}:{week_start.isoformat()}:{week_end.isoformat()}"


def resolve_week_window(
    *,
    week_start: date | None = None,
    week_end: date | None = None,
    reference_date: date | None = None,
) -> tuple[date, date]:
    if week_start and week_end:
        if week_end < week_start:
            raise ValueError("week_end must be on or after week_start")
        return week_start, week_end
    if week_start:
        return week_start, week_start + timedelta(days=6)
    end = week_end or reference_date or date.today()
    return end - timedelta(days=6), end


def _trend_direction(change_pct: float, threshold: float = 5.0) -> str:
    if change_pct > threshold:
        return "UP"
    if change_pct < -threshold:
        return "DOWN"
    return "STABLE"


def _pct_change(current: float, baseline: float) -> float:
    if baseline > 0:
        return round(((current - baseline) / baseline) * 100.0, 2)
    if current > 0:
        return 100.0
    return 0.0


def calculate_weekly_digest(
    uid: int,
    *,
    week_start: date | None = None,
    week_end: date | None = None,
    reference_date: date | None = None,
) -> dict:
    w_start, w_end = resolve_week_window(
        week_start=week_start,
        week_end=week_end,
        reference_date=reference_date,
    )
    window_days = max(1, (w_end - w_start).days + 1)
    prev_end = w_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=window_days - 1)
    baseline_end = prev_end
    baseline_start = w_start - timedelta(days=window_days * 4)

    user = db.session.get(User, uid)
    currency = (user.preferred_currency if user else None) or "INR"

    categories = db.session.query(Category).filter(Category.user_id == uid).all()
    cat_map: dict[int | None, str] = {c.id: c.name for c in categories}
    cat_map[None] = "Uncategorized"

    all_window_txs = (
        db.session.query(Expense)
        .filter(
            Expense.user_id == uid,
            Expense.spent_at >= baseline_start,
            Expense.spent_at <= w_end,
        )
        .order_by(Expense.spent_at.asc(), Expense.id.asc())
        .all()
    )

    current_txs = [tx for tx in all_window_txs if w_start <= tx.spent_at <= w_end]
    prev_txs = [tx for tx in all_window_txs if prev_start <= tx.spent_at <= prev_end]
    baseline_txs = [
        tx for tx in all_window_txs if baseline_start <= tx.spent_at <= baseline_end
    ]

    cur_income = round(
        sum(float(tx.amount) for tx in current_txs if tx.expense_type == "INCOME"),
        2,
    )
    cur_expense_txs = [tx for tx in current_txs if tx.expense_type != "INCOME"]
    cur_expenses = round(sum(float(tx.amount) for tx in cur_expense_txs), 2)
    cur_net = round(cur_income - cur_expenses, 2)

    prev_income = round(
        sum(float(tx.amount) for tx in prev_txs if tx.expense_type == "INCOME"),
        2,
    )
    prev_expense_txs = [tx for tx in prev_txs if tx.expense_type != "INCOME"]
    prev_expenses = round(sum(float(tx.amount) for tx in prev_expense_txs), 2)
    prev_net = round(prev_income - prev_expenses, 2)

    baseline_expense_txs = [tx for tx in baseline_txs if tx.expense_type != "INCOME"]
    baseline_total_expenses = round(
        sum(float(tx.amount) for tx in baseline_expense_txs), 2
    )
    rolling_4_week_avg = round(baseline_total_expenses / 4.0, 2)

    wow_change_pct = _pct_change(cur_expenses, prev_expenses)
    wow_delta = round(cur_expenses - prev_expenses, 2)
    direction = _trend_direction(wow_change_pct)
    vs_4_week_avg_pct = _pct_change(cur_expenses, rolling_4_week_avg)

    daily_average_spend = round(cur_expenses / float(window_days), 2)
    savings_rate_pct = (
        round(((cur_income - cur_expenses) / cur_income) * 100.0, 2)
        if cur_income > 0
        else 0.0
    )

    # Category aggregation for current, previous, and 4-week baseline
    cur_cat_totals: dict[int | None, float] = {}
    for tx in cur_expense_txs:
        cur_cat_totals[tx.category_id] = round(
            cur_cat_totals.get(tx.category_id, 0.0) + float(tx.amount), 2
        )

    prev_cat_totals: dict[int | None, float] = {}
    for tx in prev_expense_txs:
        prev_cat_totals[tx.category_id] = round(
            prev_cat_totals.get(tx.category_id, 0.0) + float(tx.amount), 2
        )

    base_cat_totals: dict[int | None, float] = {}
    for tx in baseline_expense_txs:
        base_cat_totals[tx.category_id] = round(
            base_cat_totals.get(tx.category_id, 0.0) + float(tx.amount), 2
        )

    all_cat_ids = set(cur_cat_totals.keys()) | set(prev_cat_totals.keys())
    category_trends = []
    for cid in all_cat_ids:
        c_amt = round(cur_cat_totals.get(cid, 0.0), 2)
        p_amt = round(prev_cat_totals.get(cid, 0.0), 2)
        delta = round(c_amt - p_amt, 2)
        c_pct = _pct_change(c_amt, p_amt)
        share_pct = (
            round((c_amt / cur_expenses) * 100.0, 2) if cur_expenses > 0 else 0.0
        )
        category_trends.append(
            {
                "category_id": cid,
                "category_name": cat_map.get(cid, "Uncategorized"),
                "current_amount": c_amt,
                "previous_amount": p_amt,
                "delta": delta,
                "change_pct": c_pct,
                "share_pct": share_pct,
                "direction": _trend_direction(c_pct),
            }
        )
    category_trends.sort(key=lambda r: (r["current_amount"], r["delta"]), reverse=True)

    top_categories = [
        {
            "category_id": r["category_id"],
            "category_name": r["category_name"],
            "amount": r["current_amount"],
            "share_pct": r["share_pct"],
            "change_pct": r["change_pct"],
        }
        for r in category_trends
        if r["current_amount"] > 0
    ][:3]

    # Daily breakdown
    daily_breakdown = []
    for day_offset in range(window_days):
        d = w_start + timedelta(days=day_offset)
        day_exp = round(
            sum(float(tx.amount) for tx in cur_expense_txs if tx.spent_at == d),
            2,
        )
        day_inc = round(
            sum(
                float(tx.amount)
                for tx in current_txs
                if tx.expense_type == "INCOME" and tx.spent_at == d
            ),
            2,
        )
        daily_breakdown.append(
            {
                "date": d.isoformat(),
                "expenses": day_exp,
                "income": day_inc,
                "net": round(day_inc - day_exp, 2),
            }
        )

    # Spike detection engine
    spikes: list[dict] = []

    # 1. Category spend spikes
    for row in category_trends:
        cid = row["category_id"]
        c_amt = row["current_amount"]
        if c_amt <= 0:
            continue
        p_amt = row["previous_amount"]
        b_avg = round(base_cat_totals.get(cid, 0.0) / 4.0, 2)
        baseline_ref = p_amt if p_amt > 0 else b_avg
        if baseline_ref > 0:
            inc_pct = round(((c_amt - baseline_ref) / baseline_ref) * 100.0, 2)
            if inc_pct >= 25.0 and (c_amt - baseline_ref) >= 10.0:
                severity = "HIGH" if inc_pct >= 50.0 else "MEDIUM"
                spikes.append(
                    {
                        "type": "CATEGORY_SPIKE",
                        "category_id": cid,
                        "category_name": row["category_name"],
                        "amount": c_amt,
                        "baseline_amount": baseline_ref,
                        "increase_pct": inc_pct,
                        "severity": severity,
                        "reason": (
                            f"{row['category_name']} spending rose {inc_pct}% "
                            f"({c_amt:.2f} vs {baseline_ref:.2f} baseline)."
                        ),
                    }
                )

    # 2. Single-transaction outlier spikes
    if len(cur_expense_txs) >= 2:
        amounts = [float(tx.amount) for tx in cur_expense_txs]
        mean_amt = sum(amounts) / len(amounts)
        variance = sum((x - mean_amt) ** 2 for x in amounts) / len(amounts)
        std_amt = math.sqrt(variance)
        threshold = max(mean_amt * 1.75, mean_amt + 1.2 * std_amt)
        for tx in cur_expense_txs:
            amt = float(tx.amount)
            if amt >= threshold and amt >= 25.0:
                ratio = round(amt / mean_amt, 2) if mean_amt > 0 else 1.0
                inc_pct = round((ratio - 1.0) * 100.0, 2)
                spikes.append(
                    {
                        "type": "TRANSACTION_SPIKE",
                        "expense_id": tx.id,
                        "description": tx.notes or "Expense",
                        "category_id": tx.category_id,
                        "category_name": cat_map.get(tx.category_id, "Uncategorized"),
                        "amount": round(amt, 2),
                        "baseline_amount": round(mean_amt, 2),
                        "increase_pct": inc_pct,
                        "ratio_to_avg": ratio,
                        "date": tx.spent_at.isoformat(),
                        "severity": "HIGH" if ratio >= 2.5 else "MEDIUM",
                        "reason": (
                            f"Large transaction '{tx.notes or 'Expense'}' "
                            f"({amt:.2f}) is "
                            f"{ratio}x the weekly average transaction ({mean_amt:.2f})."
                        ),
                    }
                )
    elif len(cur_expense_txs) == 1 and prev_expenses > 0:
        tx = cur_expense_txs[0]
        amt = float(tx.amount)
        if amt >= prev_expenses * 1.5 and (amt - prev_expenses) >= 25.0:
            ratio = round(amt / prev_expenses, 2)
            inc_pct = _pct_change(amt, prev_expenses)
            spikes.append(
                {
                    "type": "TRANSACTION_SPIKE",
                    "expense_id": tx.id,
                    "description": tx.notes or "Expense",
                    "category_id": tx.category_id,
                    "category_name": cat_map.get(tx.category_id, "Uncategorized"),
                    "amount": round(amt, 2),
                    "baseline_amount": prev_expenses,
                    "increase_pct": inc_pct,
                    "ratio_to_avg": ratio,
                    "date": tx.spent_at.isoformat(),
                    "severity": "HIGH" if ratio >= 2.0 else "MEDIUM",
                    "reason": (
                        f"Single transaction '{tx.notes or 'Expense'}' ({amt:.2f}) "
                        f"exceeded previous week spend ({prev_expenses:.2f}) "
                        f"by {inc_pct}%."
                    ),
                }
            )

    # Upcoming bills due in the next 7 days from week_end
    upcoming_horizon = w_end + timedelta(days=7)
    due_bills = (
        db.session.query(Bill)
        .filter(
            Bill.user_id == uid,
            Bill.active.is_(True),
            Bill.next_due_date >= w_start,
            Bill.next_due_date <= upcoming_horizon,
        )
        .order_by(Bill.next_due_date.asc())
        .all()
    )
    upcoming_bills = [
        {
            "id": b.id,
            "name": b.name,
            "amount": float(b.amount),
            "currency": b.currency,
            "next_due_date": b.next_due_date.isoformat(),
            "cadence": b.cadence.value,
            "autopay_enabled": b.autopay_enabled,
            "days_until_due": (b.next_due_date - w_end).days,
        }
        for b in due_bills
    ]
    upcoming_bills_total = round(sum(b["amount"] for b in upcoming_bills), 2)

    # Generate highlights and recommendations
    highlights: list[str] = []
    recommendations: list[str] = []

    if direction == "UP":
        highlights.append(
            f"Weekly spending increased by {wow_change_pct}% compared to last week "
            f"({cur_expenses:.2f} vs {prev_expenses:.2f} {currency})."
        )
        recommendations.append(
            "Review discretionary purchases from this week to bring "
            "spending back toward your baseline."
        )
    elif direction == "DOWN":
        highlights.append(
            f"Weekly spending decreased by {abs(wow_change_pct)}% "
            f"compared to last week "
            f"({cur_expenses:.2f} vs {prev_expenses:.2f} {currency})."
        )
        recommendations.append(
            "Consider transferring this week's savings surplus into "
            "your emergency or goal fund."
        )
    else:
        highlights.append(
            f"Weekly spending remained stable at {cur_expenses:.2f} {currency} "
            f"({wow_change_pct}% WoW)."
        )

    if top_categories:
        top_cat = top_categories[0]
        highlights.append(
            f"Top spending category: {top_cat['category_name']} "
            f"at {top_cat['amount']:.2f} "
            f"{currency} ({top_cat['share_pct']}% of weekly spend)."
        )

    if spikes:
        highlights.append(
            f"Detected {len(spikes)} spending spike(s) requiring attention."
        )
        recommendations.append(
            f"Set a category alert for {spikes[0]['category_name']} "
            f"after a {spikes[0]['increase_pct']}% spike."
        )

    if upcoming_bills:
        highlights.append(
            f"{len(upcoming_bills)} bill(s) totaling "
            f"{upcoming_bills_total:.2f} {currency} due soon."
        )
        recommendations.append(
            f"Keep at least {upcoming_bills_total:.2f} {currency} liquid "
            "for upcoming bill obligations."
        )

    if not recommendations:
        recommendations.append(
            "Maintain consistent expense logging to sharpen weekly "
            "trend and anomaly detection."
        )

    return {
        "period": {
            "week_start": w_start.isoformat(),
            "week_end": w_end.isoformat(),
            "prev_week_start": prev_start.isoformat(),
            "prev_week_end": prev_end.isoformat(),
            "days": window_days,
        },
        "currency": currency,
        "summary": {
            "total_income": cur_income,
            "total_expenses": cur_expenses,
            "net_flow": cur_net,
            "transaction_count": len(current_txs),
            "expense_transaction_count": len(cur_expense_txs),
            "daily_average_spend": daily_average_spend,
            "savings_rate_pct": savings_rate_pct,
            "upcoming_bills_total": upcoming_bills_total,
            "upcoming_bills_count": len(upcoming_bills),
        },
        "trends": {
            "direction": direction,
            "trend_direction": direction,
            "week_over_week_change_pct": wow_change_pct,
            "wow_change_pct": wow_change_pct,
            "week_over_week_delta": wow_delta,
            "current_week_expenses": cur_expenses,
            "previous_week_expenses": prev_expenses,
            "current_week_income": cur_income,
            "previous_week_income": prev_income,
            "previous_week_net_flow": prev_net,
            "rolling_4_week_avg": rolling_4_week_avg,
            "vs_4_week_avg_pct": vs_4_week_avg_pct,
            "category_trends": category_trends,
            "daily_breakdown": daily_breakdown,
        },
        "spikes": spikes,
        "anomalies": spikes,
        "top_categories": top_categories,
        "upcoming_bills": upcoming_bills,
        "highlights": highlights,
        "recommendations": recommendations,
        "tips": recommendations,
    }


def get_weekly_digest(
    uid: int,
    *,
    week_start: date | None = None,
    week_end: date | None = None,
    reference_date: date | None = None,
) -> dict:
    w_start, w_end = resolve_week_window(
        week_start=week_start,
        week_end=week_end,
        reference_date=reference_date,
    )
    key = weekly_digest_cache_key(uid, w_start, w_end)
    cached = cache_get(key)
    if cached:
        return cached
    digest = calculate_weekly_digest(
        uid,
        week_start=w_start,
        week_end=w_end,
    )
    cache_set(key, digest, ttl_seconds=300)
    return digest


def generate_and_store_weekly_digest(
    uid: int,
    *,
    week_start: date | None = None,
    week_end: date | None = None,
    reference_date: date | None = None,
    notify: bool = False,
    channel: str = "email",
) -> dict:
    w_start, w_end = resolve_week_window(
        week_start=week_start,
        week_end=week_end,
        reference_date=reference_date,
    )
    digest = calculate_weekly_digest(
        uid,
        week_start=w_start,
        week_end=w_end,
    )
    generated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    digest["generated_at"] = generated_at.isoformat()

    existing = (
        db.session.query(WeeklyDigest)
        .filter_by(user_id=uid, week_start=w_start, week_end=w_end)
        .first()
    )
    if existing:
        existing.currency = digest["currency"]
        existing.total_income = digest["summary"]["total_income"]
        existing.total_expenses = digest["summary"]["total_expenses"]
        existing.net_flow = digest["summary"]["net_flow"]
        existing.wow_change_pct = digest["trends"]["wow_change_pct"]
        existing.trend_direction = digest["trends"]["direction"]
        existing.spikes_count = len(digest["spikes"])
        existing.payload_json = json.dumps(digest)
        existing.created_at = generated_at
        record = existing
    else:
        record = WeeklyDigest(
            user_id=uid,
            week_start=w_start,
            week_end=w_end,
            currency=digest["currency"],
            total_income=digest["summary"]["total_income"],
            total_expenses=digest["summary"]["total_expenses"],
            net_flow=digest["summary"]["net_flow"],
            wow_change_pct=digest["trends"]["wow_change_pct"],
            trend_direction=digest["trends"]["direction"],
            spikes_count=len(digest["spikes"]),
            payload_json=json.dumps(digest),
            created_at=generated_at,
        )
        db.session.add(record)

    db.session.flush()
    digest["digest_id"] = record.id
    digest["persisted"] = True
    record.payload_json = json.dumps(digest)

    if notify:
        headline = (
            digest["highlights"][0]
            if digest["highlights"]
            else f"Weekly digest ({w_start.isoformat()} to {w_end.isoformat()})"
        )
        rem = Reminder(
            user_id=uid,
            message=f"Weekly Financial Digest: {headline}"[:500],
            send_at=generated_at,
            sent=False,
            channel=channel if channel in {"email", "whatsapp"} else "email",
        )
        db.session.add(rem)
        db.session.flush()
        digest["reminder_id"] = rem.id

    db.session.commit()

    cache_delete_patterns([f"digests:{uid}:*"])
    cache_set(weekly_digest_cache_key(uid, w_start, w_end), digest, ttl_seconds=300)
    logger.info(
        "Generated weekly digest id=%s user=%s week=%s..%s spikes=%s",
        record.id,
        uid,
        w_start.isoformat(),
        w_end.isoformat(),
        len(digest["spikes"]),
    )
    return digest
