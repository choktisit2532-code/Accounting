from personal_finance.tests.conftest import register_and_login


def create_account(client, name="เงินสด", balance="1000.00"):
    response = client.post("/api/accounts", json={"name": name, "type": "cash", "balance": balance})
    assert response.status_code == 201
    return response.json()


def expense_category(client):
    categories = client.get("/api/categories").json()
    return next(item for item in categories if item["type"] == "expense")["id"]


def test_starting_balance_has_a_ledger_entry(client):
    register_and_login(client)
    account = create_account(client)
    history = client.get("/api/transactions").json()
    assert account["balance"] == "1000.00"
    assert len(history) == 1
    assert history[0]["source"] == "system"


def test_create_and_delete_expense_restores_balance(client):
    register_and_login(client)
    account = create_account(client)
    response = client.post("/api/transactions", data={
        "type": "expense", "amount": "125.50", "account_id": account["id"],
        "category_id": expense_category(client), "date_val": "2026-07-16",
    })
    assert response.status_code == 201
    assert client.get("/api/accounts").json()[0]["balance"] == "874.50"
    assert client.delete(f"/api/transactions/{response.json()['id']}").status_code == 200
    assert client.get("/api/accounts").json()[0]["balance"] == "1000.00"


def test_negative_amount_and_same_account_transfer_are_rejected(client):
    register_and_login(client)
    account = create_account(client)
    negative = client.post("/api/transactions", data={
        "type": "expense", "amount": "-1", "account_id": account["id"], "date_val": "2026-07-16"
    })
    assert negative.status_code == 422
    transfer = client.post("/api/transactions", data={
        "type": "transfer", "amount": "10", "account_id": account["id"],
        "to_account_id": account["id"], "date_val": "2026-07-16",
    })
    assert transfer.status_code == 422


def test_reconciliation_creates_auditable_adjustment(client):
    register_and_login(client)
    account = create_account(client)
    response = client.post(f"/api/accounts/{account['id']}/reconcile", json={"actual_balance": "900"})
    assert response.status_code == 200
    history = client.get("/api/transactions").json()
    assert len(history) == 2
    assert history[0]["source"] == "system"
    assert history[0]["amount"] == "100.00"


def test_transaction_update_reverts_old_effect_before_applying_new(client):
    register_and_login(client)
    account = create_account(client)
    response = client.post("/api/transactions", data={
        "type": "expense", "amount": "100", "account_id": account["id"],
        "category_id": expense_category(client), "date_val": "2026-07-16",
    })
    tx_id = response.json()["id"]
    response = client.put(f"/api/transactions/{tx_id}", json={"amount": "250"})
    assert response.status_code == 200
    assert client.get("/api/accounts").json()[0]["balance"] == "750.00"
