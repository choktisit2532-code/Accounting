import csv
import io
import secrets
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func
from sqlalchemy.orm import Session

from personal_finance.db import get_db
from personal_finance.local_time import bangkok_today
from personal_finance.models import Account, Category, LinePairCode, Transaction, User, utcnow
from personal_finance.security import client_key, current_user, rate_limiter


router = APIRouter(prefix="/api/reports", tags=["Reports"])
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
PDF_FONT = "IBMPlexThai"
PDF_FONT_BOLD = "IBMPlexThai-Bold"


def month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


@router.get("/dashboard")
def get_dashboard_summary(
    month: int | None = None,
    year: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    today = bangkok_today()
    month = month or today.month
    year = year or today.year
    if not 1 <= month <= 12 or not 2000 <= year <= 2200:
        raise HTTPException(status_code=422, detail="เดือนหรือปีไม่ถูกต้อง")
    start_of_month, end_of_month = month_bounds(year, month)

    total_balance = db.query(func.coalesce(func.sum(Account.balance), 0)).filter(Account.user_id == user.id).scalar()
    monthly_income = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
        Transaction.user_id == user.id,
        Transaction.type == "income",
        Transaction.source != "system",
        Transaction.date >= start_of_month,
        Transaction.date < end_of_month,
    ).scalar()
    monthly_expense = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
        Transaction.user_id == user.id,
        Transaction.type == "expense",
        Transaction.source != "system",
        Transaction.date >= start_of_month,
        Transaction.date < end_of_month,
    ).scalar()
    monthly_transfer = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
        Transaction.user_id == user.id,
        Transaction.type == "transfer",
        Transaction.source != "system",
        Transaction.date >= start_of_month,
        Transaction.date < end_of_month,
    ).scalar()

    categories_data = db.query(
        Transaction.type,
        Category.name,
        Category.color,
        Category.icon,
        func.sum(Transaction.amount),
    ).join(Transaction, Transaction.category_id == Category.id).filter(
        Transaction.user_id == user.id,
        Transaction.type.in_(("income", "expense")),
        Transaction.source != "system",
        Transaction.date >= start_of_month,
        Transaction.date < end_of_month,
    ).group_by(Transaction.type, Category.id, Category.name, Category.color, Category.icon).all()

    breakdowns = {"income": [], "expense": []}
    for row in categories_data:
        breakdowns[row[0]].append({
            "category_name": row[1],
            "color": row[2] or "#6B7280",
            "icon": row[3] or "fa-tag",
            "amount": float(row[4]),
        })
    for values in breakdowns.values():
        values.sort(key=lambda item: item["amount"], reverse=True)

    daily_rows = db.query(
        Transaction.date,
        func.coalesce(func.sum(Transaction.amount).filter(Transaction.type == "income"), 0),
        func.coalesce(func.sum(Transaction.amount).filter(Transaction.type == "expense"), 0),
    ).filter(
        Transaction.user_id == user.id,
        Transaction.source != "system",
        Transaction.date >= start_of_month,
        Transaction.date < end_of_month,
    ).group_by(Transaction.date).all()
    daily_by_date = {
        row[0]: {"income": float(row[1]), "expense": float(row[2])}
        for row in daily_rows
    }
    daily_cashflow = []
    for day in range(1, monthrange(year, month)[1] + 1):
        current_date = date(year, month, day)
        values = daily_by_date.get(current_date, {"income": 0.0, "expense": 0.0})
        daily_cashflow.append({
            "day": day,
            "date": current_date.isoformat(),
            "income": values["income"],
            "expense": values["expense"],
        })

    yearly_cashflow = []
    for target_month in range(1, 13):
        period_start, period_end = month_bounds(year, target_month)
        income, expense = db.query(
            func.coalesce(func.sum(Transaction.amount).filter(Transaction.type == "income"), 0),
            func.coalesce(func.sum(Transaction.amount).filter(Transaction.type == "expense"), 0),
        ).filter(
            Transaction.user_id == user.id,
            Transaction.source != "system",
            Transaction.date >= period_start,
            Transaction.date < period_end,
        ).one()
        yearly_cashflow.append({
            "month": target_month,
            "income": float(income),
            "expense": float(expense),
        })

    trend = []
    for delta in range(-5, 1):
        target_year, target_month = shift_month(year, month, delta)
        period_start, period_end = month_bounds(target_year, target_month)
        income, expense = db.query(
            func.coalesce(func.sum(Transaction.amount).filter(Transaction.type == "income"), 0),
            func.coalesce(func.sum(Transaction.amount).filter(Transaction.type == "expense"), 0),
        ).filter(
            Transaction.user_id == user.id,
            Transaction.source != "system",
            Transaction.date >= period_start,
            Transaction.date < period_end,
        ).one()
        trend.append({"month": period_start.strftime("%m/%Y"), "income": float(income), "expense": float(expense)})

    monthly_txs = db.query(Transaction).filter(
        Transaction.user_id == user.id,
        Transaction.source != "system",
        Transaction.date >= start_of_month,
        Transaction.date < end_of_month,
    ).order_by(
        Transaction.date.desc(), Transaction.created_at.desc()
    ).all()
    tx_list = []
    for tx in monthly_txs:
        tx_list.append({
            "id": tx.id,
            "type": tx.type,
            "amount": float(tx.amount),
            "category_id": tx.category_id,
            "category_name": tx.category.name if tx.category else ("โอนเงิน" if tx.type == "transfer" else "ไม่มีหมวดหมู่"),
            "category_icon": tx.category.icon if tx.category else ("fa-exchange-alt" if tx.type == "transfer" else "fa-tag"),
            "category_color": tx.category.color if tx.category else ("#3B82F6" if tx.type == "transfer" else "#6B7280"),
            "account_id": tx.account_id,
            "account_name": tx.account.name if tx.account else "-",
            "to_account_id": tx.to_account_id,
            "to_account_name": tx.to_account.name if tx.to_account else None,
            "date": tx.date.isoformat(),
            "note": tx.note,
            "has_receipt": bool(tx.receipt_path),
            "source": tx.source,
        })

    income_value = float(monthly_income)
    expense_value = float(monthly_expense)
    return {
        "period": {"month": month, "year": year},
        "net_worth": float(total_balance),
        "month_income": income_value,
        "month_expense": expense_value,
        "month_transfer": float(monthly_transfer),
        "cash_flow": income_value - expense_value,
        "savings_rate": round(((income_value - expense_value) / income_value * 100), 2) if income_value else 0,
        "category_breakdown": breakdowns["expense"],
        "category_breakdowns": breakdowns,
        "daily_cashflow": daily_cashflow,
        "yearly_cashflow": yearly_cashflow,
        "monthly_trend": trend,
        "recent_transactions": tx_list[:10],
        "monthly_transactions": tx_list,
    }


@router.post("/pairing-code")
def get_or_create_pairing_code(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rate_limiter.check(client_key(request, f"pair:{user.id}"), limit=5, window_seconds=600)
    now = utcnow()
    active_code = db.query(LinePairCode).filter(
        LinePairCode.user_id == user.id,
        LinePairCode.used_at.is_(None),
        LinePairCode.expires_at > now,
    ).first()
    if active_code:
        return {"code": active_code.code, "expires_in_seconds": int((active_code.expires_at - now).total_seconds())}

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(5):
        code = "PF-" + "".join(secrets.choice(alphabet) for _ in range(8))
        if not db.query(LinePairCode.id).filter(LinePairCode.code == code).first():
            pair = LinePairCode(user_id=user.id, code=code, expires_at=now + timedelta(minutes=10))
            db.add(pair)
            db.commit()
            return {"code": code, "expires_in_seconds": 600}
    raise HTTPException(status_code=503, detail="ไม่สามารถสร้างรหัสได้ กรุณาลองใหม่")


@router.get("/line-status")
def line_status(user: User = Depends(current_user)):
    return {"paired": bool(user.line_user_id)}


@router.delete("/line-pairing")
def unlink_line(user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.line_user_id = None
    db.commit()
    return {"message": "ยกเลิกการเชื่อมต่อ LINE แล้ว"}


def safe_csv(value: object) -> object:
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def filtered_transactions(
    db: Session,
    user_id: int,
    start_date: date | None,
    end_date: date | None,
    transaction_type: str | None = None,
    include_system: bool = True,
) -> list[Transaction]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="ช่วงวันที่ไม่ถูกต้อง")
    if transaction_type and transaction_type not in {"income", "expense", "transfer"}:
        raise HTTPException(status_code=422, detail="ประเภทรายการไม่ถูกต้อง")
    query = db.query(Transaction).filter(Transaction.user_id == user_id)
    if not include_system:
        query = query.filter(Transaction.source != "system")
    if start_date:
        query = query.filter(Transaction.date >= start_date)
    if end_date:
        query = query.filter(Transaction.date <= end_date)
    if transaction_type:
        query = query.filter(Transaction.type == transaction_type)
    return query.order_by(Transaction.date, Transaction.id).all()


def register_pdf_fonts() -> None:
    if PDF_FONT in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(PDF_FONT, FONT_DIR / "IBMPlexSansThaiLooped-Regular.ttf"))
    pdfmetrics.registerFont(TTFont(PDF_FONT_BOLD, FONT_DIR / "IBMPlexSansThaiLooped-Bold.ttf"))


def pdf_page_number(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont(PDF_FONT, 8)
    canvas.setFillColor(colors.HexColor("#667085"))
    canvas.drawRightString(landscape(A4)[0] - 14 * mm, 9 * mm, f"หน้า {document.page}")
    canvas.restoreState()


@router.get("/transactions.csv")
def export_transactions(
    start_date: date | None = None,
    end_date: date | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = filtered_transactions(db, user.id, start_date, end_date)

    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["วันที่", "ประเภท", "จำนวนเงิน", "หมวดหมู่", "บัญชีต้นทาง", "บัญชีปลายทาง", "รายละเอียด", "แหล่งข้อมูล"])
    for tx in rows:
        writer.writerow([
            tx.date.isoformat(),
            tx.type,
            f"{tx.amount:.2f}",
            safe_csv(tx.category.name if tx.category else ""),
            safe_csv(tx.account.name if tx.account else ""),
            safe_csv(tx.to_account.name if tx.to_account else ""),
            safe_csv(tx.note or ""),
            tx.source,
        ])
    filename = f"transactions-{bangkok_today().isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/transactions.pdf")
def export_transactions_pdf(
    start_date: date | None = None,
    end_date: date | None = None,
    transaction_type: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = filtered_transactions(
        db, user.id, start_date, end_date, transaction_type, include_system=False
    )
    register_pdf_fonts()

    income = sum(float(tx.amount) for tx in rows if tx.type == "income")
    expense = sum(float(tx.amount) for tx in rows if tx.type == "expense")
    date_label = "ทุกช่วงเวลา"
    if start_date or end_date:
        date_label = f"{start_date.isoformat() if start_date else 'เริ่มต้น'} ถึง {end_date.isoformat() if end_date else 'ปัจจุบัน'}"

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=15 * mm,
        title="รายงานธุรกรรมการเงิน",
        author="Smart Finance 2.0",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ThaiTitle", parent=styles["Title"], fontName=PDF_FONT_BOLD,
        fontSize=20, leading=25, textColor=colors.HexColor("#173B8E"), spaceAfter=3 * mm,
    )
    body_style = ParagraphStyle(
        "ThaiBody", parent=styles["BodyText"], fontName=PDF_FONT,
        fontSize=9, leading=13, textColor=colors.HexColor("#344054"),
    )
    cell_style = ParagraphStyle(
        "ThaiCell", parent=body_style, fontSize=8, leading=11, wordWrap="CJK",
    )
    amount_style = ParagraphStyle(
        "ThaiAmount", parent=cell_style, alignment=TA_RIGHT, fontName=PDF_FONT_BOLD,
    )
    header_style = ParagraphStyle(
        "ThaiHeader", parent=cell_style, alignment=TA_CENTER,
        fontName=PDF_FONT_BOLD, textColor=colors.white,
    )

    story = [
        Paragraph("รายงานธุรกรรมการเงิน", title_style),
        Paragraph(f"ผู้ใช้งาน: {user.full_name} &nbsp;&nbsp;|&nbsp;&nbsp; ช่วงข้อมูล: {date_label}", body_style),
        Spacer(1, 5 * mm),
    ]
    summary = Table([
        [Paragraph("รายรับรวม", header_style), Paragraph("รายจ่ายรวม", header_style), Paragraph("กระแสเงินสดสุทธิ", header_style), Paragraph("จำนวนรายการ", header_style)],
        [f"฿{income:,.2f}", f"฿{expense:,.2f}", f"฿{income - expense:,.2f}", f"{len(rows):,} รายการ"],
    ], colWidths=[47 * mm] * 4, rowHeights=[9 * mm, 12 * mm])
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3157F6")),
        ("FONTNAME", (0, 1), (-1, 1), PDF_FONT_BOLD),
        ("FONTSIZE", (0, 1), (-1, 1), 12),
        ("TEXTCOLOR", (0, 1), (0, 1), colors.HexColor("#0B8F64")),
        ("TEXTCOLOR", (1, 1), (1, 1), colors.HexColor("#D64550")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D0D5DD")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E4E7EC")),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#F8FAFC")),
    ]))
    story.extend([summary, Spacer(1, 6 * mm)])

    table_rows = [[
        Paragraph("วันที่", header_style), Paragraph("ประเภท", header_style),
        Paragraph("หมวดหมู่", header_style), Paragraph("บัญชี", header_style),
        Paragraph("รายละเอียด", header_style), Paragraph("จำนวนเงิน", header_style),
    ]]
    type_names = {"income": "รายรับ", "expense": "รายจ่าย", "transfer": "โอนเงิน"}
    for tx in rows:
        account = tx.account.name if tx.account else "-"
        if tx.type == "transfer":
            account = f"{account} ไป {tx.to_account.name if tx.to_account else '-'}"
        signed_amount = float(tx.amount) if tx.type == "income" else -float(tx.amount) if tx.type == "expense" else float(tx.amount)
        table_rows.append([
            Paragraph(tx.date.isoformat(), cell_style),
            Paragraph(type_names[tx.type], cell_style),
            Paragraph(tx.category.name if tx.category else ("โอนเงิน" if tx.type == "transfer" else "-"), cell_style),
            Paragraph(account, cell_style),
            Paragraph(tx.note or "-", cell_style),
            Paragraph(f"{signed_amount:,.2f}", amount_style),
        ])
    if len(table_rows) == 1:
        table_rows.append([Paragraph("ไม่พบรายการในช่วงเวลาที่เลือก", cell_style), "", "", "", "", ""])

    transactions_table = Table(
        table_rows,
        colWidths=[25 * mm, 23 * mm, 39 * mm, 47 * mm, 96 * mm, 32 * mm],
        repeatRows=1,
    )
    transactions_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173B8E")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 1), (-1, -1), PDF_FONT),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E4E7EC")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("SPAN", (0, 1), (-1, 1)) if len(rows) == 0 else ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.white),
    ]))
    story.append(transactions_table)
    document.build(story, onFirstPage=pdf_page_number, onLaterPages=pdf_page_number)

    filename = f"financial-report-{bangkok_today().isoformat()}.pdf"
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
