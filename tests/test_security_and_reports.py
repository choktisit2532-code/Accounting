import base64
import hashlib
import hmac
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from personal_finance.routers import line_webhook
from personal_finance.tests.conftest import register_and_login
from personal_finance.tests.test_ledger import create_account, expense_category


def test_receipt_requires_owner_session(client):
    register_and_login(client)
    account = create_account(client)
    image = BytesIO()
    Image.new("RGB", (2400, 1200), color="white").save(image, format="PNG")
    response = client.post(
        "/api/transactions",
        data={"type": "expense", "amount": "10", "account_id": account["id"], "category_id": expense_category(client)},
        files={"receipt": ("receipt.png", image.getvalue(), "image/png")},
    )
    transaction = response.json()
    tx_id = transaction["id"]
    assert transaction["receipt_path"].startswith("users/")
    assert transaction["receipt_path"].endswith(".webp")
    receipt_response = client.get(f"/api/transactions/{tx_id}/receipt")
    assert receipt_response.status_code == 200
    assert receipt_response.headers["content-type"] == "image/webp"
    with Image.open(BytesIO(receipt_response.content)) as stored:
        assert stored.format == "WEBP"
        assert max(stored.size) <= 1600
    client.post("/api/auth/logout")
    assert client.get(f"/api/transactions/{tx_id}/receipt").status_code == 401


def test_invalid_line_signature_is_rejected(client, monkeypatch):
    monkeypatch.setattr(line_webhook, "settings", SimpleNamespace(line_channel_secret="secret"))
    response = client.post("/api/line/webhook", content=b'{"events":[]}', headers={"x-line-signature": "invalid"})
    assert response.status_code == 401


def test_valid_empty_line_webhook(client, monkeypatch):
    secret = "secret"
    monkeypatch.setattr(line_webhook, "settings", SimpleNamespace(line_channel_secret=secret))
    body = b'{"events":[]}'
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    response = client.post("/api/line/webhook", content=body, headers={"x-line-signature": signature})
    assert response.status_code == 200


def test_budget_api_returns_only_requested_period(client):
    register_and_login(client)
    category_id = expense_category(client)
    assert client.post("/api/budgets", json={
        "category_id": category_id, "limit_amount": "1000", "month": 7, "year": 2026
    }).status_code == 200
    assert len(client.get("/api/budgets?month=7&year=2026").json()) == 1
    assert client.get("/api/budgets?month=8&year=2026").json() == []


def test_dashboard_trend_contains_six_distinct_months(client):
    register_and_login(client)
    data = client.get("/api/reports/dashboard?month=7&year=2026").json()
    labels = [item["month"] for item in data["monthly_trend"]]
    assert len(labels) == 6
    assert len(set(labels)) == 6


def test_dashboard_yearly_cashflow_contains_twelve_months_and_correct_totals(client):
    register_and_login(client)
    account = create_account(client, balance="5000")
    category_id = expense_category(client)
    for amount, tx_date in [("100", "2026-01-05"), ("250", "2026-07-16"), ("999", "2025-07-16")]:
        response = client.post("/api/transactions", data={
            "type": "expense", "amount": amount, "account_id": account["id"],
            "category_id": category_id, "date_val": tx_date,
        })
        assert response.status_code == 201

    data = client.get("/api/reports/dashboard?month=7&year=2026").json()

    assert len(data["yearly_cashflow"]) == 12
    assert [item["month"] for item in data["yearly_cashflow"]] == list(range(1, 13))
    assert data["yearly_cashflow"][0]["expense"] == 100.0
    assert data["yearly_cashflow"][6]["expense"] == 250.0
    assert sum(item["expense"] for item in data["yearly_cashflow"]) == 350.0


def test_dashboard_recent_transactions_match_selected_month(client):
    register_and_login(client)
    account = create_account(client, balance="3000")
    category_id = expense_category(client)
    for amount, tx_date in [("85", "2026-07-16"), ("51", "2026-07-15"), ("2740", "2024-07-15")]:
        response = client.post("/api/transactions", data={
            "type": "expense", "amount": amount, "account_id": account["id"],
            "category_id": category_id, "date_val": tx_date,
        })
        assert response.status_code == 201

    data = client.get("/api/reports/dashboard?month=7&year=2026").json()

    assert data["month_expense"] == 136.0
    assert [item["amount"] for item in data["recent_transactions"]] == [85.0, 51.0]
    assert [item["amount"] for item in data["monthly_transactions"]] == [85.0, 51.0]
    assert sum(item["amount"] for item in data["category_breakdowns"]["expense"]) == 136.0
    assert data["category_breakdowns"]["income"] == []
    assert data["month_transfer"] == 0.0
    assert all(item["date"].startswith("2026-07") for item in data["recent_transactions"])
    assert len(data["daily_cashflow"]) == 31
    assert data["daily_cashflow"][14] == {
        "day": 15, "date": "2026-07-15", "income": 0.0, "expense": 51.0
    }
    assert data["daily_cashflow"][15] == {
        "day": 16, "date": "2026-07-16", "income": 0.0, "expense": 85.0
    }
    assert sum(item["expense"] for item in data["daily_cashflow"]) == data["month_expense"]
