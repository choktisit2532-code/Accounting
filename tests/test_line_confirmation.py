from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from personal_finance.db import SessionLocal
from personal_finance.models import PendingLineTransaction, User, utcnow
from personal_finance.routers.line_webhook import confirm_pending, pending_message
from personal_finance.tests.conftest import register_and_login
from personal_finance.tests.test_ledger import create_account, expense_category


def test_line_confirmation_changes_balance_only_once(client):
    register_and_login(client)
    account = create_account(client)
    category_id = expense_category(client)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "user@example.com").one()
        category_name = next(
            item["name"] for item in client.get("/api/categories").json() if item["id"] == category_id
        )
        pending = PendingLineTransaction(
            event_key="line-event-1",
            user_id=user.id,
            line_user_id="U-test",
            payload={
                "type": "expense",
                "amount": "50.00",
                "category": category_name,
                "account_name": account["name"],
                "to_account_name": None,
                "transaction_date": "2026-07-16",
                "note": "รายการจาก LINE",
            },
            status="pending",
            expires_at=utcnow() + timedelta(minutes=15),
        )
        db.add(pending)
        db.commit()
        db.refresh(pending)
        confirm_pending(db, pending)
        first_balance = client.get("/api/accounts").json()[0]["balance"]
        confirm_pending(db, pending)
        second_balance = client.get("/api/accounts").json()[0]["balance"]
    assert first_balance == "950.00"
    assert second_balance == "950.00"



def test_line_transfer_requires_named_destination(client):
    register_and_login(client)
    account = create_account(client)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "user@example.com").one()
        pending = PendingLineTransaction(
            event_key="line-transfer-missing-destination",
            user_id=user.id,
            line_user_id="U-test",
            payload={
                "type": "transfer",
                "amount": "100.00",
                "category": None,
                "account_name": account["name"],
                "to_account_name": None,
                "transaction_date": "2026-07-16",
                "note": "โอนเงิน",
            },
            status="pending",
            expires_at=utcnow() + timedelta(minutes=15),
        )
        db.add(pending)
        db.commit()
        db.refresh(pending)
        with pytest.raises(HTTPException) as exc:
            confirm_pending(db, pending)
    assert exc.value.status_code == 422
    assert "ต้องระบุบัญชีปลายทาง" in exc.value.detail
    assert client.get("/api/accounts").json()[0]["balance"] == "1000.00"


def test_pending_buttons_template_uses_160_character_variant():
    pending = SimpleNamespace(
        id=7,
        payload={
            "type": "transfer",
            "amount": "100.00",
            "category": None,
            "account_name": "Krungthai XXX-X-XX371-4",
            "to_account_name": "เงินสด",
            "transaction_date": "2026-07-16",
        },
    )

    template = pending_message(pending)["template"]

    assert template["type"] == "buttons"
    assert "title" not in template
    assert len(template["text"]) <= 160
