from dataclasses import replace

from personal_finance.config import settings
from personal_finance.db import SessionLocal
from personal_finance.models import User
from personal_finance.tests.conftest import register_and_login


class LineResponse:
    status_code = 200

    def __init__(self, user_id):
        self.user_id = user_id

    def json(self):
        return {"userId": self.user_id}


def fake_line_client(user_id):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return LineResponse(user_id)

    return Client


def test_line_public_config(client, monkeypatch):
    monkeypatch.setattr("personal_finance.main.settings", replace(settings, line_liff_id="123-test"))
    assert client.get("/api/public/line-config").json() == {"liff_id": "123-test"}


def test_liff_session_requires_paired_account(client, monkeypatch):
    monkeypatch.setattr("personal_finance.routers.auth.settings", replace(settings, line_liff_id="123-test"))

    monkeypatch.setattr("personal_finance.routers.auth.httpx.AsyncClient", fake_line_client("U-not-paired"))
    response = client.post("/api/auth/liff-session", json={"access_token": "x" * 30})
    assert response.status_code == 403


def test_liff_session_sets_accounting_cookie(client, monkeypatch):
    monkeypatch.setattr("personal_finance.routers.auth.settings", replace(settings, line_liff_id="123-test"))
    register_and_login(client)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "user@example.com").one()
        user.line_user_id = "U-liff"
        db.commit()
    client.post("/api/auth/logout")
    monkeypatch.setattr("personal_finance.routers.auth.httpx.AsyncClient", fake_line_client("U-liff"))
    response = client.post("/api/auth/liff-session", json={"access_token": "x" * 30})
    assert response.status_code == 200
    assert response.cookies.get(settings.cookie_name)
