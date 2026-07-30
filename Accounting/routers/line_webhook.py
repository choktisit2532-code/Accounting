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
    FleetDocument,
    FleetExpense,
    FleetVehicle,
    utcnow,
)
from personal_finance.routers.transactions import delete_receipt, save_receipt_bytes
from personal_finance.security import client_key, rate_limiter
from personal_finance.services.ledger import create_transaction


router = APIRouter(prefix="/api/line", tags=["LINE Bot"])
logger = logging.getLogger("personal_finance.line")
VALID_TYPES = {"income", "expense", "transfer"}
THAI_MONTHS = (
    "à¸¡à¸à¸£à¸²à¸„à¸¡", "à¸à¸¸à¸¡à¸ à¸²à¸žà¸±à¸™à¸˜à¹Œ", "à¸¡à¸µà¸™à¸²à¸„à¸¡", "à¹€à¸¡à¸©à¸²à¸¢à¸™", "à¸žà¸¤à¸©à¸ à¸²à¸„à¸¡", "à¸¡à¸´à¸–à¸¸à¸™à¸²à¸¢à¸™",
    "à¸à¸£à¸à¸Žà¸²à¸„à¸¡", "à¸ªà¸´à¸‡à¸«à¸²à¸„à¸¡", "à¸à¸±à¸™à¸¢à¸²à¸¢à¸™", "à¸•à¸¸à¸¥à¸²à¸„à¸¡", "à¸žà¸¤à¸¨à¸ˆà¸´à¸à¸²à¸¢à¸™", "à¸˜à¸±à¸™à¸§à¸²à¸„à¸¡",
)
THAI_MONTH_ALIASES = {
    1: ("à¸¡à¸à¸£à¸²à¸„à¸¡", "à¸¡à¸„"), 2: ("à¸à¸¸à¸¡à¸ à¸²à¸žà¸±à¸™à¸˜à¹Œ", "à¸à¸ž"), 3: ("à¸¡à¸µà¸™à¸²à¸„à¸¡", "à¸¡à¸µà¸„"),
    4: ("à¹€à¸¡à¸©à¸²à¸¢à¸™", "à¹€à¸¡à¸¢"), 5: ("à¸žà¸¤à¸©à¸ à¸²à¸„à¸¡", "à¸žà¸„"), 6: ("à¸¡à¸´à¸–à¸¸à¸™à¸²à¸¢à¸™", "à¸¡à¸´à¸¢"),
    7: ("à¸à¸£à¸à¸Žà¸²à¸„à¸¡", "à¸à¸„"), 8: ("à¸ªà¸´à¸‡à¸«à¸²à¸„à¸¡", "à¸ªà¸„"), 9: ("à¸à¸±à¸™à¸¢à¸²à¸¢à¸™", "à¸à¸¢"),
    10: ("à¸•à¸¸à¸¥à¸²à¸„à¸¡", "à¸•à¸„"), 11: ("à¸žà¸¤à¸¨à¸ˆà¸´à¸à¸²à¸¢à¸™", "à¸žà¸¢"), 12: ("à¸˜à¸±à¸™à¸§à¸²à¸„à¸¡", "à¸˜à¸„"),
}
THAI_DIGIT_TRANSLATION = str.maketrans("à¹à¹‘à¹’à¹“à¹”à¹•à¹–à¹—à¹˜à¹™", "0123456789")

ANALYSIS_PROMPT = """
You extract one or more personal-finance transactions from Thai text or a receipt image.
For text, return only a JSON array (one object per distinct amount/item). For an image,
return an array containing the single transaction shown. Never combine separate purchases.
Example: "à¸„à¹ˆà¸²à¸à¸²à¹à¸Ÿ30 à¸„à¹ˆà¸²à¸‚à¹‰à¸²à¸§85 à¸‹à¸·à¹‰à¸­à¹€à¸«à¸¥à¹‡à¸580" must return 3 objects.
Each object has this shape:
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
Expense: à¸­à¸²à¸«à¸²à¸£à¹à¸¥à¸°à¹€à¸„à¸£à¸·à¹ˆà¸­à¸‡à¸”à¸·à¹ˆà¸¡, à¸à¸²à¸£à¹€à¸”à¸´à¸™à¸—à¸²à¸‡ / à¸¢à¸²à¸™à¸žà¸²à¸«à¸™à¸°, à¸Šà¹‰à¸­à¸›à¸›à¸´à¹‰à¸‡,
à¸—à¸µà¹ˆà¸žà¸±à¸à¸­à¸²à¸¨à¸±à¸¢ / à¸„à¹ˆà¸²à¹€à¸Šà¹ˆà¸², à¸„à¹ˆà¸²à¸ªà¸²à¸˜à¸²à¸£à¸“à¸¹à¸›à¹‚à¸ à¸„ (à¸™à¹‰à¸³, à¹„à¸Ÿ, à¹€à¸™à¹‡à¸•),
à¸„à¸§à¸²à¸¡à¸šà¸±à¸™à¹€à¸—à¸´à¸‡ / à¸—à¹ˆà¸­à¸‡à¹€à¸—à¸µà¹ˆà¸¢à¸§, à¸ªà¸¸à¸‚à¸ à¸²à¸ž / à¸£à¸±à¸à¸©à¸²à¸žà¸¢à¸²à¸šà¸²à¸¥, à¸à¸²à¸£à¸¨à¸¶à¸à¸©à¸²,
à¸‚à¸­à¸‡à¹ƒà¸Šà¹‰à¹ƒà¸™à¸šà¹‰à¸²à¸™, à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¸­à¸·à¹ˆà¸™ à¹†.
Income: à¹€à¸‡à¸´à¸™à¹€à¸”à¸·à¸­à¸™, à¸˜à¸¸à¸£à¸à¸´à¸ˆà¸ªà¹ˆà¸§à¸™à¸•à¸±à¸§, à¸à¸²à¸£à¸¥à¸‡à¸—à¸¸à¸™, à¸£à¸²à¸¢à¸£à¸±à¸šà¸­à¸·à¹ˆà¸™ à¹†.
Classification rules:
- Buying, paying, spending, fees, bills, food, or shopping are expenses, even when a bank account is named.
- Use transfer only when the user explicitly moves money between two of their own accounts.
- A transfer must include two distinct account names: account_name is the source and to_account_name is the destination.
- For a bank slip, extract the visible sender (à¸ˆà¸²à¸) into sender_name and recipient (à¹„à¸›à¸¢à¸±à¸‡) into recipient_name.
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
        raise ValueError("à¸›à¸µà¹„à¸¡à¹ˆà¸–à¸¹à¸à¸•à¹‰à¸­à¸‡")
    return year


def parse_summary_command(text: str) -> tuple[str, int, int | None] | None:
    normalized = text.translate(THAI_DIGIT_TRANSLATION).lower().strip()
    if "à¸ªà¸£à¸¸à¸›" not in normalized or not any(word in normalized for word in ("à¹€à¸”à¸·à¸­à¸™", "à¸›à¸µ")):
        return None
    today = bangkok_today()
    compact = re.sub(r"[\s.]", "", normalized)

    if "à¹€à¸”à¸·à¸­à¸™" in normalized:
        if "à¹€à¸”à¸·à¸­à¸™à¸™à¸µà¹‰" in normalized:
            month = today.month
        else:
            slash_match = re.search(r"(\d{1,2})\s*/\s*(\d{2,4})", normalized)
            number_match = re.search(r"à¹€à¸”à¸·à¸­à¸™\s*(\d{1,2})", normalized)
            if slash_match:
                month = int(slash_match.group(1))
            elif number_match:
                month = int(number_match.group(1))
            else:
                month = next((value for value, aliases in THAI_MONTH_ALIASES.items() if any(alias in compact for alias in aliases)), 0)
        if not 1 <= month <= 12:
            raise ValueError("à¹€à¸”à¸·à¸­à¸™à¹„à¸¡à¹ˆà¸–à¸¹à¸à¸•à¹‰à¸­à¸‡")

        slash_match = re.search(r"(\d{1,2})\s*/\s*(\d{2,4})", normalized)
        year_match = re.search(r"(?:à¸›à¸µ|à¸ž\.?à¸¨\.?)\s*(\d{2,4})", normalized)
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

    if "à¸›à¸µà¸™à¸µà¹‰" in normalized:
        return "year", today.year, None
    year_match = re.search(r"(?:à¸›à¸µ|à¸ž\.?à¸¨\.?)\s*(\d{2,4})", normalized)
    if not year_match:
        raise ValueError("à¸›à¸µà¹„à¸¡à¹ˆà¸–à¸¹à¸à¸•à¹‰à¸­à¸‡")
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
    return f"-à¸¿{abs(amount):,.2f}" if amount < 0 else f"à¸¿{amount:,.2f}"


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
            comparison = f"à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢{'à¹€à¸žà¸´à¹ˆà¸¡' if expense_change >= 0 else 'à¸¥à¸”'}à¸¥à¸‡ {abs(expense_change):,.1f}% à¸ˆà¸²à¸à¹€à¸”à¸·à¸­à¸™à¸à¹ˆà¸­à¸™"
        elif expense:
            comparison = "à¹€à¸”à¸·à¸­à¸™à¸à¹ˆà¸­à¸™à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¸¡à¸µà¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¸ªà¸³à¸«à¸£à¸±à¸šà¹€à¸›à¸£à¸µà¸¢à¸šà¹€à¸—à¸µà¸¢à¸š"
        else:
            comparison = "à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¸¡à¸µà¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¹ƒà¸™à¹€à¸”à¸·à¸­à¸™à¸™à¸µà¹‰à¹à¸¥à¸°à¹€à¸”à¸·à¸­à¸™à¸à¹ˆà¸­à¸™"
        category_lines = _top_expense_categories(db, user_id, start, end)
        top_text = "\n".join(
            f"{index}. {name} {_money(amount)}" for index, (name, amount) in enumerate(category_lines, 1)
        ) or "à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¸¡à¸µà¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢"
        heading = f"ðŸ“Š à¸ªà¸£à¸¸à¸›à¸à¸²à¸£à¹€à¸‡à¸´à¸™à¹€à¸”à¸·à¸­à¸™{THAI_MONTHS[month - 1]} {year + 543}"
        body = (
            f"{heading}\n\n"
            f"ðŸŸ¢ à¸£à¸²à¸¢à¸£à¸±à¸š: {_money(income)}\n"
            f"ðŸ”´ à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢: {_money(expense)}\n"
            f"ðŸ’° à¸ªà¸¸à¸—à¸˜à¸´: {'+' if net > 0 else ''}{_money(net)}\n\n"
            f"à¸ˆà¸³à¸™à¸§à¸™à¸£à¸²à¸¢à¸à¸²à¸£\n"
            f"â€¢ à¸£à¸²à¸¢à¸£à¸±à¸š {totals['income_count']} à¸£à¸²à¸¢à¸à¸²à¸£\n"
            f"â€¢ à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢ {totals['expense_count']} à¸£à¸²à¸¢à¸à¸²à¸£\n\n"
            f"à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¸ªà¸¹à¸‡à¸ªà¸¸à¸”\n{top_text}\n\n"
            f"à¹€à¸—à¸µà¸¢à¸šà¹€à¸”à¸·à¸­à¸™à¸à¹ˆà¸­à¸™\nâ€¢ {comparison}"
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
        highest_text = THAI_MONTHS[int(highest_month[0]) - 1] if highest_month else "à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¸¡à¸µà¸‚à¹‰à¸­à¸¡à¸¹à¸¥"
        top_categories = _top_expense_categories(db, user_id, start, end, limit=1)
        top_category = top_categories[0][0] if top_categories else "à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¸¡à¸µà¸‚à¹‰à¸­à¸¡à¸¹à¸¥"
        body = (
            f"ðŸ“ˆ à¸ªà¸£à¸¸à¸›à¸à¸²à¸£à¹€à¸‡à¸´à¸™à¸›à¸µ {year + 543}\n\n"
            f"ðŸŸ¢ à¸£à¸²à¸¢à¸£à¸±à¸šà¸£à¸§à¸¡: {_money(income)}\n"
            f"ðŸ”´ à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¸£à¸§à¸¡: {_money(expense)}\n"
            f"ðŸ’° à¸ªà¸¸à¸—à¸˜à¸´à¸—à¸±à¹‰à¸‡à¸›à¸µ: {'+' if net > 0 else ''}{_money(net)}\n\n"
            f"â€¢ à¸£à¸²à¸¢à¸£à¸±à¸šà¹€à¸‰à¸¥à¸µà¹ˆà¸¢à¸•à¹ˆà¸­à¹€à¸”à¸·à¸­à¸™ {_money(income / divisor)}\n"
            f"â€¢ à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¹€à¸‰à¸¥à¸µà¹ˆà¸¢à¸•à¹ˆà¸­à¹€à¸”à¸·à¸­à¸™ {_money(expense / divisor)}\n"
            f"â€¢ à¹€à¸”à¸·à¸­à¸™à¸—à¸µà¹ˆà¹ƒà¸Šà¹‰à¸ˆà¹ˆà¸²à¸¢à¸ªà¸¹à¸‡à¸ªà¸¸à¸”: {highest_text}\n"
            f"â€¢ à¸«à¸¡à¸§à¸”à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¸ªà¸¹à¸‡à¸ªà¸¸à¸”: {top_category}\n"
            f"â€¢ à¸­à¸±à¸•à¸£à¸²à¸à¸²à¸£à¸­à¸­à¸¡: {savings_rate:,.1f}%\n\n"
            f"à¸ˆà¸³à¸™à¸§à¸™à¸£à¸²à¸¢à¸à¸²à¸£: à¸£à¸²à¸¢à¸£à¸±à¸š {totals['income_count']} Â· à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢ {totals['expense_count']}"
        )
    now = bangkok_now()
    return f"{body}\n\nà¸‚à¹‰à¸­à¸¡à¸¹à¸¥ à¸“ {now.day} {THAI_MONTHS[now.month - 1]} {now.year + 543} à¹€à¸§à¸¥à¸² {now:%H:%M} à¸™."


async def handle_summary_command(db: Session, reply_token: str, user: User, text: str) -> bool:
    if "à¸ªà¸£à¸¸à¸›" not in text or not any(word in text for word in ("à¹€à¸”à¸·à¸­à¸™", "à¸›à¸µ")):
        return False
    try:
        request = parse_summary_command(text)
    except ValueError:
        await reply_text(
            reply_token,
            "à¸£à¸¹à¸›à¹à¸šà¸šà¸„à¸³à¸ªà¸±à¹ˆà¸‡à¹„à¸¡à¹ˆà¸–à¸¹à¸à¸•à¹‰à¸­à¸‡\nà¸•à¸±à¸§à¸­à¸¢à¹ˆà¸²à¸‡: à¸‚à¸­à¸ªà¸£à¸¸à¸›à¹€à¸”à¸·à¸­à¸™à¸™à¸µà¹‰, à¸‚à¸­à¸ªà¸£à¸¸à¸›à¹€à¸”à¸·à¸­à¸™à¸à¸£à¸à¸Žà¸²à¸„à¸¡ 2569 à¸«à¸£à¸·à¸­ à¸‚à¸­à¸ªà¸£à¸¸à¸›à¸›à¸µà¸™à¸µà¹‰",
        )
        return True
    if request is None:
        return False
    period, year, month = request
    await reply_text(reply_token, build_financial_summary(db, user.id, period, year, month))
    return True


def _summary_flex(title: str, color: str, rows: list[tuple[str, str]], note: str) -> dict:
    return {
        "type": "flex",
        "altText": f"{title}: " + " Â· ".join(f"{label} {value}" for label, value in rows),
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box", "layout": "vertical", "backgroundColor": color,
                "paddingAll": "20px",
                "contents": [
                    {"type": "text", "text": "ACCOUNTING", "size": "xs",
                     "color": "#FFFFFFCC", "weight": "bold"},
                    {"type": "text", "text": title, "size": "xl", "color": "#FFFFFF",
                     "weight": "bold", "margin": "sm"},
                ],
            },
            "body": {
                "type": "box", "layout": "vertical", "spacing": "md",
                "contents": [
                    *[
                        {"type": "box", "layout": "horizontal", "contents": [
                            {"type": "text", "text": label, "size": "sm",
                             "color": "#667085", "flex": 3},
                            {"type": "text", "text": value, "size": "sm",
                             "weight": "bold", "align": "end", "flex": 3},
                        ]} for label, value in rows
                    ],
                    {"type": "separator", "margin": "md"},
                    {"type": "text", "text": note, "size": "xs", "color": "#98A2B3",
                     "wrap": True, "margin": "md"},
                ],
            },
            "footer": {
                "type": "box", "layout": "vertical",
                "contents": [{"type": "button", "style": "primary", "color": color,
                              "action": {"type": "uri", "label": "à¹€à¸›à¸´à¸” Web App",
                                         "uri": f"{settings.public_base_url}/dashboard"}}],
            },
        },
    }


async def handle_accounting_menu_command(
    db: Session, reply_token: str, user: User, text: str
) -> bool:
    normalized = re.sub(r"\s+", "", text.lower())
    now = bangkok_now()
    period_note = f"à¸‚à¹‰à¸­à¸¡à¸¹à¸¥à¹€à¸”à¸·à¸­à¸™{THAI_MONTHS[now.month - 1]} {now.year + 543} à¸“ {now:%H:%M} à¸™."

    if normalized == "à¹€à¸‡à¸´à¸™à¸­à¸¢à¸¹à¹ˆà¸—à¸µà¹ˆà¹„à¸«à¸™":
        accounts = db.query(Account).filter(Account.user_id == user.id).order_by(
            Account.balance.desc(), Account.name
        ).all()
        rows = [(account.name, _money(account.balance)) for account in accounts[:8]]
        if not rows:
            rows = [("à¸¢à¸­à¸”à¸„à¸‡à¹€à¸«à¸¥à¸·à¸­", "à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¸¡à¸µà¸šà¸±à¸à¸Šà¸µ")]
        total = sum((Decimal(account.balance or 0) for account in accounts), Decimal("0"))
        rows.append(("à¸£à¸§à¸¡à¸—à¸¸à¸à¸šà¸±à¸à¸Šà¸µ", _money(total)))
        await reply_messages(
            reply_token,
            [_summary_flex("à¹€à¸‡à¸´à¸™à¸­à¸¢à¸¹à¹ˆà¸—à¸µà¹ˆà¹„à¸«à¸™", "#3157F6", rows, "à¸¢à¸­à¸”à¸•à¸²à¸¡à¸šà¸±à¸à¸Šà¸µà¸—à¸µà¹ˆà¸šà¸±à¸™à¸—à¸¶à¸à¹„à¸§à¹‰à¹ƒà¸™à¸£à¸°à¸šà¸š")],
        )
        return True

    command_types = {
        "à¸ªà¸£à¸¸à¸›à¸£à¸²à¸¢à¸£à¸±à¸šà¹€à¸”à¸·à¸­à¸™à¸™à¸µà¹‰": ("income", "à¸ªà¸£à¸¸à¸›à¸£à¸²à¸¢à¸£à¸±à¸š", "#13A89E"),
        "à¸ªà¸£à¸¸à¸›à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¹€à¸”à¸·à¸­à¸™à¸™à¸µà¹‰": ("expense", "à¸ªà¸£à¸¸à¸›à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢", "#FF7A59"),
        "à¸ªà¸£à¸¸à¸›à¸à¸³à¹„à¸£à¹€à¸”à¸·à¸­à¸™à¸™à¸µà¹‰": ("profit", "à¸ªà¸£à¸¸à¸›à¸à¸³à¹„à¸£", "#7B61FF"),
    }
    command = command_types.get(normalized)
    if command:
        kind, title, color = command
        start, end = _month_bounds(now.year, now.month)
        totals = _period_totals(db, user.id, start, end)
        income = Decimal(totals["income"])
        expense = Decimal(totals["expense"])
        if kind == "income":
            rows = [("à¸£à¸²à¸¢à¸£à¸±à¸šà¸£à¸§à¸¡", _money(income)),
                    ("à¸ˆà¸³à¸™à¸§à¸™à¸£à¸²à¸¢à¸à¸²à¸£", f"{totals['income_count']} à¸£à¸²à¸¢à¸à¸²à¸£")]
        elif kind == "expense":
            top = _top_expense_categories(db, user.id, start, end, limit=1)
            rows = [("à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¸£à¸§à¸¡", _money(expense)),
                    ("à¸ˆà¸³à¸™à¸§à¸™à¸£à¸²à¸¢à¸à¸²à¸£", f"{totals['expense_count']} à¸£à¸²à¸¢à¸à¸²à¸£"),
                    ("à¸«à¸¡à¸§à¸”à¸ªà¸¹à¸‡à¸ªà¸¸à¸”", top[0][0] if top else "à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¸¡à¸µà¸‚à¹‰à¸­à¸¡à¸¹à¸¥")]
        else:
            net = income - expense
            rows = [("à¸£à¸²à¸¢à¸£à¸±à¸š", _money(income)), ("à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢", _money(expense)),
                    ("à¸à¸³à¹„à¸£à¸ªà¸¸à¸—à¸˜à¸´", _money(net))]
        await reply_messages(reply_token, [_summary_flex(title, color, rows, period_note)])
        return True

    if normalized == "à¸§à¸´à¸˜à¸µà¸ªà¹ˆà¸‡à¸£à¸¹à¸›à¹ƒà¸šà¹€à¸ªà¸£à¹‡à¸ˆ":
        await reply_text(
            reply_token,
            "ðŸ“· à¸ªà¹ˆà¸‡à¸£à¸¹à¸›à¸ªà¸¥à¸´à¸›à¸«à¸£à¸·à¸­à¹ƒà¸šà¹€à¸ªà¸£à¹‡à¸ˆà¹€à¸‚à¹‰à¸²à¸«à¹‰à¸­à¸‡à¹à¸Šà¸•à¸™à¸µà¹‰à¹„à¸”à¹‰à¹€à¸¥à¸¢\n"
            "à¸£à¸°à¸šà¸šà¸ˆà¸°à¸­à¹ˆà¸²à¸™à¸¢à¸­à¸” à¸§à¸±à¸™à¸—à¸µà¹ˆ à¸«à¸¡à¸§à¸” à¹à¸¥à¸°à¸šà¸±à¸à¸Šà¸µ à¹à¸¥à¹‰à¸§à¸ªà¹ˆà¸‡à¸£à¸²à¸¢à¸à¸²à¸£à¹ƒà¸«à¹‰à¸•à¸£à¸§à¸ˆà¸ªà¸­à¸šà¸à¹ˆà¸­à¸™à¸šà¸±à¸™à¸—à¸¶à¸à¸—à¸¸à¸à¸„à¸£à¸±à¹‰à¸‡",
        )
        return True
    return False


def fleet_flex_message(db: Session, user_id: int) -> dict:
    month_start = bangkok_today().replace(day=1)
    vehicles = db.query(FleetVehicle).filter(
        FleetVehicle.user_id == user_id, FleetVehicle.is_active.is_(True)
    ).order_by(FleetVehicle.name).all()
    expense = db.query(func.coalesce(func.sum(FleetExpense.amount), 0)).filter(
        FleetExpense.user_id == user_id, FleetExpense.expense_date >= month_start
    ).scalar()
    due = db.query(FleetDocument).filter(
        FleetDocument.user_id == user_id,
        FleetDocument.expiry_date <= bangkok_today() + timedelta(days=30),
    ).count()
    vehicle_lines = [
        {
            "type": "box", "layout": "horizontal", "margin": "md",
            "contents": [
                {"type": "text", "text": vehicle.name, "size": "sm", "weight": "bold", "flex": 3},
                {"type": "text", "text": f"{vehicle.current_mileage:,} à¸à¸¡.", "size": "sm",
                 "color": "#667085", "align": "end", "flex": 2},
            ],
        } for vehicle in vehicles[:4]
    ] or [{"type": "text", "text": "à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¸¡à¸µà¸£à¸–à¹ƒà¸™à¸£à¸°à¸šà¸š", "size": "sm", "color": "#667085"}]
    web_url = settings.liff_fleet_url or f"{settings.public_base_url}/fleet"
    return {
        "type": "flex",
        "altText": f"à¸ˆà¸±à¸”à¸à¸²à¸£à¸£à¸– {len(vehicles)} à¸„à¸±à¸™ Â· à¸„à¹ˆà¸²à¹ƒà¸Šà¹‰à¸ˆà¹ˆà¸²à¸¢à¹€à¸”à¸·à¸­à¸™à¸™à¸µà¹‰ à¸¿{Decimal(expense or 0):,.2f}",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box", "layout": "vertical", "backgroundColor": "#3157F6",
                "paddingAll": "20px",
                "contents": [
                    {"type": "text", "text": "SMART FINANCE Â· FLEET", "size": "xs", "color": "#C9D5FF", "weight": "bold"},
                    {"type": "text", "text": "à¸£à¸–à¸‚à¸­à¸‡à¸‰à¸±à¸™", "size": "xl", "color": "#FFFFFF", "weight": "bold", "margin": "sm"},
                ],
            },
            "body": {
                "type": "box", "layout": "vertical", "spacing": "sm",
                "contents": [
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "à¸„à¹ˆà¸²à¹ƒà¸Šà¹‰à¸ˆà¹ˆà¸²à¸¢à¹€à¸”à¸·à¸­à¸™à¸™à¸µà¹‰", "size": "sm", "color": "#667085"},
                        {"type": "text", "text": f"à¸¿{Decimal(expense or 0):,.2f}", "weight": "bold", "align": "end"},
                    ]},
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "à¹€à¸­à¸à¸ªà¸²à¸£à¹ƒà¸à¸¥à¹‰à¸«à¸¡à¸”à¸­à¸²à¸¢à¸¸", "size": "sm", "color": "#667085"},
                        {"type": "text", "text": f"{due} à¸£à¸²à¸¢à¸à¸²à¸£", "weight": "bold", "align": "end", "color": "#E94C67" if due else "#12A594"},
                    ]},
                    {"type": "separator", "margin": "lg"},
                    *vehicle_lines,
                ],
            },
            "footer": {
                "type": "box", "layout": "vertical",
                "contents": [{"type": "button", "style": "primary", "color": "#FF7A59",
                              "action": {"type": "uri", "label": "à¹€à¸›à¸´à¸”à¸£à¸°à¸šà¸šà¸ˆà¸±à¸”à¸à¸²à¸£à¸£à¸–", "uri": web_url}}],
            },
        },
    }


async def handle_fleet_command(db: Session, reply_token: str, user: User, text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.lower())
    if normalized not in {"à¸ˆà¸±à¸”à¸à¸²à¸£à¸£à¸–", "à¸£à¸–à¸‚à¸­à¸‡à¸‰à¸±à¸™", "fleet", "à¸ªà¸£à¸¸à¸›à¸£à¸–"}:
        return False
    await reply_messages(reply_token, [fleet_flex_message(db, user.id)])
    return True


async def download_line_content(message_id: str) -> bytes:
    response = await line_api(f"/v2/bot/message/{message_id}/content")
    if len(response.content) > settings.max_upload_bytes:
        raise ValueError("à¸£à¸¹à¸›à¸ à¸²à¸žà¸¡à¸µà¸‚à¸™à¸²à¸”à¹ƒà¸«à¸à¹ˆà¹€à¸à¸´à¸™à¸à¸³à¸«à¸™à¸”")
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
        parts.append({
            "text": (
                f"Today in Asia/Bangkok is {bangkok_today().isoformat()}. "
                "Resolve Thai relative dates such as à¸§à¸±à¸™à¸™à¸µà¹‰ and à¹€à¸¡à¸·à¹ˆà¸­à¸§à¸²à¸™ from this date. "
                "Thai short years such as 69 mean Buddhist year 2569 (Gregorian 2026)."
            )
        })
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
    parsed = json.loads(raw)
    # Backward compatible with an older model response while callers transition
    # to the multi-transaction array contract.
    return parsed if isinstance(parsed, list) else [parsed]


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
    type_label = {"income": "à¸£à¸²à¸¢à¸£à¸±à¸š", "expense": "à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢", "transfer": "à¹‚à¸­à¸™à¹€à¸‡à¸´à¸™"}[payload["type"]]
    account_text = payload.get("account_name") or "à¹ƒà¸«à¹‰à¸£à¸°à¸šà¸šà¹€à¸¥à¸·à¸­à¸"
    if payload["type"] == "transfer":
        account_text = f"{account_text} â†’ {payload.get('to_account_name') or 'à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¸£à¸°à¸šà¸¸à¸›à¸¥à¸²à¸¢à¸—à¸²à¸‡'}"
    counterparty_text = ""
    if payload["type"] == "income" and payload.get("sender_name"):
        counterparty_text = f"\nà¸œà¸¹à¹‰à¹‚à¸­à¸™: {payload['sender_name']}"
    elif payload["type"] == "expense" and payload.get("recipient_name"):
        counterparty_text = f"\nà¸œà¸¹à¹‰à¸£à¸±à¸š: {payload['recipient_name']}"
    details = (
        f"{type_label} à¸¿{Decimal(payload['amount']):,.2f}\n"
        f"à¸«à¸¡à¸§à¸”: {payload.get('category') or '-'}\n"
        f"à¸šà¸±à¸à¸Šà¸µ: {account_text}{counterparty_text}\n"
        f"à¸§à¸±à¸™à¸—à¸µà¹ˆ: {payload['transaction_date']}"
    )
    return {
        "type": "template",
        "altText": f"à¸à¸£à¸¸à¸“à¸²à¸•à¸£à¸§à¸ˆà¸ªà¸­à¸šà¸£à¸²à¸¢à¸à¸²à¸£ #{pending.id}",
        "template": {
            "type": "buttons",
            "text": details[:160],
            "actions": [
                {"type": "postback", "label": "à¸¢à¸·à¸™à¸¢à¸±à¸™", "data": f"pf_action=confirm&pending_id={pending.id}", "displayText": f"à¸¢à¸·à¸™à¸¢à¸±à¸™à¸£à¸²à¸¢à¸à¸²à¸£ #{pending.id}"},
                {"type": "postback", "label": "à¹à¸à¹‰à¸¢à¸­à¸”à¹€à¸‡à¸´à¸™", "data": f"pf_action=edit&pending_id={pending.id}", "displayText": f"à¹à¸à¹‰à¹„à¸‚à¸£à¸²à¸¢à¸à¸²à¸£ #{pending.id}"},
                {"type": "postback", "label": "à¸¢à¸à¹€à¸¥à¸´à¸", "data": f"pf_action=cancel&pending_id={pending.id}", "displayText": f"à¸¢à¸à¹€à¸¥à¸´à¸à¸£à¸²à¸¢à¸à¸²à¸£ #{pending.id}"},
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
        r"^(?:à¸™à¸²à¸¢|à¸™à¸²à¸‡à¸ªà¸²à¸§|à¸™à¸²à¸‡|à¸™\.?\s*à¸ª\.?|mr\.?|mrs\.?|miss)\s*",
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
    return f"{original} Â· {addition}"[:1000] if original else addition[:1000]


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
                "à¸•à¸£à¸§à¸ˆà¸žà¸šà¸§à¹ˆà¸²à¹€à¸›à¹‡à¸™à¸£à¸²à¸¢à¸£à¸±à¸š à¹à¸•à¹ˆà¸¢à¸±à¸‡à¸£à¸°à¸šà¸¸à¸šà¸±à¸à¸Šà¸µà¸£à¸±à¸šà¹€à¸‡à¸´à¸™à¹„à¸¡à¹ˆà¹„à¸”à¹‰ "
                "à¸à¸£à¸¸à¸“à¸²à¸žà¸´à¸¡à¸žà¹Œà¸Šà¸·à¹ˆà¸­à¸šà¸±à¸à¸Šà¸µà¸£à¸±à¸šà¹€à¸‡à¸´à¸™à¹ƒà¸«à¹‰à¸•à¸£à¸‡à¸à¸±à¸šà¸«à¸™à¹‰à¸²à¹€à¸§à¹‡à¸š"
            )
        result["type"] = "income"
        result["category"] = "à¸£à¸²à¸¢à¸£à¸±à¸šà¸­à¸·à¹ˆà¸™ à¹†"
        result["account_name"] = receiving_account.name
        result["to_account_name"] = None
        result["note"] = _append_counterparty(
            result.get("note"),
            "à¸œà¸¹à¹‰à¹‚à¸­à¸™",
            result.get("sender_name"),
        )
        return result, "â„¹ï¸ à¸Šà¸·à¹ˆà¸­à¸œà¸¹à¹‰à¸£à¸±à¸šà¸•à¸£à¸‡à¸à¸±à¸šà¸Šà¸·à¹ˆà¸­à¹€à¸ˆà¹‰à¸²à¸‚à¸­à¸‡à¸šà¸±à¸à¸Šà¸µ à¸£à¸°à¸šà¸šà¸ˆà¸¶à¸‡à¸ˆà¸±à¸”à¸ªà¸¥à¸´à¸›à¸™à¸µà¹‰à¹€à¸›à¹‡à¸™à¸£à¸²à¸¢à¸£à¸±à¸š"

    # The registered owner paying another person or merchant is an expense.
    if owner_is_sender and not owner_is_recipient:
        paying_account = source or (accounts[0] if len(accounts) == 1 else None)
        if paying_account is None:
            raise ValueError(
                "à¸•à¸£à¸§à¸ˆà¸žà¸šà¸§à¹ˆà¸²à¹€à¸›à¹‡à¸™à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢ à¹à¸•à¹ˆà¸¢à¸±à¸‡à¸£à¸°à¸šà¸¸à¸šà¸±à¸à¸Šà¸µà¸—à¸µà¹ˆà¸ˆà¹ˆà¸²à¸¢à¹„à¸¡à¹ˆà¹„à¸”à¹‰ "
                "à¸à¸£à¸¸à¸“à¸²à¸žà¸´à¸¡à¸žà¹Œà¸Šà¸·à¹ˆà¸­à¸šà¸±à¸à¸Šà¸µà¹ƒà¸«à¹‰à¸•à¸£à¸‡à¸à¸±à¸šà¸«à¸™à¹‰à¸²à¹€à¸§à¹‡à¸š"
            )
        result["type"] = "expense"
        result["category"] = "à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¸­à¸·à¹ˆà¸™ à¹†"
        result["account_name"] = paying_account.name
        result["to_account_name"] = None
        result["note"] = _append_counterparty(
            result.get("note"),
            "à¸œà¸¹à¹‰à¸£à¸±à¸š",
            result.get("recipient_name"),
        )
        return result, "â„¹ï¸ à¸Šà¸·à¹ˆà¸­à¸œà¸¹à¹‰à¸ªà¹ˆà¸‡à¸•à¸£à¸‡à¸à¸±à¸šà¸Šà¸·à¹ˆà¸­à¹€à¸ˆà¹‰à¸²à¸‚à¸­à¸‡à¸šà¸±à¸à¸Šà¸µ à¸£à¸°à¸šà¸šà¸ˆà¸¶à¸‡à¸ˆà¸±à¸”à¸ªà¸¥à¸´à¸›à¸™à¸µà¹‰à¹€à¸›à¹‡à¸™à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢"

    # Two recognised accounts owned by the same user are an internal transfer.
    if result["type"] == "transfer" and source and destination and source.id != destination.id:
        result["account_name"] = source.name
        result["to_account_name"] = destination.name
        return result, None

    if result["type"] == "transfer":
        if not result.get("account_name") or not result.get("to_account_name"):
            raise ValueError(
                "à¸£à¸²à¸¢à¸à¸²à¸£à¹‚à¸­à¸™à¹€à¸‡à¸´à¸™à¸•à¹‰à¸­à¸‡à¸£à¸°à¸šà¸¸à¸—à¸±à¹‰à¸‡à¸šà¸±à¸à¸Šà¸µà¸•à¹‰à¸™à¸—à¸²à¸‡à¹à¸¥à¸°à¸›à¸¥à¸²à¸¢à¸—à¸²à¸‡\n"
                "à¸•à¸±à¸§à¸­à¸¢à¹ˆà¸²à¸‡: à¹‚à¸­à¸™ 100 à¸ˆà¸²à¸ Krungthai à¹„à¸› à¹€à¸‡à¸´à¸™à¸ªà¸”"
            )
        raise ValueError(
            "à¸¢à¸±à¸‡à¸£à¸°à¸šà¸¸à¹„à¸¡à¹ˆà¹„à¸”à¹‰à¸§à¹ˆà¸²à¸ªà¸¥à¸´à¸›à¸™à¸µà¹‰à¹€à¸›à¹‡à¸™à¹€à¸‡à¸´à¸™à¹€à¸‚à¹‰à¸² à¹€à¸‡à¸´à¸™à¸­à¸­à¸ à¸«à¸£à¸·à¸­à¹‚à¸­à¸™à¸£à¸°à¸«à¸§à¹ˆà¸²à¸‡à¸šà¸±à¸à¸Šà¸µ "
            "à¸à¸£à¸¸à¸“à¸²à¸£à¸°à¸šà¸¸à¸Šà¸·à¹ˆà¸­à¸œà¸¹à¹‰à¸ªà¹ˆà¸‡ à¸œà¸¹à¹‰à¸£à¸±à¸š à¸«à¸£à¸·à¸­à¸Šà¸·à¹ˆà¸­à¸šà¸±à¸à¸Šà¸µà¹ƒà¸«à¹‰à¸Šà¸±à¸”à¹€à¸ˆà¸™"
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
        account = Account(user_id=user_id, name="à¹€à¸‡à¸´à¸™à¸ªà¸”", type="cash", balance=Decimal("0.00"))
        db.add(account)
        db.flush()
        return account
    raise HTTPException(
        status_code=422,
        detail="à¹„à¸¡à¹ˆà¸ªà¸²à¸¡à¸²à¸£à¸–à¸£à¸°à¸šà¸¸à¸šà¸±à¸à¸Šà¸µà¹„à¸”à¹‰ à¸à¸£à¸¸à¸“à¸²à¸£à¸°à¸šà¸¸à¸Šà¸·à¹ˆà¸­à¸šà¸±à¸à¸Šà¸µà¹ƒà¸™à¸‚à¹‰à¸­à¸„à¸§à¸²à¸¡à¹ƒà¸«à¹‰à¸•à¸£à¸‡à¸à¸±à¸šà¸«à¸™à¹‰à¸²à¹€à¸§à¹‡à¸š",
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
    fallback = "à¸£à¸²à¸¢à¸£à¸±à¸šà¸­à¸·à¹ˆà¸™ à¹†" if tx_type == "income" else "à¸£à¸²à¸¢à¸ˆà¹ˆà¸²à¸¢à¸­à¸·à¹ˆà¸™ à¹†"
    return db.query(Category).filter(Category.user_id == user_id, Category.name == fallback, Category.type == tx_type).first()


def confirm_pending(db: Session, pending: PendingLineTransaction) -> str:
    if pending.status == "confirmed":
        tx = db.query(Transaction).filter(Transaction.external_id == f"pending:{pending.id}", Transaction.source == "line").first()
        return f"âœ… à¸£à¸²à¸¢à¸à¸²à¸£ #{pending.id} à¸–à¸¹à¸à¸šà¸±à¸™à¸—à¸¶à¸à¹à¸¥à¹‰à¸§" + (f" (à¸˜à¸¸à¸£à¸à¸£à¸£à¸¡ #{tx.id})" if tx else "")
    if pending.status != "pending":
        return f"à¸£à¸²à¸¢à¸à¸²à¸£ #{pending.id} à¹„à¸¡à¹ˆà¸­à¸¢à¸¹à¹ˆà¹ƒà¸™à¸ªà¸–à¸²à¸™à¸°à¸—à¸µà¹ˆà¸¢à¸·à¸™à¸¢à¸±à¸™à¹„à¸”à¹‰"
    if pending.expires_at <= utcnow():
        pending.status = "expired"
        pending.resolved_at = utcnow()
        db.commit()
        delete_receipt(pending.receipt_path)
        return f"âŒ› à¸£à¸²à¸¢à¸à¸²à¸£ #{pending.id} à¸«à¸¡à¸”à¸­à¸²à¸¢à¸¸à¹à¸¥à¹‰à¸§ à¸à¸£à¸¸à¸“à¸²à¸ªà¹ˆà¸‡à¸‚à¹‰à¸­à¸¡à¸¹à¸¥à¹ƒà¸«à¸¡à¹ˆ"

    data = pending.payload
    source = find_account(db, pending.user_id, data.get("account_name"))
    destination = None
    if data["type"] == "transfer":
        if not data.get("to_account_name"):
            raise HTTPException(
                status_code=422,
                detail="à¸£à¸²à¸¢à¸à¸²à¸£à¹‚à¸­à¸™à¹€à¸‡à¸´à¸™à¸•à¹‰à¸­à¸‡à¸£à¸°à¸šà¸¸à¸šà¸±à¸à¸Šà¸µà¸›à¸¥à¸²à¸¢à¸—à¸²à¸‡ à¹€à¸Šà¹ˆà¸™ â€œà¹‚à¸­à¸™ 100 à¸ˆà¸²à¸ Krungthai à¹„à¸› à¹€à¸‡à¸´à¸™à¸ªà¸”â€",
            )
        destination = find_account(db, pending.user_id, data.get("to_account_name"), destination=True)
        if destination.id == source.id:
            raise HTTPException(status_code=422, detail="à¸šà¸±à¸à¸Šà¸µà¸•à¹‰à¸™à¸—à¸²à¸‡à¹à¸¥à¸°à¸›à¸¥à¸²à¸¢à¸—à¸²à¸‡à¸•à¹‰à¸­à¸‡à¹„à¸¡à¹ˆà¸‹à¹‰à¸³à¸à¸±à¸™")
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
    return f"âœ… à¸šà¸±à¸™à¸—à¸¶à¸à¸ªà¸³à¹€à¸£à¹‡à¸ˆ #{tx.id}\n{data['type']} à¸¿{Decimal(data['amount']):,.2f}\nà¸šà¸±à¸à¸Šà¸µ {source.name}"


async def handle_pairing(db: Session, reply_token: str, line_id: str, text: str) -> bool:
    if not text.lower().startswith("à¸œà¸¹à¸à¸šà¸±à¸à¸Šà¸µ "):
        return False
    code = text.split(maxsplit=1)[1].strip().upper()
    pair = db.query(LinePairCode).filter(
        LinePairCode.code == code,
        LinePairCode.used_at.is_(None),
        LinePairCode.expires_at > utcnow(),
    ).with_for_update().first()
    if not pair:
        await reply_text(reply_token, "âŒ à¹„à¸¡à¹ˆà¸žà¸šà¸£à¸«à¸±à¸ªà¸™à¸µà¹‰à¸«à¸£à¸·à¸­à¸£à¸«à¸±à¸ªà¸«à¸¡à¸”à¸­à¸²à¸¢à¸¸à¹à¸¥à¹‰à¸§")
        return True
    existing = db.query(User).filter(User.line_user_id == line_id, User.id != pair.user_id).first()
    if existing:
        await reply_text(reply_token, "âŒ LINE à¸™à¸µà¹‰à¹€à¸Šà¸·à¹ˆà¸­à¸¡à¸à¸±à¸šà¸šà¸±à¸à¸Šà¸µà¸­à¸·à¹ˆà¸™à¸­à¸¢à¸¹à¹ˆ à¸à¸£à¸¸à¸“à¸²à¸¢à¸à¹€à¸¥à¸´à¸à¸à¸²à¸£à¹€à¸Šà¸·à¹ˆà¸­à¸¡à¸•à¹ˆà¸­à¸ˆà¸²à¸à¸šà¸±à¸à¸Šà¸µà¹€à¸”à¸´à¸¡à¸à¹ˆà¸­à¸™")
        return True
    user = db.get(User, pair.user_id)
    user.line_user_id = line_id
    pair.used_at = utcnow()
    db.commit()
    await reply_text(reply_token, f"âœ… à¸œà¸¹à¸à¸šà¸±à¸à¸Šà¸µà¸ªà¸³à¹€à¸£à¹‡à¸ˆ\nà¸œà¸¹à¹‰à¹ƒà¸Šà¹‰à¸‡à¸²à¸™: {user.full_name}\nà¸«à¸¥à¸±à¸‡à¸ˆà¸²à¸à¸™à¸µà¹‰à¸£à¸°à¸šà¸šà¸ˆà¸°à¹ƒà¸«à¹‰à¸•à¸£à¸§à¸ˆà¸ªà¸­à¸šà¸à¹ˆà¸­à¸™à¸šà¸±à¸™à¸—à¸¶à¸à¸—à¸¸à¸à¸„à¸£à¸±à¹‰à¸‡")
    return True


async def handle_postback(db: Session, event: dict, line_id: str, reply_token: str) -> None:
    params = parse_qs(event.get("postback", {}).get("data", ""))
    action = (params.get("pf_action") or [""])[0]
    try:
        pending_id = int((params.get("pending_id") or ["0"])[0])
    except ValueError:
        await reply_text(reply_token, "à¸„à¸³à¸ªà¸±à¹ˆà¸‡à¹„à¸¡à¹ˆà¸–à¸¹à¸à¸•à¹‰à¸­à¸‡")
        return
    pending = db.query(PendingLineTransaction).filter(
        PendingLineTransaction.id == pending_id,
        PendingLineTransaction.line_user_id == line_id,
    ).with_for_update().first()
    if not pending:
        await reply_text(reply_token, "à¹„à¸¡à¹ˆà¸žà¸šà¸£à¸²à¸¢à¸à¸²à¸£à¸—à¸µà¹ˆà¸£à¸­à¸•à¸£à¸§à¸ˆà¸ªà¸­à¸š")
        return
    if action == "confirm":
        try:
            await reply_text(reply_token, confirm_pending(db, pending))
        except HTTPException as exc:
            db.rollback()
            await reply_text(reply_token, f"âŒ à¸¢à¸±à¸‡à¸šà¸±à¸™à¸—à¸¶à¸à¹„à¸¡à¹ˆà¹„à¸”à¹‰: {exc.detail}")
    elif action == "cancel":
        if pending.status == "pending":
            pending.status = "cancelled"
            pending.resolved_at = utcnow()
            db.commit()
            delete_receipt(pending.receipt_path)
        await reply_text(reply_token, f"à¸¢à¸à¹€à¸¥à¸´à¸à¸£à¸²à¸¢à¸à¸²à¸£ #{pending.id} à¹à¸¥à¹‰à¸§")
    elif action == "edit":
        await reply_text(reply_token, f"à¸žà¸´à¸¡à¸žà¹Œ â€œà¹à¸à¹‰à¹„à¸‚ {pending.id} à¸ˆà¸³à¸™à¸§à¸™à¹€à¸‡à¸´à¸™à¹ƒà¸«à¸¡à¹ˆâ€\nà¸•à¸±à¸§à¸­à¸¢à¹ˆà¸²à¸‡: à¹à¸à¹‰à¹„à¸‚ {pending.id} 250")
    else:
        await reply_text(reply_token, "à¸„à¸³à¸ªà¸±à¹ˆà¸‡à¹„à¸¡à¹ˆà¸–à¸¹à¸à¸•à¹‰à¸­à¸‡")


async def handle_edit_command(db: Session, reply_token: str, line_id: str, text: str) -> bool:
    match = re.fullmatch(r"à¹à¸à¹‰à¹„à¸‚\s+#?(\d+)\s+([\d,]+(?:\.\d{1,2})?)", text.strip())
    if not match:
        return False
    pending_id = int(match.group(1))
    try:
        amount = Decimal(match.group(2).replace(",", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        await reply_text(reply_token, "à¸ˆà¸³à¸™à¸§à¸™à¹€à¸‡à¸´à¸™à¹„à¸¡à¹ˆà¸–à¸¹à¸à¸•à¹‰à¸­à¸‡")
        return True
    if amount <= 0:
        await reply_text(reply_token, "à¸ˆà¸³à¸™à¸§à¸™à¹€à¸‡à¸´à¸™à¸•à¹‰à¸­à¸‡à¸¡à¸²à¸à¸à¸§à¹ˆà¸² 0")
        return True
    pending = db.query(PendingLineTransaction).filter(
        PendingLineTransaction.id == pending_id,
        PendingLineTransaction.line_user_id == line_id,
        PendingLineTransaction.status == "pending",
        PendingLineTransaction.expires_at > utcnow(),
    ).with_for_update().first()
    if not pending:
        await reply_text(reply_token, "à¹„à¸¡à¹ˆà¸žà¸šà¸£à¸²à¸¢à¸à¸²à¸£à¸—à¸µà¹ˆà¹à¸à¹‰à¹„à¸‚à¹„à¸”à¹‰ à¸«à¸£à¸·à¸­à¸£à¸²à¸¢à¸à¸²à¸£à¸«à¸¡à¸”à¸­à¸²à¸¢à¸¸à¹à¸¥à¹‰à¸§")
        return True
    updated = dict(pending.payload)
    updated["amount"] = str(amount)
    pending.payload = updated
    db.commit()
    await reply_messages(reply_token, [{"type": "text", "text": "à¹à¸à¹‰à¹„à¸‚à¸¢à¸­à¸”à¹€à¸‡à¸´à¸™à¹à¸¥à¹‰à¸§ à¸à¸£à¸¸à¸“à¸²à¸•à¸£à¸§à¸ˆà¸ªà¸­à¸šà¸­à¸µà¸à¸„à¸£à¸±à¹‰à¸‡"}, pending_message(pending)])
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
                    await reply_text(reply_token, "âŒ à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¹„à¸”à¹‰à¸œà¸¹à¸à¸šà¸±à¸à¸Šà¸µ\nà¸‚à¸­à¸£à¸«à¸±à¸ªà¸ˆà¸²à¸à¸«à¸™à¹‰à¸²à¹€à¸§à¹‡à¸š à¹à¸¥à¹‰à¸§à¸žà¸´à¸¡à¸žà¹Œ â€œà¸œà¸¹à¸à¸šà¸±à¸à¸Šà¸µ PF-XXXXXXXXâ€")
                    finish_event(db, key, "processed")
                    continue
                if msg_type == "text" and await handle_accounting_menu_command(
                    db, reply_token, user, text
                ):
                    finish_event(db, key, "processed")
                    continue
                if msg_type == "text" and await handle_summary_command(db, reply_token, user, text):
                    finish_event(db, key, "processed")
                    continue
                if msg_type == "text" and await handle_fleet_command(db, reply_token, user, text):
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
                    fallback_note = "à¸§à¸´à¹€à¸„à¸£à¸²à¸°à¸«à¹Œà¸ˆà¸²à¸à¸£à¸¹à¸›à¹ƒà¸šà¹€à¸ªà¸£à¹‡à¸ˆà¸«à¸£à¸·à¸­à¸ªà¸¥à¸´à¸›"
                    receipt_path = save_receipt_bytes(image_bytes, user.id)
                else:
                    await reply_text(reply_token, "à¸£à¸­à¸‡à¸£à¸±à¸šà¹€à¸‰à¸žà¸²à¸°à¸‚à¹‰à¸­à¸„à¸§à¸²à¸¡à¹à¸¥à¸°à¸£à¸¹à¸›à¹ƒà¸šà¹€à¸ªà¸£à¹‡à¸ˆ/à¸ªà¸¥à¸´à¸›")
                    finish_event(db, key, "processed")
                    continue

                raw_items = raw_analysis if isinstance(raw_analysis, list) else [raw_analysis]
                analyses = [
                    normalized
                    for item in raw_items[:4]
                    if isinstance(item, dict)
                    for normalized in [
                        normalize_analysis(
                            item,
                            fallback_note,
                            original_text=(
                                str(item.get("note") or fallback_note)
                                if msg_type == "text" and len(raw_items) > 1
                                else (text if msg_type == "text" else None)
                            ),
                        )
                    ]
                    if normalized is not None
                ]
                if not analyses:
                    delete_receipt(receipt_path)
                    await reply_text(reply_token, "à¹„à¸¡à¹ˆà¸žà¸šà¸‚à¹‰à¸­à¸¡à¸¹à¸¥à¸ˆà¸³à¸™à¸§à¸™à¹€à¸‡à¸´à¸™à¸—à¸µà¹ˆà¸Šà¸±à¸”à¹€à¸ˆà¸™ à¸ˆà¸¶à¸‡à¸¢à¸±à¸‡à¹„à¸¡à¹ˆà¸¡à¸µà¸à¸²à¸£à¸šà¸±à¸™à¸—à¸¶à¸à¸£à¸²à¸¢à¸à¸²à¸£")
                    finish_event(db, key, "processed")
                    continue
                messages = []
                if msg_type == "text" and len(analyses) == 1:
                    messages.append({
                        "type": "text",
                        "text": f"à¸‚à¹‰à¸­à¸„à¸§à¸²à¸¡à¸•à¹‰à¸™à¸‰à¸šà¸±à¸šà¸—à¸µà¹ˆà¸£à¸°à¸šà¸šà¸ˆà¸°à¸šà¸±à¸™à¸—à¸¶à¸:\n{analyses[0]['note']}",
                    })
                elif msg_type == "text":
                    messages.append({
                        "type": "text",
                        "text": f"à¸žà¸š {len(analyses)} à¸£à¸²à¸¢à¸à¸²à¸£ à¸à¸£à¸¸à¸“à¸²à¸•à¸£à¸§à¸ˆà¸ªà¸­à¸šà¹à¸¥à¸°à¸¢à¸·à¸™à¸¢à¸±à¸™à¸—à¸µà¸¥à¸°à¸£à¸²à¸¢à¸à¸²à¸£",
                    })
                for index, analysis in enumerate(analyses):
                    try:
                        analysis, classification_notice = reconcile_analyzed_accounts(
                            db,
                            user,
                            analysis,
                        )
                    except ValueError as exc:
                        delete_receipt(receipt_path)
                        await reply_text(reply_token, f"âŒ {exc}")
                        finish_event(db, key, "processed")
                        break
                    pending = await create_pending_from_event(
                        db,
                        event_id=key if len(analyses) == 1 else f"{key}:{index + 1}",
                        user=user,
                        line_id=line_id,
                        analysis=analysis,
                        receipt_path=receipt_path if index == 0 else None,
                    )
                    if classification_notice and len(messages) < 4:
                        messages.append({"type": "text", "text": classification_notice})
                    messages.append(pending_message(pending))
                else:
                    await reply_messages(reply_token, messages[:5])
                    finish_event(db, key, "processed")
                    continue
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
                        await reply_text(reply_token, "âŒ à¸£à¸°à¸šà¸šà¸›à¸£à¸°à¸¡à¸§à¸¥à¸œà¸¥à¹„à¸¡à¹ˆà¸ªà¸³à¹€à¸£à¹‡à¸ˆ à¸à¸£à¸¸à¸“à¸²à¸¥à¸­à¸‡à¹ƒà¸«à¸¡à¹ˆà¸ à¸²à¸¢à¸«à¸¥à¸±à¸‡")
                    except Exception:
                        logger.exception("Unable to send LINE error reply event_key=%s", key)
            except Exception:
                db.rollback()
                finish_event(db, key, "failed")
                logger.exception("LINE event failed event_key=%s", key)
                try:
                    await reply_text(reply_token, "âŒ à¸£à¸°à¸šà¸šà¸›à¸£à¸°à¸¡à¸§à¸¥à¸œà¸¥à¹„à¸¡à¹ˆà¸ªà¸³à¹€à¸£à¹‡à¸ˆ à¸à¸£à¸¸à¸“à¸²à¸¥à¸­à¸‡à¹ƒà¸«à¸¡à¹ˆà¸ à¸²à¸¢à¸«à¸¥à¸±à¸‡")
                except Exception:
                    logger.exception("Unable to send LINE error reply event_key=%s", key)

    return {"status": "ok"}
摘要
翻译
扩写
重写
解释说明
语法
问答
解释代码
Explain
