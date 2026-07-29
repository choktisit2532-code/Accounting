from datetime import date, timedelta

from personal_finance.tests.conftest import register_and_login
from personal_finance.tests.test_ledger import create_account, expense_category


def test_category_crud_and_budget_dependency(client):
    register_and_login(client)
    created = client.post("/api/categories", json={
        "name": "สัตว์เลี้ยง", "type": "expense", "icon": "fa-paw", "color": "#22C55E"
    })
    assert created.status_code == 201
    category_id = created.json()["id"]
    updated = client.put(f"/api/categories/{category_id}", json={
        "name": "ค่าใช้จ่ายสัตว์เลี้ยง", "type": "expense", "icon": "fa-paw", "color": "#22C55E"
    })
    assert updated.status_code == 200
    assert client.post("/api/budgets", json={
        "category_id": category_id, "limit_amount": "500", "month": 7, "year": 2026
    }).status_code == 200
    assert client.delete(f"/api/categories/{category_id}").status_code == 409


def test_savings_goal_full_lifecycle(client):
    register_and_login(client)
    target_date = (date.today() + timedelta(days=30)).isoformat()
    created = client.post("/api/savings", json={
        "name": "กองทุนฉุกเฉิน", "target_amount": "10000", "current_amount": "1000",
        "target_date": target_date,
    })
    assert created.status_code == 201
    goal_id = created.json()["id"]
    contributed = client.post(f"/api/savings/{goal_id}/contribute", json={"amount": "500"})
    assert contributed.json()["current_amount"] == "1500.00"
    assert client.put(f"/api/savings/{goal_id}", json={"name": "เงินฉุกเฉิน"}).status_code == 200
    assert client.delete(f"/api/savings/{goal_id}").status_code == 200


def test_empty_account_can_be_renamed_and_deleted(client):
    register_and_login(client)
    account = create_account(client, balance="0")
    renamed = client.put(f"/api/accounts/{account['id']}", json={"name": "เงินสำรอง"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "เงินสำรอง"
    assert client.delete(f"/api/accounts/{account['id']}").status_code == 200


def test_account_with_history_cannot_be_deleted(client):
    register_and_login(client)
    account = create_account(client)
    assert client.delete(f"/api/accounts/{account['id']}").status_code == 409


def test_user_cannot_read_or_delete_another_users_transaction(client):
    register_and_login(client, "owner@example.com")
    account = create_account(client)
    response = client.post("/api/transactions", data={
        "type": "expense", "amount": "20", "account_id": account["id"],
        "category_id": expense_category(client), "date_val": "2026-07-16",
    })
    tx_id = response.json()["id"]
    client.post("/api/auth/logout")
    register_and_login(client, "other@example.com")
    assert client.get("/api/transactions").json() == []
    assert client.delete(f"/api/transactions/{tx_id}").status_code == 404


def test_csv_export_contains_utf8_header_and_data(client):
    register_and_login(client)
    account = create_account(client)
    client.post("/api/transactions", data={
        "type": "expense", "amount": "20", "account_id": account["id"],
        "category_id": expense_category(client), "date_val": "2026-07-16", "note": "อาหารกลางวัน",
    })
    response = client.get("/api/reports/transactions.csv")
    assert response.status_code == 200
    assert "อาหารกลางวัน" in response.text
    assert response.headers["content-type"].startswith("text/csv")


def test_pdf_export_contains_summary_and_is_downloadable(client):
    register_and_login(client)
    account = create_account(client)
    client.post("/api/transactions", data={
        "type": "expense", "amount": "20", "account_id": account["id"],
        "category_id": expense_category(client), "date_val": "2026-07-16", "note": "อาหารกลางวัน",
    })

    response = client.get("/api/reports/transactions.pdf?start_date=2026-07-01&end_date=2026-07-31")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 5000


def test_transaction_type_filter_never_mixes_income_and_expense(client):
    register_and_login(client)
    account = create_account(client, balance="0")
    category_id = expense_category(client)
    assert client.post("/api/transactions", data={
        "type": "expense", "amount": "85", "account_id": account["id"],
        "category_id": category_id, "date_val": "2026-07-16",
    }).status_code == 201
    income_category = next(
        item for item in client.get("/api/categories").json() if item["type"] == "income"
    )
    assert client.post("/api/transactions", data={
        "type": "income", "amount": "10581.90", "account_id": account["id"],
        "category_id": income_category["id"], "date_val": "2026-07-17",
    }).status_code == 201

    filtered_response = client.get("/api/transactions?type=expense")
    rows = filtered_response.json()

    assert filtered_response.headers["x-applied-transaction-type"] == "expense"
    assert len(rows) == 1
    assert rows[0]["type"] == "expense"
    assert rows[0]["amount"] == "85.00"

    summary = client.get("/api/transactions/summary?type=expense").json()
    assert summary == {
        "count": 1,
        "income": 0.0,
        "expense": 85.0,
        "net": -85.0,
        "transfer": 0.0,
    }


def test_invalid_receipt_is_rejected_without_creating_transaction(client):
    register_and_login(client)
    account = create_account(client)
    before = len(client.get("/api/transactions").json())
    response = client.post(
        "/api/transactions",
        data={"type": "expense", "amount": "10", "account_id": account["id"]},
        files={"receipt": ("fake.png", b"not-an-image", "image/png")},
    )
    assert response.status_code == 422
    assert len(client.get("/api/transactions").json()) == before
