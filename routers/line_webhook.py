import base64
import hashlib
import hmac
import io
import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from PIL import Image
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_finance.config import settings
from personal_finance.db import SessionLocal
from personal_finance.local_time import bangkok_now, bangkok_today
from personal_finance.models import (
    Account,
    Category,
    LineEvent,
    LinePairCode,
    PendingLineTransaction,
    Transaction,
    User,
    utcnow,
)
from personal_finance.routers.transactions import delete_receipt, save_receipt_bytes
from personal_finance.security import client_key, rate_limiter
from personal_finance.services.ledger import create_transaction


router = APIRouter(prefix="/api/line", tags=["LINE Bot"])
logger = logging.getLogger("personal_finance.line")
VALID_TYPES = {"income", "expense", "transfer"}
THAI_MONTHS = (
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
)
THAI_MONTH_ALIASES = {
    1: ("มกราคม", "มค"), 2: ("กุมภาพันธ์", "กพ"), 3: ("มีนาคม", "มีค"),
    4: ("เมษายน", "เมย"), 5: ("พฤษภาคม", "พค"), 6: ("มิถุนายน", "มิย"),
    7: ("กรกฎาคม", "กค"), 8: ("สิงหาคม", "สค"), 9: ("กันยายน", "กย"),
    10: ("ตุลาคม", "ตค"), 11: ("พฤศจิกายน", "พย"), 12: ("ธันวาคม", "ธค"),
}
THAI_DIGIT_TRANSLATION = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

ANALYSIS_PROMPT = """
You extract one personal-finance transaction from Thai text or a receipt image.
Return only a JSON object:
{
  "type": "income" | "expense" | "transfer" | "unknown",
  "amount": number | null,
  "category": string | null,
  "account_name": string | null,
  "to_account_name": string | null,
  "sender_name": string | null,
  "recipient_name": string | null,
  "transaction_date": "YYYY-MM-DD" | null,
  "note": string | null
}
Use only these categories:
Expense: อาหารและเครื่องดื่ม, การเดินทาง / ยานพาหนะ, ช้อปปิ้ง,
ที่พักอาศัย / ค่าเช่า, ค่าสาธารณูปโภค (น้ำ, ไฟ, เน็ต),
ความบันเทิง / ท่องเที่ยว, สุขภาพ / รักษาพยาบาล, การศึกษา,
ของใช้ในบ้าน, รายจ่ายอื่น ๆ.
Income: เงินเดือน, ธุรกิจส่วนตัว, การลงทุน, รายรับอื่น ๆ.
Classification rules:
- Buying, paying, spending, fees, bills, food, or shopping are expenses, even when a bank account is named.
- Use transfer only when the user explicitly moves money between two of their own accounts.
- A transfer must include two distinct account names: account_name is the source and to_account_name is the destination.
- For a bank slip, extract the visible sender (จาก) into sender_name and recipient (ไปยัง) into recipient_name.
- If the recipient name matches the registered owner and the sender does not, this is income. account_name is the receiving account.
- If the sender name matches the registered owner and the recipient does not, this is an expense. account_name is the paying account.
- If both bank accounts belong to the registered owner, this is a transfer between own accounts.
- Keep the external sender or recipient name in note.
- Never infer transfer merely because a bank account, bank name, account number, promptpay, or slip is present.
Never invent an amount or bank account. If uncertain, use null or unknown.
""".strip()


def verify_line_signature(body: bytes, signature: str | None) -> None:
    if not settings.line_channel_secret:
        raise HTTPException(status_code=503, detail="LINE_CHANNEL_SECRET is not configured")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing LINE signature")
    digest = hmac.new(settings.line_channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid LINE signature")


async def line_api(path: str, *, payload: dict | None = None) -> httpx.Response:
    if not settings.line_channel_access_token:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN is not configured")
    headers = {"Authorization": f"Bearer {settings.line_channel_access_token}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        if payload is None:
            response = await client.get(f"https://api-data.line.me{path}", headers=headers)
        else:
            headers["Content-Type"] = "application/json"
            response = await client.post(f"https://api.line.me{path}", json=payload, headers=headers)
    response.raise_for_status()
    return response


async def reply_messages(reply_token: str, messages: list[dict]) -> None:
    await line_api("/v2/bot/message/reply", payload={"replyToken": reply_token, "messages": messages[:5]})


async def reply_text(reply_token: str, text: str) -> None:
    await reply_messages(reply_token, [{"type": "text", "text": text[:5000]}])


def _normalise_report_year(raw_year: int, default_year: int) -> int:
    if raw_year < 100:
        raw_year += 2500
    if raw_year >= 2400:
        raw_year -= 543
    year = raw_year or default_year
    if not 2000 <= year <= 2200:
        raise ValueError("ปีไม่ถูกต้อง")
    return year


def parse_summary_command(text: str) -> tuple[str, int, int | None] | None:
    normalized = text.translate(THAI_DIGIT_TRANSLATION).lower().strip()
    if "สรุป" not in normalized or not any(word in normalized for word in ("เดือน", "ปี")):
        return None
    today = bangkok_today()
    compact = re.sub(r"[\s.]", "", normalized)

    if "เดือน" in normalized:
        if "เดือนนี้" in normalized:
            month = today.month
        else:
            slash_match = re.search(r"(\d{1,2})\s*/\s*(\d{2,4})", normalized)
            number_match = re.search(r"เดือน\s*(\d{1,2})", normalized)
            if slash_match:
                month = int(slash_match.group(1))
            elif number_match:
                month = int(number_match.group(1))
            else:
                month = next((value for value, aliases in THAI_MONTH_ALIASES.items() if any(alias in compact for alias in aliases)), 0)
        if not 1 <= month <= 12:
            raise ValueError("เดือนไม่ถูกต้อง")

        slash_match = re.search(r"(\d{1,2})\s*/\s*(\d{2,4})", normalized)
        year_match = re.search(r"(?:ปี|พ\.?ศ\.?)\s*(\d{2,4})", normalized)
        bare_year_match = re.search(r"(?<!\d)(\d{4})(?!\d)", normalized)
        raw_year = (
            int(slash_match.group(2))
            if slash_match
            else int(year_match.group(1))
            if year_match
            else int(bare_year_match.group(1))
            if bare_year_match
            else today.year
        )
        return "month", _normalise_report_year(raw_year, today.year), month

    if "ปีนี้" in normalized:
        return "year", today.year, None
    year_match = re.search(r"(?:ปี|พ\.?ศ\.?)\s*(\d{2,4})", normalized)
    if not year_match:
        raise ValueError("ปีไม่ถูกต้อง")
    return "year", _normalise_report_year(int(year_match.group(1)), today.year), None


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + delta
    return index // 12, index % 12 + 1


def _period_totals(db: Session, user_id: int, start: date, end: date) -> dict[str, Decimal | int]:
    values: dict[str, Decimal | int] = {
        "income": Decimal("0"), "expense": Decimal("0"),
        "income_count": 0, "expense_count": 0,
    }
    rows = db.query(
        Transaction.type,
        func.coalesce(func.sum(Transaction.amount), 0),
        func.count(Transaction.id),
    ).filter(
        Transaction.user_id == user_id,
        Transaction.type.in_(("income", "expense")),
        Transaction.source != "system",
        Transaction.date >= start,
        Transaction.date < end,
    ).group_by(Transaction.type).all()
    for tx_type, amount, count in rows:
        values[tx_type] = Decimal(amount or 0)
        values[f"{tx_type}_count"] = int(count or 0)
    return values


def _top_expense_categories(db: Session, user_id: int, start: date, end: date, limit: int = 3) -> list[tuple[str, Decimal]]:
    rows = db.query(Category.name, func.sum(Transaction.amount)).join(
        Transaction, Transaction.category_id == Category.id
    ).filter(
        Transaction.user_id == user_id,
        Transaction.type == "expense",
        Transaction.source != "system",
        Transaction.date >= start,
        Transaction.date < end,
    ).group_by(Category.id, Category.name).order_by(func.sum(Transaction.amount).desc()).limit(limit).all()
    return [(str(name), Decimal(amount or 0)) for name, amount in rows]


def _money(value: Decimal | int) -> str:
    amount = Decimal(value)
    return f"-฿{abs(amount):,.2f}" if amount < 0 else f"฿{amount:,.2f}"


def build_financial_summary(db: Session, user_id: int, period: str, year: int, month: int | None) -> str:
    if period == "month" and month is not None:
        start, end = _month_bounds(year, month)
        totals = _period_totals(db, user_id, start, end)
        income = Decimal(totals["income"])
        expense = Decimal(totals["expense"])
        net = income - expense
        previous_year, previous_month = _shift_month(year, month, -1)
        previous = _period_totals(db, user_id, *_month_bounds(previous_year, previous_month))
        previous_expense = Decimal(previous["expense"])
        if previous_expense:
            expense_change = (expense - previous_expense) / previous_expense * 100
            comparison = f"รายจ่าย{'เพิ่ม' if expense_change >= 0 else 'ลด'}ลง {abs(expense_change):,.1f}% จากเดือนก่อน"
        elif expense:
            comparison = "เดือนก่อนยังไม่มีรายจ่ายสำหรับเปรียบเทียบ"
        else:
            comparison = "ยังไม่มีรายจ่ายในเดือนนี้และเดือนก่อน"
        category_lines = _top_expense_categories(db, user_id, start, end)
        top_text = "\n".join(
            f"{index}. {name} {_money(amount)}" for index, (name, amount) in enumerate(category_lines, 1)
        ) or "ยังไม่มีรายจ่าย"
        heading = f"📊 สรุปการเงินเดือน{THAI_MONTHS[month - 1]} {year + 543}"
        body = (
            f"{heading}\n\n"
            f"🟢 รายรับ: {_money(income)}\n"
            f"🔴 รายจ่าย: {_money(expense)}\n"
            f"💰 สุทธิ: {'+' if net > 0 else ''}{_money(net)}\n\n"
            f"จำนวนรายการ\n"
            f"• รายรับ {totals['income_count']} รายการ\n"
            f"• รายจ่าย {totals['expense_count']} รายการ\n\n"
            f"รายจ่ายสูงสุด\n{top_text}\n\n"
            f"เทียบเดือนก่อน\n• {comparison}"
        )
    else:
        start, end = date(year, 1, 1), date(year + 1, 1, 1)
        totals = _period_totals(db, user_id, start, end)
        income = Decimal(totals["income"])
        expense = Decimal(totals["expense"])
        net = income - expense
        today = bangkok_today()
        divisor = today.month if year == today.year else 12
        divisor = max(divisor, 1)
        savings_rate = (net / income * 100) if income else Decimal("0")
        month_rows = db.query(
            func.extract("month", Transaction.date),
            func.sum(Transaction.amount),
        ).filter(
            Transaction.user_id == user_id,
            Transaction.type == "expense",
            Transaction.source != "system",
            Transaction.date >= start,
            Transaction.date < end,
        ).group_by(func.extract("month", Transaction.date)).all()
        highest_month = max(month_rows, key=lambda row: Decimal(row[1] or 0), default=None)
        highest_text = THAI_MONTHS[int(highest_month[0]) - 1] if highest_month else "ยังไม่มีข้อมูล"
        top_categories = _top_expense_categories(db, user_id, start, end, limit=1)
        top_category = top_categories[0][0] if top_categories else "ยังไม่มีข้อมูล"
        body = (
            f"📈 สรุปการเงินปี {year + 543}\n\n"
            f"🟢 รายรับรวม: {_money(income)}\n"
            f"🔴 รายจ่ายรวม: {_money(expense)}\n"
            f"💰 สุทธิทั้งปี: {'+' if net > 0 else ''}{_money(net)}\n\n"
            f"• รายรับเฉลี่ยต่อเดือน {_money(income / divisor)}\n"
            f"• รายจ่ายเฉลี่ยต่อเดือน {_money(expense / divisor)}\n"
            f"• เดือนที่ใช้จ่ายสูงสุด: {highest_text}\n"
            f"• หมวดรายจ่ายสูงสุด: {top_category}\n"
            f"• อัตราการออม: {savings_rate:,.1f}%\n\n"
            f"จำนวนรายการ: รายรับ {totals['income_count']} · รายจ่าย {totals['expense_count']}"
        )
    now = bangkok_now()
    return f"{body}\n\nข้อมูล ณ {now.day} {THAI_MONTHS[now.month - 1]} {now.year + 543} เวลา {now:%H:%M} น."


async def handle_summary_command(db: Session, reply_token: str, user: User, text: str) -> bool:
    if "สรุป" not in text or not any(word in text for word in ("เดือน", "ปี")):
        return False
    try:
        request = parse_summary_command(text)
    except ValueError:
        await reply_text(
            reply_token,
            "รูปแบบคำสั่งไม่ถูกต้อง\nตัวอย่าง: ขอสรุปเดือนนี้, ขอสรุปเดือนกรกฎาคม 2569 หรือ ขอสรุปปีนี้",
        )
        return True
    if request is None:
        return False
    period, year, month = request
    await reply_text(reply_token, build_financial_summary(db, user.id, period, year, month))
    return True


async def download_line_content(message_id: str) -> bytes:
    response = await line_api(f"/v2/bot/message/{message_id}/content")
    if len(response.content) > settings.max_upload_bytes:
        raise ValueError("รูปภาพมีขนาดใหญ่เกินกำหนด")
    return response.content


def image_mime(image_bytes: bytes) -> str:
    with Image.open(io.BytesIO(image_bytes)) as image:
        return {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }.get(image.format or "", "image/jpeg")


async def analyze_with_gemini(
    *,
    text: str | None = None,
    image_bytes: bytes | None = None,
    owner_name: str | None = None,
) -> dict:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    parts: list[dict] = [{"text": ANALYSIS_PROMPT}]
    if owner_name:
        parts.append({
            "text": (
                "Registered owner name for direction matching only: "
                f"{owner_name[:150]}"
            )
        })
    if text is not None:
        parts.append({"text": f"User message: {text[:3000]}"})
    if image_bytes is not None:
        parts.append({
            "inlineData": {
                "mimeType": image_mime(image_bytes),
                "data": base64.b64encode(image_bytes).decode("ascii"),
            }
        })
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload, headers={
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        })
    response.raise_for_status()
    result = response.json()
    raw = result["candidates"][0]["content"]["parts"][0]["text"].strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    return json.loads(raw)


def normalize_analysis(
    raw: dict,
    fallback_note: str,
    *,
    original_text: str | None = None,
) -> dict | None:
    tx_type = str(raw.get("type") or "").lower()
    if tx_type not in VALID_TYPES:
        return None
    try:
        amount = Decimal(str(raw.get("amount"))).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return None
    if amount <= 0 or amount > Decimal("999999999999.99"):
        return None
    parsed_date = bangkok_today()
    if raw.get("transaction_date"):
        try:
            candidate = date.fromisoformat(str(raw["transaction_date"]))
            if candidate <= bangkok_today():
                parsed_date = candidate
        except ValueError:
            pass
    return {
        "type": tx_type,
        "amount": str(amount),
        "category": str(raw.get("category") or "")[:100] or None,
        "account_name": str(raw.get("account_name") or "")[:100] or None,
        "to_account_name": str(raw.get("to_account_name") or "")[:100] or None,
        "sender_name": str(raw.get("sender_name") or "")[:150] or None,
        "recipient_name": str(raw.get("recipient_name") or "")[:150] or None,
        "transaction_date": parsed_date.isoformat(),
        # Text sent through LINE is evidence supplied by the user. Preserve it
        # verbatim instead of replacing it with Gemini's summarized note.
        "note": str(original_text if original_text is not None else (raw.get("note") or fallback_note))[:1000],
    }


def event_key(event: dict) -> str:
    return str(
        event.get("webhookEventId")
        or event.get("message", {}).get("id")
        or event.get("replyToken")
        or hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
    )[:150]


def begin_event(db: Session, key: str) -> bool:
    row = db.query(LineEvent).filter(LineEvent.event_key == key).first()
    if row and row.status in {"processing", "processed"}:
        return False
    if row:
        row.status = "processing"
    else:
        db.add(LineEvent(event_key=key, status="processing"))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def finish_event(db: Session, key: str, status: str) -> None:
    row = db.query(LineEvent).filter(LineEvent.event_key == key).first()
    if row:
        row.status = status
        row.processed_at = utcnow() if status == "processed" else None
        db.commit()


def pending_message(pending: PendingLineTransaction) -> dict:
    payload = pending.payload
    type_label = {"income": "รายรับ", "expense": "รายจ่าย", "transfer": "โอนเงิน"}[payload["type"]]
    account_text = payload.get("account_name") or "ให้ระบบเลือก"
    if payload["type"] == "transfer":
        account_text = f"{account_text} → {payload.get('to_account_name') or 'ยังไม่ระบุปลายทาง'}"
    counterparty_text = ""
    if payload["type"] == "income" and payload.get("sender_name"):
        counterparty_text = f"\nผู้โอน: {payload['sender_name']}"
    elif payload["type"] == "expense" and payload.get("recipient_name"):
        counterparty_text = f"\nผู้รับ: {payload['recipient_name']}"
    details = (
        f"{type_label} ฿{Decimal(payload['amount']):,.2f}\n"
        f"หมวด: {payload.get('category') or '-'}\n"
        f"บัญชี: {account_text}{counterparty_text}\n"
        f"วันที่: {payload['transaction_date']}"
    )
    return {
        "type": "template",
        "altText": f"กรุณาตรวจสอบรายการ #{pending.id}",
        "template": {
            "type": "buttons",
            "text": details[:160],
            "actions": [
                {"type": "postback", "label": "ยืนยัน", "data": f"pf_action=confirm&pending_id={pending.id}", "displayText": f"ยืนยันรายการ #{pending.id}"},
                {"type": "postback", "label": "แก้ยอดเงิน", "data": f"pf_action=edit&pending_id={pending.id}", "displayText": f"แก้ไขรายการ #{pending.id}"},
                {"type": "postback", "label": "ยกเลิก", "data": f"pf_action=cancel&pending_id={pending.id}", "displayText": f"ยกเลิกรายการ #{pending.id}"},
            ],
        },
    }


def _account_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def match_existing_account(accounts: list[Account], name: str | None) -> Account | None:
    if name:
        needle = _account_key(name)
        for account in accounts:
            haystack = _account_key(account.name)
            if needle and haystack and (needle in haystack or haystack in needle):
                return account
    return None


def _person_key(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.casefold().strip()
    normalized = re.sub(
        r"^(?:นาย|นางสาว|นาง|น\.?\s*ส\.?|mr\.?|mrs\.?|miss)\s*",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def person_matches_owner(person_name: str | None, owner_name: str) -> bool:
    person_key = _person_key(person_name)
    owner_key = _person_key(owner_name)
    return bool(person_key and owner_key and person_key == owner_key)


def _append_counterparty(note: str | None, label: str, name: str | None) -> str:
    original = str(note or "").strip()
    counterparty = str(name or "").strip()
    if not counterparty or counterparty.casefold() in original.casefold():
        return original[:1000]
    addition = f"{label}: {counterparty}"
    return f"{original} · {addition}"[:1000] if original else addition[:1000]


def reconcile_analyzed_accounts(
    db: Session,
    user: User,
    analysis: dict,
) -> tuple[dict, str | None]:
    """Resolve account direction using the registered owner's name before account names."""
    result = dict(analysis)
    accounts = db.query(Account).filter(Account.user_id == user.id).order_by(Account.id).all()
    source = match_existing_account(accounts, result.get("account_name"))
    destination = match_existing_account(accounts, result.get("to_account_name"))
    owner_is_sender = person_matches_owner(result.get("sender_name"), user.full_name)
    owner_is_recipient = person_matches_owner(result.get("recipient_name"), user.full_name)

    # A customer paying the registered owner is income, regardless of the
    # transaction label guessed from the word "transfer" on the slip.
    if owner_is_recipient and not owner_is_sender:
        receiving_account = destination
        if receiving_account is None and result.get("type") == "income":
            receiving_account = source
        if receiving_account is None and len(accounts) == 1:
            receiving_account = accounts[0]
        if receiving_account is None:
            raise ValueError(
                "ตรวจพบว่าเป็นรายรับ แต่ยังระบุบัญชีรับเงินไม่ได้ "
                "กรุณาพิมพ์ชื่อบัญชีรับเงินให้ตรงกับหน้าเว็บ"
            )
        result["type"] = "income"
        result["category"] = "รายรับอื่น ๆ"
        result["account_name"] = receiving_account.name
        result["to_account_name"] = None
        result["note"] = _append_counterparty(
            result.get("note"),
            "ผู้โอน",
            result.get("sender_name"),
        )
        return result, "ℹ️ ชื่อผู้รับตรงกับชื่อเจ้าของบัญชี ระบบจึงจัดสลิปนี้เป็นรายรับ"

    # The registered owner paying another person or merchant is an expense.
    if owner_is_sender and not owner_is_recipient:
        paying_account = source or (accounts[0] if len(accounts) == 1 else None)
        if paying_account is None:
            raise ValueError(
                "ตรวจพบว่าเป็นรายจ่าย แต่ยังระบุบัญชีที่จ่ายไม่ได้ "
                "กรุณาพิมพ์ชื่อบัญชีให้ตรงกับหน้าเว็บ"
            )
        result["type"] = "expense"
        result["category"] = "รายจ่ายอื่น ๆ"
        result["account_name"] = paying_account.name
        result["to_account_name"] = None
        result["note"] = _append_counterparty(
            result.get("note"),
            "ผู้รับ",
            result.get("recipient_name"),
        )
        return result, "ℹ️ ชื่อผู้ส่งตรงกับชื่อเจ้าของบัญชี ระบบจึงจัดสลิปนี้เป็นรายจ่าย"

    # Two recognised accounts owned by the same user are an internal transfer.
    if result["type"] == "transfer" and source and destination and source.id != destination.id:
        result["account_name"] = source.name
        result["to_account_name"] = destination.name
        return result, None

    if result["type"] == "transfer":
        if not result.get("account_name") or not result.get("to_account_name"):
            raise ValueError(
                "รายการโอนเงินต้องระบุทั้งบัญชีต้นทางและปลายทาง\n"
                "ตัวอย่าง: โอน 100 จาก Krungthai ไป เงินสด"
            )
        raise ValueError(
            "ยังระบุไม่ได้ว่าสลิปนี้เป็นเงินเข้า เงินออก หรือโอนระหว่างบัญชี "
            "กรุณาระบุชื่อผู้ส่ง ผู้รับ หรือชื่อบัญชีให้ชัดเจน"
        )

    # Non-slip income/expense records still use the existing account-matching rule.
    matched = source or (accounts[0] if len(accounts) == 1 else None)
    if matched is not None:
        result["account_name"] = matched.name
    return result, None


def find_account(db: Session, user_id: int, name: str | None, *, destination: bool = False) -> Account:
    accounts = db.query(Account).filter(Account.user_id == user_id).order_by(Account.id).all()
    matched = match_existing_account(accounts, name)
    if matched is not None:
        return matched
    if len(accounts) == 1 and not destination:
        return accounts[0]
    if not accounts and not destination:
        account = Account(user_id=user_id, name="เงินสด", type="cash", balance=Decimal("0.00"))
        db.add(account)
        db.flush()
        return account
    raise HTTPException(
        status_code=422,
        detail="ไม่สามารถระบุบัญชีได้ กรุณาระบุชื่อบัญชีในข้อความให้ตรงกับหน้าเว็บ",
    )


def find_category(db: Session, user_id: int, name: str | None, tx_type: str) -> Category | None:
    if tx_type == "transfer":
        return None
    category = None
    if name:
        category = db.query(Category).filter(
            ((Category.user_id == user_id) | (Category.user_id.is_(None))),
            Category.name == name,
            Category.type == tx_type,
        ).first()
    if category:
        return category
    fallback = "รายรับอื่น ๆ" if tx_type == "income" else "รายจ่ายอื่น ๆ"
    return db.query(Category).filter(Category.user_id == user_id, Category.name == fallback, Category.type == tx_type).first()


def confirm_pending(db: Session, pending: PendingLineTransaction) -> str:
    if pending.status == "confirmed":
        tx = db.query(Transaction).filter(Transaction.external_id == f"pending:{pending.id}", Transaction.source == "line").first()
        return f"✅ รายการ #{pending.id} ถูกบันทึกแล้ว" + (f" (ธุรกรรม #{tx.id})" if tx else "")
    if pending.status != "pending":
        return f"รายการ #{pending.id} ไม่อยู่ในสถานะที่ยืนยันได้"
    if pending.expires_at <= utcnow():
        pending.status = "expired"
        pending.resolved_at = utcnow()
        db.commit()
        delete_receipt(pending.receipt_path)
        return f"⌛ รายการ #{pending.id} หมดอายุแล้ว กรุณาส่งข้อมูลใหม่"

    data = pending.payload
    source = find_account(db, pending.user_id, data.get("account_name"))
    destination = None
    if data["type"] == "transfer":
        if not data.get("to_account_name"):
            raise HTTPException(
                status_code=422,
                detail="รายการโอนเงินต้องระบุบัญชีปลายทาง เช่น “โอน 100 จาก Krungthai ไป เงินสด”",
            )
        destination = find_account(db, pending.user_id, data.get("to_account_name"), destination=True)
        if destination.id == source.id:
            raise HTTPException(status_code=422, detail="บัญชีต้นทางและปลายทางต้องไม่ซ้ำกัน")
    category = find_category(db, pending.user_id, data.get("category"), data["type"])
    tx = create_transaction(
        db,
        user_id=pending.user_id,
        tx_type=data["type"],
        amount=data["amount"],
        account_id=source.id,
        category_id=category.id if category else None,
        to_account_id=destination.id if destination else None,
        tx_date=date.fromisoformat(data["transaction_date"]),
        note=data.get("note"),
        receipt_path=pending.receipt_path,
        source="line",
        external_id=f"pending:{pending.id}",
    )
    pending.status = "confirmed"
    pending.resolved_at = utcnow()
    db.commit()
    db.refresh(tx)
    return f"✅ บันทึกสำเร็จ #{tx.id}\n{data['type']} ฿{Decimal(data['amount']):,.2f}\nบัญชี {source.name}"


async def handle_pairing(db: Session, reply_token: str, line_id: str, text: str) -> bool:
    if not text.lower().startswith("ผูกบัญชี "):
        return False
    code = text.split(maxsplit=1)[1].strip().upper()
    pair = db.query(LinePairCode).filter(
        LinePairCode.code == code,
        LinePairCode.used_at.is_(None),
        LinePairCode.expires_at > utcnow(),
    ).with_for_update().first()
    if not pair:
        await reply_text(reply_token, "❌ ไม่พบรหัสนี้หรือรหัสหมดอายุแล้ว")
        return True
    existing = db.query(User).filter(User.line_user_id == line_id, User.id != pair.user_id).first()
    if existing:
        await reply_text(reply_token, "❌ LINE นี้เชื่อมกับบัญชีอื่นอยู่ กรุณายกเลิกการเชื่อมต่อจากบัญชีเดิมก่อน")
        return True
    user = db.get(User, pair.user_id)
    user.line_user_id = line_id
    pair.used_at = utcnow()
    db.commit()
    await reply_text(reply_token, f"✅ ผูกบัญชีสำเร็จ\nผู้ใช้งาน: {user.full_name}\nหลังจากนี้ระบบจะให้ตรวจสอบก่อนบันทึกทุกครั้ง")
    return True


async def handle_postback(db: Session, event: dict, line_id: str, reply_token: str) -> None:
    params = parse_qs(event.get("postback", {}).get("data", ""))
    action = (params.get("pf_action") or [""])[0]
    try:
        pending_id = int((params.get("pending_id") or ["0"])[0])
    except ValueError:
        await reply_text(reply_token, "คำสั่งไม่ถูกต้อง")
        return
    pending = db.query(PendingLineTransaction).filter(
        PendingLineTransaction.id == pending_id,
        PendingLineTransaction.line_user_id == line_id,
    ).with_for_update().first()
    if not pending:
        await reply_text(reply_token, "ไม่พบรายการที่รอตรวจสอบ")
        return
    if action == "confirm":
        try:
            await reply_text(reply_token, confirm_pending(db, pending))
        except HTTPException as exc:
            db.rollback()
            await reply_text(reply_token, f"❌ ยังบันทึกไม่ได้: {exc.detail}")
    elif action == "cancel":
        if pending.status == "pending":
            pending.status = "cancelled"
            pending.resolved_at = utcnow()
            db.commit()
            delete_receipt(pending.receipt_path)
        await reply_text(reply_token, f"ยกเลิกรายการ #{pending.id} แล้ว")
    elif action == "edit":
        await reply_text(reply_token, f"พิมพ์ “แก้ไข {pending.id} จำนวนเงินใหม่”\nตัวอย่าง: แก้ไข {pending.id} 250")
    else:
        await reply_text(reply_token, "คำสั่งไม่ถูกต้อง")


async def handle_edit_command(db: Session, reply_token: str, line_id: str, text: str) -> bool:
    match = re.fullmatch(r"แก้ไข\s+#?(\d+)\s+([\d,]+(?:\.\d{1,2})?)", text.strip())
    if not match:
        return False
    pending_id = int(match.group(1))
    try:
        amount = Decimal(match.group(2).replace(",", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        await reply_text(reply_token, "จำนวนเงินไม่ถูกต้อง")
        return True
    if amount <= 0:
        await reply_text(reply_token, "จำนวนเงินต้องมากกว่า 0")
        return True
    pending = db.query(PendingLineTransaction).filter(
        PendingLineTransaction.id == pending_id,
        PendingLineTransaction.line_user_id == line_id,
        PendingLineTransaction.status == "pending",
        PendingLineTransaction.expires_at > utcnow(),
    ).with_for_update().first()
    if not pending:
        await reply_text(reply_token, "ไม่พบรายการที่แก้ไขได้ หรือรายการหมดอายุแล้ว")
        return True
    updated = dict(pending.payload)
    updated["amount"] = str(amount)
    pending.payload = updated
    db.commit()
    await reply_messages(reply_token, [{"type": "text", "text": "แก้ไขยอดเงินแล้ว กรุณาตรวจสอบอีกครั้ง"}, pending_message(pending)])
    return True


async def create_pending_from_event(
    db: Session,
    *,
    event_id: str,
    user: User,
    line_id: str,
    analysis: dict,
    receipt_path: str | None,
) -> PendingLineTransaction:
    existing = db.query(PendingLineTransaction).filter(PendingLineTransaction.event_key == event_id).first()
    if existing:
        return existing
    pending = PendingLineTransaction(
        event_key=event_id,
        user_id=user.id,
        line_user_id=line_id,
        payload=analysis,
        receipt_path=receipt_path,
        status="pending",
        expires_at=utcnow() + timedelta(minutes=15),
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)
    return pending


@router.post("/webhook")
async def line_webhook(request: Request, x_line_signature: str | None = Header(default=None)):
    body_bytes = await request.body()
    verify_line_signature(body_bytes, x_line_signature)
    rate_limiter.check(client_key(request, "line-webhook"), limit=120, window_seconds=60)
    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    for event in body.get("events", []):
        reply_token = event.get("replyToken")
        line_id = event.get("source", {}).get("userId")
        if not reply_token or not line_id:
            continue
        key = event_key(event)
        with SessionLocal() as db:
            if not begin_event(db, key):
                continue
            try:
                if event.get("type") == "postback":
                    await handle_postback(db, event, line_id, reply_token)
                    finish_event(db, key, "processed")
                    continue
                if event.get("type") != "message":
                    finish_event(db, key, "processed")
                    continue

                message = event.get("message", {})
                msg_type = message.get("type")
                text = message.get("text", "").strip() if msg_type == "text" else ""
                if msg_type == "text" and await handle_pairing(db, reply_token, line_id, text):
                    finish_event(db, key, "processed")
                    continue

                user = db.query(User).filter(User.line_user_id == line_id).first()
                if not user:
                    await reply_text(reply_token, "❌ ยังไม่ได้ผูกบัญชี\nขอรหัสจากหน้าเว็บ แล้วพิมพ์ “ผูกบัญชี PF-XXXXXXXX”")
                    finish_event(db, key, "processed")
                    continue
                if msg_type == "text" and await handle_summary_command(db, reply_token, user, text):
                    finish_event(db, key, "processed")
                    continue
                if msg_type == "text" and await handle_edit_command(db, reply_token, line_id, text):
                    finish_event(db, key, "processed")
                    continue

                receipt_path = None
                if msg_type == "text":
                    raw_analysis = await analyze_with_gemini(text=text, owner_name=user.full_name)
                    fallback_note = text
                elif msg_type == "image":
                    image_bytes = await download_line_content(str(message.get("id")))
                    raw_analysis = await analyze_with_gemini(
                        image_bytes=image_bytes,
                        owner_name=user.full_name,
                    )
                    fallback_note = "วิเคราะห์จากรูปใบเสร็จหรือสลิป"
                    receipt_path = save_receipt_bytes(image_bytes, user.id)
                else:
                    await reply_text(reply_token, "รองรับเฉพาะข้อความและรูปใบเสร็จ/สลิป")
                    finish_event(db, key, "processed")
                    continue

                analysis = normalize_analysis(
                    raw_analysis,
                    fallback_note,
                    original_text=text if msg_type == "text" else None,
                )
                if not analysis:
                    delete_receipt(receipt_path)
                    await reply_text(reply_token, "ไม่พบข้อมูลจำนวนเงินที่ชัดเจน จึงยังไม่มีการบันทึกรายการ")
                    finish_event(db, key, "processed")
                    continue
                try:
                    analysis, classification_notice = reconcile_analyzed_accounts(
                        db,
                        user,
                        analysis,
                    )
                except ValueError as exc:
                    delete_receipt(receipt_path)
                    await reply_text(reply_token, f"❌ {exc}")
                    finish_event(db, key, "processed")
                    continue
                pending = await create_pending_from_event(
                    db,
                    event_id=key,
                    user=user,
                    line_id=line_id,
                    analysis=analysis,
                    receipt_path=receipt_path,
                )
                messages = []
                if msg_type == "text":
                    messages.append({
                        "type": "text",
                        "text": f"ข้อความต้นฉบับที่ระบบจะบันทึก:\n{analysis['note']}",
                    })
                if classification_notice:
                    messages.append({"type": "text", "text": classification_notice})
                messages.append(pending_message(pending))
                await reply_messages(reply_token, messages)
                finish_event(db, key, "processed")
            except httpx.HTTPStatusError as exc:
                db.rollback()
                finish_event(db, key, "failed")
                logger.exception(
                    "External API rejected LINE event event_key=%s status=%s url=%s response=%s",
                    key,
                    exc.response.status_code,
                    exc.request.url,
                    exc.response.text[:1000],
                )
                if exc.request.url.host not in {"api.line.me", "api-data.line.me"}:
                    try:
                        await reply_text(reply_token, "❌ ระบบประมวลผลไม่สำเร็จ กรุณาลองใหม่ภายหลัง")
                    except Exception:
                        logger.exception("Unable to send LINE error reply event_key=%s", key)
            except Exception:
                db.rollback()
                finish_event(db, key, "failed")
                logger.exception("LINE event failed event_key=%s", key)
                try:
                    await reply_text(reply_token, "❌ ระบบประมวลผลไม่สำเร็จ กรุณาลองใหม่ภายหลัง")
                except Exception:
                    logger.exception("Unable to send LINE error reply event_key=%s", key)

    return {"status": "ok"}
