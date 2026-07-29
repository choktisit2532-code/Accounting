import os
import tempfile


TEST_DB = tempfile.NamedTemporaryFile(prefix="pf-test-", suffix=".db", delete=False).name
os.environ["APP_ENV"] = "test"
os.environ["PERSONAL_FINANCE_DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-longer-than-thirty-two-characters"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["ALLOWED_ORIGINS"] = "http://testserver"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_finance.db import Base, engine  # noqa: E402
from personal_finance.main import app  # noqa: E402
from personal_finance.security import rate_limiter  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    rate_limiter._events.clear()
    with TestClient(app) as test_client:
        yield test_client


def register_and_login(client: TestClient, email: str = "user@example.com") -> None:
    response = client.post("/api/auth/register", json={
        "email": email,
        "full_name": "ผู้ใช้ทดสอบ",
        "password": "StrongPass123!",
    })
    assert response.status_code == 201
    response = client.post("/api/auth/login", json={"email": email, "password": "StrongPass123!"})
    assert response.status_code == 200
    assert "pf_session" in response.cookies
