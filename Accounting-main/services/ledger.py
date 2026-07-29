from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from personal_finance.models import Account, Category, Transaction
from personal_finance.local_time import bangkok_today


VALID_TRANSACTION_TYPES = {"income", "expense", "transfer"}


def _money(value: Decimal | float | str) -> Decimal:
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise HTTPException(status_code=422, detail="จำนวนเงินต้องมากกว่า 0")
    return amount


def _owned_accounts(db: Session, user_id: int, account_ids: list[int]) -> dict[int, Account]:
    ids = sorted(set(account_ids))
    rows = (
        db.query(Account)
        .filter(Account.user_id == user_id, Account.id.in_(ids))
        .order_by(Account.id)
        .with_for_update()
        .all()
    )
    accounts = {row.id: row for row in rows}
    if len(accounts) != len(ids):
        raise HTTPException(status_code=404, detail="ไม่พบบัญชีที่ระบุหรือไม่มีสิทธิ์ใช้งาน")
    return accounts


def validate_category(db: Session, user_id: int, category_id: int | None, tx_type: str) -> Category | None:
    if tx_type == "transfer":
        if category_id is not None:
            raise HTTPException(status_code=422, detail="รายการโอนเงินไม่ใช้หมวดหมู่")
        return None
    if category_id is None:
        return None
    category = db.query(Category).filter(
        Category.id == category_id,
        (Category.user_id == user_id) | (Category.user_id.is_(None)),
    ).first()
    if not category:
        raise HTTPException(status_code=404, detail="ไม่พบหมวดหมู่ที่ระบุ")
    if category.type != tx_type:
        raise HTTPException(status_code=422, detail="ประเภทหมวดหมู่ไม่ตรงกับประเภทธุรกรรม")
    return category


def apply_effect(
    db: Session,
    *,
    user_id: int,
    tx_type: str,
    amount: Decimal | float | str,
    account_id: int,
    to_account_id: int | None,
    direction: int = 1,
) -> None:
    if tx_type not in VALID_TRANSACTION_TYPES:
        raise HTTPException(status_code=422, detail="ประเภทธุรกรรมไม่ถูกต้อง")
    amount = _money(amount) * direction
    if tx_type == "transfer":
        if not to_account_id:
            raise HTTPException(status_code=422, detail="ต้องระบุบัญชีปลายทาง")
        if account_id == to_account_id:
            raise HTTPException(status_code=422, detail="บัญชีต้นทางและปลายทางต้องไม่ซ้ำกัน")
        accounts = _owned_accounts(db, user_id, [account_id, to_account_id])
        accounts[account_id].balance -= amount
        accounts[to_account_id].balance += amount
        return

    account = _owned_accounts(db, user_id, [account_id])[account_id]
    account.balance += amount if tx_type == "income" else -amount


def create_transaction(
    db: Session,
    *,
    user_id: int,
    tx_type: str,
    amount: Decimal | float | str,
    account_id: int,
    category_id: int | None = None,
    to_account_id: int | None = None,
    tx_date: date | None = None,
    note: str | None = None,
    receipt_path: str | None = None,
    source: str = "web",
    external_id: str | None = None,
) -> Transaction:
    amount = _money(amount)
    validate_category(db, user_id, category_id, tx_type)
    apply_effect(
        db,
        user_id=user_id,
        tx_type=tx_type,
        amount=amount,
        account_id=account_id,
        to_account_id=to_account_id,
    )
    tx = Transaction(
        user_id=user_id,
        type=tx_type,
        amount=amount,
        category_id=category_id,
        account_id=account_id,
        to_account_id=to_account_id,
        date=tx_date or bangkok_today(),
        note=note,
        receipt_path=receipt_path,
        source=source,
        external_id=external_id,
    )
    db.add(tx)
    db.flush()
    return tx


def revert_transaction(db: Session, tx: Transaction) -> None:
    apply_effect(
        db,
        user_id=tx.user_id,
        tx_type=tx.type,
        amount=tx.amount,
        account_id=tx.account_id,
        to_account_id=tx.to_account_id,
        direction=-1,
    )
