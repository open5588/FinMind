from datetime import date, timedelta


def test_weekly_digest_calculates_trends_and_wow_change(client, auth_header):
    week_end = date.today()
    week_start = week_end - timedelta(days=6)
    prev_week_date = week_start - timedelta(days=3)

    r = client.post("/categories", json={"name": "Groceries"}, headers=auth_header)
    assert r.status_code == 201
    groceries_id = r.get_json()["id"]

    # Previous week expense: 200
    r = client.post(
        "/expenses",
        json={
            "amount": 200,
            "description": "Last week groceries",
            "date": prev_week_date.isoformat(),
            "category_id": groceries_id,
            "expense_type": "EXPENSE",
        },
        headers=auth_header,
    )
    assert r.status_code == 201

    # Current week income: 2000
    r = client.post(
        "/expenses",
        json={
            "amount": 2000,
            "description": "Weekly freelance payout",
            "date": week_start.isoformat(),
            "expense_type": "INCOME",
        },
        headers=auth_header,
    )
    assert r.status_code == 201

    # Current week expenses: 350 total (+75% WoW)
    r = client.post(
        "/expenses",
        json={
            "amount": 250,
            "description": "Supermarket haul",
            "date": (week_start + timedelta(days=1)).isoformat(),
            "category_id": groceries_id,
            "expense_type": "EXPENSE",
        },
        headers=auth_header,
    )
    assert r.status_code == 201

    r = client.post(
        "/expenses",
        json={
            "amount": 100,
            "description": "Produce market",
            "date": week_end.isoformat(),
            "category_id": groceries_id,
            "expense_type": "EXPENSE",
        },
        headers=auth_header,
    )
    assert r.status_code == 201

    # Upcoming bill due in 3 days
    r = client.post(
        "/bills",
        json={
            "name": "Broadband",
            "amount": 60,
            "next_due_date": (week_end + timedelta(days=3)).isoformat(),
            "cadence": "MONTHLY",
        },
        headers=auth_header,
    )
    assert r.status_code == 201

    r = client.get(
        f"/api/digests/weekly?week_start={week_start.isoformat()}"
        f"&week_end={week_end.isoformat()}",
        headers=auth_header,
    )
    assert r.status_code == 200
    digest = r.get_json()

    assert digest["period"]["week_start"] == week_start.isoformat()
    assert digest["period"]["week_end"] == week_end.isoformat()
    assert digest["summary"]["total_income"] == 2000.0
    assert digest["summary"]["total_expenses"] == 350.0
    assert digest["summary"]["net_flow"] == 1650.0
    assert digest["summary"]["upcoming_bills_count"] == 1
    assert digest["summary"]["upcoming_bills_total"] == 60.0

    assert digest["trends"]["previous_week_expenses"] == 200.0
    assert digest["trends"]["week_over_week_change_pct"] == 75.0
    assert digest["trends"]["direction"] == "UP"
    assert len(digest["trends"]["daily_breakdown"]) == 7

    assert len(digest["top_categories"]) == 1
    assert digest["top_categories"][0]["category_name"] == "Groceries"
    assert digest["top_categories"][0]["amount"] == 350.0
    assert len(digest["highlights"]) >= 2
    assert len(digest["recommendations"]) >= 1


def test_weekly_digest_detects_category_and_transaction_spikes(client, auth_header):
    week_end = date.today()
    week_start = week_end - timedelta(days=6)
    prev_date = week_start - timedelta(days=2)

    r = client.post("/categories", json={"name": "Dining Out"}, headers=auth_header)
    assert r.status_code == 201
    dining_id = r.get_json()["id"]

    # Previous week Dining Out: 40
    client.post(
        "/expenses",
        json={
            "amount": 40,
            "description": "Casual lunch",
            "date": prev_date.isoformat(),
            "category_id": dining_id,
        },
        headers=auth_header,
    )

    # Current week: several normal expenses + 1 large spike transaction in Dining Out
    for idx, amt in enumerate([20, 22, 18, 25]):
        client.post(
            "/expenses",
            json={
                "amount": amt,
                "description": f"Coffee {idx}",
                "date": (week_start + timedelta(days=idx)).isoformat(),
                "category_id": dining_id,
            },
            headers=auth_header,
        )

    client.post(
        "/expenses",
        json={
            "amount": 320,
            "description": "Anniversary fine dining",
            "date": week_end.isoformat(),
            "category_id": dining_id,
        },
        headers=auth_header,
    )

    r = client.get(
        f"/api/digests/weekly?week_start={week_start.isoformat()}"
        f"&week_end={week_end.isoformat()}",
        headers=auth_header,
    )
    assert r.status_code == 200
    digest = r.get_json()

    spike_types = {s["type"] for s in digest["spikes"]}
    assert "CATEGORY_SPIKE" in spike_types
    assert "TRANSACTION_SPIKE" in spike_types

    cat_spike = next(s for s in digest["spikes"] if s["type"] == "CATEGORY_SPIKE")
    assert cat_spike["category_name"] == "Dining Out"
    assert cat_spike["severity"] == "HIGH"
    assert cat_spike["increase_pct"] > 100.0

    tx_spike = next(s for s in digest["spikes"] if s["type"] == "TRANSACTION_SPIKE")
    assert tx_spike["description"] == "Anniversary fine dining"
    assert tx_spike["amount"] == 320.0


def test_generate_weekly_digest_persists_and_creates_reminder(client, auth_header):
    week_end = date.today()
    week_start = week_end - timedelta(days=6)

    client.post(
        "/expenses",
        json={
            "amount": 150,
            "description": "Utility payment",
            "date": week_end.isoformat(),
        },
        headers=auth_header,
    )

    r = client.post(
        "/api/digests/generate",
        json={
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "notify": True,
            "channel": "email",
        },
        headers=auth_header,
    )
    assert r.status_code == 201
    generated = r.get_json()
    assert generated["persisted"] is True
    assert isinstance(generated["digest_id"], int)
    assert isinstance(generated["reminder_id"], int)

    hist = client.get("/api/digests/history", headers=auth_header)
    assert hist.status_code == 200
    items = hist.get_json()
    assert len(items) == 1
    assert items[0]["id"] == generated["digest_id"]
    assert items[0]["total_expenses"] == 150.0


def test_weekly_digest_invalid_date_params_return_400(client, auth_header):
    r = client.get("/api/digests/weekly?week_start=not-a-date", headers=auth_header)
    assert r.status_code == 400

    r = client.get(
        "/api/digests/weekly?week_start=2026-05-10&week_end=2026-05-01",
        headers=auth_header,
    )
    assert r.status_code == 400
