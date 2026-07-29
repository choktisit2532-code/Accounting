from decimal import Decimal

from personal_finance.db import SessionLocal
from personal_finance.models import Account, FleetExpense, Transaction
from personal_finance.routers.line_webhook import fleet_flex_message

from conftest import register_and_login


def create_account(client, balance="10000"):
    response = client.post("/api/accounts", json={"name": "บัญชีรถ", "type": "bank", "balance": balance})
    assert response.status_code == 201
    return response.json()


def create_vehicle(client, account_id):
    response = client.post("/api/fleet/vehicles", json={
        "name": "รถส่งของ", "plate_number": "กข 1234", "vehicle_type": "car",
        "default_account_id": account_id,
    })
    assert response.status_code == 201
    return response.json()


def test_fleet_expense_updates_accounting_and_delete_reverts_balance(client):
    register_and_login(client)
    account = create_account(client)
    vehicle = create_vehicle(client, account["id"])
    response = client.post("/api/fleet/expenses", json={
        "vehicle_id": vehicle["id"], "category": "ค่าน้ำมัน", "amount": "1250.50",
        "expense_date": "2026-07-20",
    })
    assert response.status_code == 201
    expense = response.json()

    with SessionLocal() as db:
        fleet = db.get(FleetExpense, expense["id"])
        tx = db.get(Transaction, fleet.transaction_id)
        wallet = db.get(Account, account["id"])
        assert tx.source == "fleet"
        assert tx.external_id == f"fleet-expense:{fleet.id}"
        assert wallet.balance == Decimal("8749.50")

    blocked = client.delete(f"/api/transactions/{expense['transaction_id']}")
    assert blocked.status_code == 409
    deleted = client.delete(f"/api/fleet/expenses/{expense['id']}")
    assert deleted.status_code == 200
    with SessionLocal() as db:
        assert db.get(Account, account["id"]).balance == Decimal("10000.00")


def test_fleet_data_is_isolated_per_accounting_user(client):
    register_and_login(client, "owner@example.com")
    account = create_account(client)
    vehicle = create_vehicle(client, account["id"])
    client.post("/api/auth/logout")
    register_and_login(client, "other@example.com")
    assert client.get("/api/fleet/vehicles").json() == []
    assert client.post("/api/fleet/mileages", json={
        "vehicle_id": vehicle["id"], "mileage": 1000
    }).status_code == 404


def test_line_fleet_flex_uses_same_data(client):
    register_and_login(client)
    account = create_account(client)
    create_vehicle(client, account["id"])
    with SessionLocal() as db:
        user_id = db.query(Account.user_id).filter(Account.id == account["id"]).scalar()
        message = fleet_flex_message(db, user_id)
    assert message["type"] == "flex"
    assert "รถของฉัน" in message["contents"]["header"]["contents"][1]["text"]
    assert message["contents"]["footer"]["contents"][0]["action"]["uri"].endswith("/fleet")


def test_fleet_documents_are_listed_deleted_and_isolated(client):
    register_and_login(client)
    account = create_account(client)
    vehicle = create_vehicle(client, account["id"])
    created = client.post("/api/fleet/documents", json={
        "vehicle_id": vehicle["id"], "document_type": "พ.ร.บ.",
        "expiry_date": "2026-08-15",
    })
    assert created.status_code == 201
    document = created.json()
    assert client.get("/api/fleet/documents").json()[0]["id"] == document["id"]
    client.post("/api/auth/logout")
    register_and_login(client, "other-docs@example.com")
    assert client.get("/api/fleet/documents").json() == []
    assert client.delete(f"/api/fleet/documents/{document['id']}").status_code == 404


def test_fleet_expense_update_rejects_future_date(client):
    register_and_login(client)
    account = create_account(client)
    vehicle = create_vehicle(client, account["id"])
    created = client.post("/api/fleet/expenses", json={
        "vehicle_id": vehicle["id"], "category": "ค่าน้ำมัน", "amount": "100",
        "expense_date": "2026-07-20",
    }).json()
    response = client.put(f"/api/fleet/expenses/{created['id']}", json={
        "vehicle_id": vehicle["id"], "category": "ค่าน้ำมัน", "amount": "200",
        "expense_date": "2099-01-01",
    })
    assert response.status_code == 422
