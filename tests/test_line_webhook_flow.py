import base64
import hashlib
import hmac
import json
import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import httpx

from personal_finance.db import SessionLocal
from personal_finance.models import LinePairCode, PendingLineTransaction, User, utcnow
from personal_finance.routers import line_webhook
from personal_finance.tests.conftest import register_and_login
from personal_finance.tests.test_ledger import create_account, expense_category


def signed_body(secret, event):
    body = json.dumps({"events": [event]}, separators=(",", ":")).encode()
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return body, signature


def paired_user(line_id="U-flow"):
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "user@example.com").one()
        user.line_user_id = line_id
        db.commit()
        return user.id


def line_settings(secret):
    return SimpleNamespace(
        line_channel_secret=secret,
        line_channel_access_token="token",
        gemini_api_key="key",
        gemini_model="model",
        max_upload_bytes=5 * 1024 * 1024,
        upload_dir=line_webhook.settings.upload_dir,
    )


def test_summary_command_parser_supports_thai_dates(monkeypatch):
    monkeypatch.setattr(line_webhook, "bangkok_today", lambda: date(2026, 7, 20))

    assert line_webhook.parse_summary_command("ขอสรุปเดือนนี้") == ("month", 2026, 7)
    assert line_webhook.parse_summary_command("สรุปเดือน 7/2569") == ("month", 2026, 7)
    assert line_webhook.parse_summary_command("สรุปเดือนกรกฎาคม 2568") == ("month", 2025, 7)
    assert line_webhook.parse_summary_command("ขอสรุปปี ๒๕๖๙") == ("year", 2026, None)


def test_line_month_summary_uses_ledger_without_gemini(client, monkeypatch):
    register_and_login(client)
    account = create_account(client)
    categories = client.get("/api/categories").json()
    income_category_id = next(item["id"] for item in categories if item["type"] == "income")
    expense_category_id = next(item["id"] for item in categories if item["type"] == "expense")
    paired_user()
    for tx_type, amount, category_id, note in (
        ("income", "1000", income_category_id, "รายได้เสริม"),
        ("expense", "150", expense_category_id, "อาหาร"),
        ("expense", "50", expense_category_id, "ขนม"),
    ):
        response = client.post("/api/transactions", data={
            "type": tx_type,
            "amount": amount,
            "account_id": account["id"],
            "category_id": category_id,
            "date_val": "2026-07-16",
            "note": note,
        })
        assert response.status_code == 201

    replies = []
    secret = "line-secret"

    async def fail_if_gemini_called(**_kwargs):
        raise AssertionError("summary commands must not call Gemini")

    async def fake_reply(_token, text):
        replies.append(text)

    monkeypatch.setattr(line_webhook, "settings", line_settings(secret))
    monkeypatch.setattr(line_webhook, "analyze_with_gemini", fail_if_gemini_called)
    monkeypatch.setattr(line_webhook, "reply_text", fake_reply)
    event = {
        "type": "message", "webhookEventId": "evt-summary-month", "replyToken": "reply",
        "source": {"userId": "U-flow"},
        "message": {"id": "msg-summary", "type": "text", "text": "ขอสรุปเดือนกรกฎาคม 2569"},
    }
    body, signature = signed_body(secret, event)
    response = client.post("/api/line/webhook", content=body, headers={
        "content-type": "application/json", "x-line-signature": signature,
    })

    assert response.status_code == 200
    assert len(replies) == 1
    assert "รายรับ: ฿1,000.00" in replies[0]
    assert "รายจ่าย: ฿200.00" in replies[0]
    assert "สุทธิ: +฿800.00" in replies[0]
    with SessionLocal() as db:
        assert db.query(PendingLineTransaction).count() == 0


def test_gemini_key_is_sent_in_header_not_url(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"type":"expense","amount":10}'}]}}]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, json, headers):
            captured.update(url=url, json=json, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(line_webhook, "settings", line_settings("line-secret"))
    monkeypatch.setattr(line_webhook.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(line_webhook.analyze_with_gemini(text="ซื้อขนม 10 บาท"))

    assert result["amount"] == 10
    assert "?key=" not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "key"


def test_text_webhook_creates_pending_not_real_transaction(client, monkeypatch):
    register_and_login(client)
    create_account(client)
    user_id = paired_user()
    secret = "line-secret"
    replies = []

    async def fake_analyze(**_kwargs):
        return {
            "type": "expense", "amount": 75, "category": "อาหารและเครื่องดื่ม",
            "account_name": "เงินสด", "to_account_name": None,
            "transaction_date": "2026-07-16", "note": "ค่าอาหาร",
        }

    async def fake_reply(_token, messages):
        replies.extend(messages)

    monkeypatch.setattr(line_webhook, "settings", line_settings(secret))
    monkeypatch.setattr(line_webhook, "analyze_with_gemini", fake_analyze)
    monkeypatch.setattr(line_webhook, "reply_messages", fake_reply)
    event = {
        "type": "message", "webhookEventId": "evt-text-1", "replyToken": "reply",
        "source": {"userId": "U-flow"},
        "message": {"id": "msg-1", "type": "text", "text": "กินข้าว 75 บาท"},
    }
    body, signature = signed_body(secret, event)
    response = client.post("/api/line/webhook", content=body, headers={
        "content-type": "application/json", "x-line-signature": signature,
    })
    assert response.status_code == 200
    with SessionLocal() as db:
        pending = db.query(PendingLineTransaction).filter(PendingLineTransaction.user_id == user_id).one()
        assert pending.status == "pending"
        assert pending.payload["note"] == "กินข้าว 75 บาท"
    assert len(client.get("/api/transactions").json()) == 1  # starting balance only
    assert replies[0] == {
        "type": "text",
        "text": "ข้อความต้นฉบับที่ระบบจะบันทึก:\nกินข้าว 75 บาท",
    }
    assert replies[1]["type"] == "template"


def test_pairing_command_links_line_user(client, monkeypatch):
    register_and_login(client)
    secret = "line-secret"
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "user@example.com").one()
        db.add(LinePairCode(
            user_id=user.id, code="PF-ABCDEFGH", expires_at=utcnow() + timedelta(minutes=10)
        ))
        db.commit()

    async def fake_reply(_token, _text):
        return None

    monkeypatch.setattr(line_webhook, "settings", line_settings(secret))
    monkeypatch.setattr(line_webhook, "reply_text", fake_reply)
    event = {
        "type": "message", "webhookEventId": "evt-pair-1", "replyToken": "reply",
        "source": {"userId": "U-paired"},
        "message": {"id": "msg-pair", "type": "text", "text": "ผูกบัญชี PF-ABCDEFGH"},
    }
    body, signature = signed_body(secret, event)
    assert client.post("/api/line/webhook", content=body, headers={
        "content-type": "application/json", "x-line-signature": signature,
    }).status_code == 200
    with SessionLocal() as db:
        assert db.query(User).filter(User.line_user_id == "U-paired").one()


def test_postback_confirmation_is_idempotent_through_webhook(client, monkeypatch):
    register_and_login(client)
    account = create_account(client)
    category_id = expense_category(client)
    user_id = paired_user()
    secret = "line-secret"
    with SessionLocal() as db:
        category_name = next(
            item["name"] for item in client.get("/api/categories").json() if item["id"] == category_id
        )
        pending = PendingLineTransaction(
            event_key="analysis-event",
            user_id=user_id,
            line_user_id="U-flow",
            payload={
                "type": "expense", "amount": "25.00", "category": category_name,
                "account_name": account["name"], "to_account_name": None,
                "transaction_date": "2026-07-16", "note": "ยืนยันจากปุ่ม",
            },
            status="pending",
            expires_at=utcnow() + timedelta(minutes=10),
        )
        db.add(pending)
        db.commit()
        pending_id = pending.id

    async def fake_reply(_token, _text):
        return None

    monkeypatch.setattr(line_webhook, "settings", line_settings(secret))
    monkeypatch.setattr(line_webhook, "reply_text", fake_reply)
    event = {
        "type": "postback", "webhookEventId": "evt-confirm-1", "replyToken": "reply",
        "source": {"userId": "U-flow"},
        "postback": {"data": f"pf_action=confirm&pending_id={pending_id}"},
    }
    body, signature = signed_body(secret, event)
    assert client.post("/api/line/webhook", content=body, headers={
        "content-type": "application/json", "x-line-signature": signature,
    }).status_code == 200
    assert client.get("/api/accounts").json()[0]["balance"] == "975.00"
    assert client.post("/api/line/webhook", content=body, headers={
        "content-type": "application/json", "x-line-signature": signature,
    }).status_code == 200
    assert client.get("/api/accounts").json()[0]["balance"] == "975.00"



def test_transfer_without_destination_is_rejected_before_pending(client, monkeypatch):
    register_and_login(client)
    create_account(client)
    user_id = paired_user()
    secret = "line-secret"
    replies = []

    async def fake_analyze(**_kwargs):
        return {
            "type": "transfer",
            "amount": 100,
            "category": None,
            "account_name": "เงินสด",
            "to_account_name": None,
            "transaction_date": "2026-07-16",
            "note": "โอนเงิน",
        }

    async def fake_reply_text(_token, text):
        replies.append(text)

    monkeypatch.setattr(line_webhook, "settings", line_settings(secret))
    monkeypatch.setattr(line_webhook, "analyze_with_gemini", fake_analyze)
    monkeypatch.setattr(line_webhook, "reply_text", fake_reply_text)
    event = {
        "type": "message",
        "webhookEventId": "evt-transfer-missing-destination",
        "replyToken": "reply",
        "source": {"userId": "U-flow"},
        "message": {"id": "msg-transfer", "type": "text", "text": "โอน 100 บาท"},
    }
    body, signature = signed_body(secret, event)
    response = client.post(
        "/api/line/webhook",
        content=body,
        headers={"content-type": "application/json", "x-line-signature": signature},
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.query(PendingLineTransaction).filter(
            PendingLineTransaction.user_id == user_id
        ).count() == 0
    assert replies
    assert "บัญชีต้นทางและปลายทาง" in replies[0]


def test_customer_payment_slip_matching_owner_becomes_income_before_confirmation(client, monkeypatch):
    register_and_login(client)
    account = create_account(client, name="บัญชีรับเงินของฉัน")
    user_id = paired_user()
    with SessionLocal() as db:
        user = db.query(User).filter(User.id == user_id).one()
        user.full_name = "สิทธิโชค เฮงฉ้วน"
        db.commit()
    secret = "line-secret"
    replies = []

    async def fake_download(_message_id):
        return b"image-bytes"

    async def fake_analyze(**_kwargs):
        return {
            "type": "transfer",
            "amount": 5300,
            "category": None,
            "account_name": "Krungthai XXX-X-XX650-9",
            "to_account_name": "PromptPay XXX XXX 3795",
            "sender_name": "จักรโชค ใจดี",
            "recipient_name": "นาย สิทธิโชค เฮงฉ้วน",
            "transaction_date": "2026-07-20",
            "note": "รับชำระจากลูกค้า",
        }

    async def fake_reply(_token, messages):
        replies.extend(messages)

    monkeypatch.setattr(line_webhook, "settings", line_settings(secret))
    monkeypatch.setattr(line_webhook, "download_line_content", fake_download)
    monkeypatch.setattr(line_webhook, "analyze_with_gemini", fake_analyze)
    monkeypatch.setattr(line_webhook, "save_receipt_bytes", lambda _data, _user_id: "users/1/slip.webp")
    monkeypatch.setattr(line_webhook, "reply_messages", fake_reply)
    event = {
        "type": "message",
        "webhookEventId": "evt-external-promptpay-slip",
        "replyToken": "reply",
        "source": {"userId": "U-flow"},
        "message": {"id": "msg-slip", "type": "image"},
    }
    body, signature = signed_body(secret, event)
    response = client.post(
        "/api/line/webhook",
        content=body,
        headers={"content-type": "application/json", "x-line-signature": signature},
    )

    assert response.status_code == 200
    with SessionLocal() as db:
        pending = db.query(PendingLineTransaction).filter(
            PendingLineTransaction.user_id == user_id
        ).one()
        assert pending.payload["type"] == "income"
        assert pending.payload["category"] == "รายรับอื่น ๆ"
        assert pending.payload["account_name"] == account["name"]
        assert pending.payload["to_account_name"] is None
        assert pending.payload["sender_name"] == "จักรโชค ใจดี"
        assert pending.payload["recipient_name"] == "นาย สิทธิโชค เฮงฉ้วน"
        assert "ผู้โอน: จักรโชค ใจดี" in pending.payload["note"]
    assert replies[0]["type"] == "text"
    assert "ชื่อผู้รับตรงกับชื่อเจ้าของบัญชี" in replies[0]["text"]
    assert "รายรับ" in replies[0]["text"]
    assert replies[1]["type"] == "template"
    assert replies[1]["template"]["text"].startswith("รายรับ ฿5,300.00")
    assert "ผู้โอน: จักรโชค ใจดี" in replies[1]["template"]["text"]


def test_transfer_between_two_owned_accounts_stays_transfer(client):
    register_and_login(client)
    source = create_account(client, name="Krungthai")
    destination = create_account(client, name="เงินสด", balance="0")
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "user@example.com").one()
        analysis, notice = line_webhook.reconcile_analyzed_accounts(db, user, {
            "type": "transfer",
            "amount": "500.00",
            "category": None,
            "account_name": "Krungthai XXX-650-9",
            "to_account_name": "เงินสด",
            "sender_name": None,
            "recipient_name": None,
            "transaction_date": "2026-07-20",
            "note": "ย้ายเงินเข้ากระเป๋าเงินสด",
        })

    assert notice is None
    assert analysis["type"] == "transfer"
    assert analysis["account_name"] == source["name"]
    assert analysis["to_account_name"] == destination["name"]


def test_payment_slip_sent_by_owner_becomes_expense(client):
    register_and_login(client)
    account = create_account(client, name="Krungthai")
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "user@example.com").one()
        analysis, notice = line_webhook.reconcile_analyzed_accounts(db, user, {
            "type": "transfer",
            "amount": "250.00",
            "category": None,
            "account_name": "Krungthai",
            "to_account_name": "ร้านค้า PromptPay",
            "sender_name": "นางสาว ผู้ใช้ทดสอบ",
            "recipient_name": "ร้าน ตัวอย่าง",
            "transaction_date": "2026-07-20",
            "note": "ชำระค่าสินค้า",
        })

    assert "รายจ่าย" in notice
    assert analysis["type"] == "expense"
    assert analysis["category"] == "รายจ่ายอื่น ๆ"
    assert analysis["account_name"] == account["name"]
    assert analysis["to_account_name"] is None
    assert "ผู้รับ: ร้าน ตัวอย่าง" in analysis["note"]


def test_line_api_400_does_not_reuse_reply_token(client, monkeypatch):
    register_and_login(client)
    create_account(client)
    paired_user()
    secret = "line-secret"
    calls = {"messages": 0, "text": 0}

    async def fake_analyze(**_kwargs):
        return {
            "type": "expense",
            "amount": 50,
            "category": "อาหารและเครื่องดื่ม",
            "account_name": "เงินสด",
            "to_account_name": None,
            "transaction_date": "2026-07-16",
            "note": "ขนม",
        }

    async def fake_reply_messages(_token, _messages):
        calls["messages"] += 1
        request = httpx.Request("POST", "https://api.line.me/v2/bot/message/reply")
        response = httpx.Response(400, request=request, json={"message": "Bad request"})
        raise httpx.HTTPStatusError("Bad request", request=request, response=response)

    async def fake_reply_text(_token, _text):
        calls["text"] += 1

    monkeypatch.setattr(line_webhook, "settings", line_settings(secret))
    monkeypatch.setattr(line_webhook, "analyze_with_gemini", fake_analyze)
    monkeypatch.setattr(line_webhook, "reply_messages", fake_reply_messages)
    monkeypatch.setattr(line_webhook, "reply_text", fake_reply_text)
    event = {
        "type": "message",
        "webhookEventId": "evt-line-api-400",
        "replyToken": "reply",
        "source": {"userId": "U-flow"},
        "message": {"id": "msg-line-api-400", "type": "text", "text": "ซื้อขนม 50 บาท"},
    }
    body, signature = signed_body(secret, event)

    response = client.post(
        "/api/line/webhook",
        content=body,
        headers={"content-type": "application/json", "x-line-signature": signature},
    )

    assert response.status_code == 200
    assert calls == {"messages": 1, "text": 0}
