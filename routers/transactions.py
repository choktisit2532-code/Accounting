from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from personal_finance.config import settings
from personal_finance.db import get_db
from personal_finance.local_time import bangkok_today
from personal_finance.models import Account, Category, Transaction, User
from personal_finance.schemas import TransactionOut, TransactionUpdate
from personal_finance.security import current_user
from personal_finance.services.ledger import apply_effect, create_transaction as ledger_create
from personal_finance.services.ledger import revert_transaction, validate_category
from personal_finance.services.receipt_storage import ReceiptStorageError
from personal_finance.services.receipt_storage import delete_receipt as storage_delete_receipt
from personal_finance.services.receipt_storage import load_receipt
from personal_finance.services.receipt_storage import save_receipt


router = APIRouter(prefix="/api/transactions", tags=["Transactions"])


def save_receipt_bytes(data: bytes, user_id: int) -> str:
    try:
        return save_receipt(data, user_id)
    except ValueError as exc:
        status_code = 413 if "ขนาด" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except ReceiptStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _save_receipt(receipt: UploadFile | None, user_id: int) -> str | None:
    if receipt is None:
        return None
    return save_receipt_bytes(receipt.file.read(settings.max_upload_bytes + 1), user_id)


def delete_receipt(path: str | None) -> None:
    storage_delete_receipt(path)


@router.get("", response_model=list[TransactionOut])
def get_transactions(
    response: Response,
    account_id: int | None = None,
    category_id: int | None = None,
    type: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    response.headers["X-Applied-Transaction-Type"] = type or "all"
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    query = db.query(Transaction).filter(Transaction.user_id == user.id)
    if account_id is not None:
        query = query.filter((Transaction.account_id == account_id) | (Transaction.to_account_id == account_id))
    if category_id is not None:
        query = query.filter(Transaction.category_id == category_id)
    if type is not None:
        if type not in {"income", "expense", "transfer"}:
            raise HTTPException(status_code=422, detail="ประเภทธุรกรรมไม่ถูกต้อง")
        query = query.filter(Transaction.type == type)
    if start_date is not None:
        query = query.filter(Transaction.date >= start_date)
    if end_date is not None:
        query = query.filter(Transaction.date <= end_date)
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="วันที่เริ่มต้นต้องไม่เกินวันที่สิ้นสุด")
    if search:
        pattern = f"%{search[:100]}%"
        query = query.filter(or_(
            Transaction.note.ilike(pattern),
            Transaction.category.has(Category.name.ilike(pattern)),
            Transaction.account.has(Account.name.ilike(pattern)),
            Transaction.to_account.has(Account.name.ilike(pattern)),
        ))
    return query.order_by(Transaction.date.desc(), Transaction.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/summary")
def get_transactions_summary(
    type: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    search: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if type is not None and type not in {"income", "expense", "transfer"}:
        raise HTTPException(status_code=422, detail="ประเภทธุรกรรมไม่ถูกต้อง")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="วันที่เริ่มต้นต้องไม่เกินวันที่สิ้นสุด")
    query = db.query(Transaction).filter(Transaction.user_id == user.id)
    if type is not None:
        query = query.filter(Transaction.type == type)
    if start_date is not None:
        query = query.filter(Transaction.date >= start_date)
    if end_date is not None:
        query = query.filter(Transaction.date <= end_date)
    if search:
        pattern = f"%{search[:100]}%"
        query = query.filter(or_(
            Transaction.note.ilike(pattern),
            Transaction.category.has(Category.name.ilike(pattern)),
            Transaction.account.has(Account.name.ilike(pattern)),
            Transaction.to_account.has(Account.name.ilike(pattern)),
        ))

    count, income, expense, transfer = query.with_entities(
        func.count(Transaction.id),
        func.coalesce(func.sum(Transaction.amount).filter(
            Transaction.type == "income", Transaction.source != "system"
        ), 0),
        func.coalesce(func.sum(Transaction.amount).filter(
            Transaction.type == "expense", Transaction.source != "system"
        ), 0),
        func.coalesce(func.sum(Transaction.amount).filter(Transaction.type == "transfer"), 0),
    ).one()
    income_value = float(income)
    expense_value = float(expense)
    return {
        "count": count,
        "income": income_value,
        "expense": expense_value,
        "net": income_value - expense_value,
        "transfer": float(transfer),
    }


@router.post("", response_model=TransactionOut, status_code=201)
def create_transaction(
    type: str = Form(...),
    amount: Decimal = Form(...),
    account_id: int = Form(...),
    category_id: int | None = Form(None),
    to_account_id: int | None = Form(None),
    date_val: str | None = Form(None),
    note: str | None = Form(None, max_length=1000),
    receipt: UploadFile | None = File(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        parsed_date = date.fromisoformat(date_val) if date_val else bangkok_today()
    except ValueError:
        raise HTTPException(status_code=422, detail="รูปแบบวันที่ต้องเป็น YYYY-MM-DD")
    if parsed_date > bangkok_today():
        raise HTTPException(status_code=422, detail="ไม่สามารถบันทึกธุรกรรมในอนาคตได้")
    receipt_name = _save_receipt(receipt, user.id)
    try:
        tx = ledger_create(
            db,
            user_id=user.id,
            tx_type=type,
            amount=amount,
            account_id=account_id,
            category_id=category_id,
            to_account_id=to_account_id,
            tx_date=parsed_date,
            note=note,
            receipt_path=receipt_name,
        )
        db.commit()
        db.refresh(tx)
        return tx
    except Exception:
        db.rollback()
        delete_receipt(receipt_name)
        raise


@router.put("/{tx_id}", response_model=TransactionOut)
def update_transaction(
    tx_id: int,
    payload: TransactionUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    tx = db.query(Transaction).filter(Transaction.id == tx_id, Transaction.user_id == user.id).with_for_update().first()
    if not tx:
        raise HTTPException(status_code=404, detail="ไม่พบรายการนี้")
    proposed = {
        "type": payload.type if "type" in payload.model_fields_set else tx.type,
        "amount": payload.amount if "amount" in payload.model_fields_set else tx.amount,
        "category_id": payload.category_id if "category_id" in payload.model_fields_set else tx.category_id,
        "account_id": payload.account_id if "account_id" in payload.model_fields_set else tx.account_id,
        "to_account_id": payload.to_account_id if "to_account_id" in payload.model_fields_set else tx.to_account_id,
        "date": payload.date if "date" in payload.model_fields_set else tx.date,
        "note": payload.note if "note" in payload.model_fields_set else tx.note,
    }
    if proposed["type"] is None or proposed["amount"] is None or proposed["account_id"] is None or proposed["date"] is None:
        raise HTTPException(status_code=422, detail="ประเภท จำนวนเงิน บัญชี และวันที่ ห้ามเป็นค่าว่าง")
    if proposed["date"] > bangkok_today():
        raise HTTPException(status_code=422, detail="ไม่สามารถบันทึกธุรกรรมในอนาคตได้")
    validate_category(db, user.id, proposed["category_id"], proposed["type"])
    if proposed["type"] == "transfer":
        if not proposed["to_account_id"] or proposed["to_account_id"] == proposed["account_id"]:
            raise HTTPException(status_code=422, detail="กรุณาระบุบัญชีปลายทางที่แตกต่างจากบัญชีต้นทาง")
        proposed["category_id"] = None
    else:
        proposed["to_account_id"] = None
    try:
        revert_transaction(db, tx)
        apply_effect(
            db,
            user_id=user.id,
            tx_type=proposed["type"],
            amount=proposed["amount"],
            account_id=proposed["account_id"],
            to_account_id=proposed["to_account_id"],
        )
        for key, value in proposed.items():
            setattr(tx, key, value)
        db.commit()
        db.refresh(tx)
        return tx
    except Exception:
        db.rollback()
        raise


@router.delete("/{tx_id}")
def delete_transaction(tx_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    tx = db.query(Transaction).filter(Transaction.id == tx_id, Transaction.user_id == user.id).with_for_update().first()
    if not tx:
        raise HTTPException(status_code=404, detail="ไม่พบรายการนี้")
    receipt_name = tx.receipt_path
    try:
        revert_transaction(db, tx)
        db.delete(tx)
        db.commit()
    except Exception:
        db.rollback()
        raise
    delete_receipt(receipt_name)
    return {"message": "ลบรายการธุรกรรมและคืนยอดบัญชีแล้ว"}


@router.get("/{tx_id}/receipt")
def get_receipt(tx_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    tx = db.query(Transaction).filter(Transaction.id == tx_id, Transaction.user_id == user.id).first()
    if not tx or not tx.receipt_path:
        raise HTTPException(status_code=404, detail="ไม่พบใบเสร็จ")
    try:
        receipt = load_receipt(tx.receipt_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์ใบเสร็จ")
    except ReceiptStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=receipt.data,
        media_type=receipt.content_type,
        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"},
    )
