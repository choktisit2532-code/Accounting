from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from personal_finance.db import get_db
from personal_finance.models import Account, Transaction, User
from personal_finance.schemas import AccountCreate, AccountOut, AccountReconcile, AccountUpdate
from personal_finance.security import current_user
from personal_finance.services.ledger import create_transaction


router = APIRouter(prefix="/api/accounts", tags=["Accounts"])


@router.get("", response_model=list[AccountOut])
def get_accounts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.query(Account).filter(Account.user_id == user.id).order_by(Account.created_at).all()


@router.post("", response_model=AccountOut, status_code=201)
def create_account(payload: AccountCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = Account(user_id=user.id, name=payload.name, type=payload.type, balance=Decimal("0.00"))
    db.add(account)
    db.flush()
    if payload.balance != 0:
        create_transaction(
            db,
            user_id=user.id,
            tx_type="income" if payload.balance > 0 else "expense",
            amount=abs(payload.balance),
            account_id=account.id,
            note="ยอดตั้งต้นของบัญชี",
            source="system",
        )
    db.commit()
    db.refresh(account)
    return account


@router.put("/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    payload: AccountUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    account = db.query(Account).filter(Account.id == account_id, Account.user_id == user.id).first()
    if not account:
        raise HTTPException(status_code=404, detail="ไม่พบบัญชีนี้")
    if payload.name is not None:
        account.name = " ".join(payload.name.split())
    if payload.type is not None:
        account.type = payload.type
    db.commit()
    db.refresh(account)
    return account


@router.post("/{account_id}/reconcile", response_model=AccountOut)
def reconcile_account(
    account_id: int,
    payload: AccountReconcile,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    account = (
        db.query(Account)
        .filter(Account.id == account_id, Account.user_id == user.id)
        .with_for_update()
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="ไม่พบบัญชีนี้")
    difference = payload.actual_balance - account.balance
    if difference != 0:
        create_transaction(
            db,
            user_id=user.id,
            tx_type="income" if difference > 0 else "expense",
            amount=abs(difference),
            account_id=account.id,
            note=payload.note or "ปรับยอดจากการตรวจสอบยอดคงเหลือ",
            source="system",
        )
    db.commit()
    db.refresh(account)
    return account


@router.delete("/{account_id}")
def delete_account(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id, Account.user_id == user.id).first()
    if not account:
        raise HTTPException(status_code=404, detail="ไม่พบบัญชีนี้")
    has_history = db.query(Transaction.id).filter(
        Transaction.user_id == user.id,
        (Transaction.account_id == account_id) | (Transaction.to_account_id == account_id),
    ).first()
    if has_history:
        raise HTTPException(
            status_code=409,
            detail="บัญชีนี้มีประวัติธุรกรรม จึงไม่สามารถลบได้ เพื่อรักษาความถูกต้องของรายงาน",
        )
    db.delete(account)
    db.commit()
    return {"message": "ลบบัญชีเรียบร้อยแล้ว"}
