from personal_finance.tests.conftest import register_and_login


def test_registration_requires_strong_minimum_length(client):
    response = client.post("/api/auth/register", json={
        "email": "short@example.com", "full_name": "ผู้ใช้", "password": "1234567"
    })
    assert response.status_code == 422


def test_http_only_cookie_auth_and_logout(client):
    register_and_login(client)
    assert client.get("/api/auth/me").status_code == 200
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_email_is_rejected(client):
    register_and_login(client)
    response = client.post("/api/auth/register", json={
        "email": "USER@example.com", "full_name": "คนอื่น", "password": "AnotherPass123!"
    })
    assert response.status_code == 409
