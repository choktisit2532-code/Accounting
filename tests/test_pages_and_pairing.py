from personal_finance.db import SessionLocal
from personal_finance.models import User
from personal_finance.tests.conftest import register_and_login
from personal_finance.tests.test_ledger import expense_category


def test_health_and_static_pages(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["build"] == "20260720.3"
    for path in ["/", "/login", "/register", "/dashboard"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache, no-store"
    static_response = client.get("/static/app.js?v=20260720.3")
    assert static_response.status_code == 200
    assert "no-store" in static_response.headers["cache-control"]
    dashboard = client.get("/dashboard").text
    assert "ระบบรุ่น 20260720.3" in dashboard
    assert "/static/app.js?v=20260720.3" in dashboard
    assert 'data-dashboard-type="expense"' in dashboard
    assert 'id="period-prev"' in dashboard
    assert 'id="category-breakdown-list"' in dashboard


def test_pairing_code_reuse_status_and_unlink(client):
    register_and_login(client)
    first = client.post("/api/reports/pairing-code")
    second = client.post("/api/reports/pairing-code")
    assert first.status_code == 200
    assert first.json()["code"] == second.json()["code"]
    assert client.get("/api/reports/line-status").json() == {"paired": False}
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "user@example.com").one()
        user.line_user_id = "U-status"
        db.commit()
    assert client.get("/api/reports/line-status").json() == {"paired": True}
    assert client.delete("/api/reports/line-pairing").status_code == 200
    assert client.get("/api/reports/line-status").json() == {"paired": False}


def test_budget_delete_and_invalid_period(client):
    register_and_login(client)
    category_id = expense_category(client)
    created = client.post("/api/budgets", json={
        "category_id": category_id, "limit_amount": "1000", "month": 7, "year": 2026
    })
    assert client.delete(f"/api/budgets/{created.json()['id']}").status_code == 200
    assert client.get("/api/budgets?month=13&year=2026").status_code == 422
    assert client.get("/api/reports/dashboard?month=13&year=2026").status_code == 422
