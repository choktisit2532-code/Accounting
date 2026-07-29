from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from personal_finance.db import get_db
from personal_finance.local_time import bangkok_today
from personal_finance.models import (
    Account, FleetDocument, FleetExpense, FleetMileage, FleetVehicle,
    Transaction, User,
)
from personal_finance.security import current_user
from personal_finance.services.ledger import apply_effect, create_transaction, revert_transaction

router = APIRouter(prefix="/api/fleet", tags=["Fleet"])


class VehicleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    plate_number: str = Field(min_length=1, max_length=30)
    vehicle_type: str = Field(default="car", pattern="^(car|motorcycle|other)$")
    default_account_id: int | None = None


class MileageIn(BaseModel):
    vehicle_id: int
    mileage: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=1000)
    source: str = Field(default="web", pattern="^(web|line|ocr)$")


class ExpenseIn(BaseModel):
    vehicle_id: int
    account_id: int | None = None
    category_id: int | None = None
    category: str = Field(min_length=1, max_length=50)
    amount: Decimal = Field(gt=0)
    expense_date: date = Field(default_factory=bangkok_today)
    garage_name: str | None = Field(default=None, max_length=150)
    note: str | None = Field(default=None, max_length=1000)


class DocumentIn(BaseModel):
    vehicle_id: int
    document_type: str = Field(min_length=1, max_length=40)
    expiry_date: date


def owned_vehicle(db: Session, user_id: int, vehicle_id: int, lock: bool = False) -> FleetVehicle:
    query = db.query(FleetVehicle).filter(FleetVehicle.id == vehicle_id, FleetVehicle.user_id == user_id)
    vehicle = query.with_for_update().first() if lock else query.first()
    if not vehicle:
        raise HTTPException(404, "ไม่พบรถคันนี้")
    return vehicle


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    vehicle_ids = db.query(FleetVehicle.id).filter(
        FleetVehicle.user_id == user.id, FleetVehicle.is_active.is_(True)
    )
    month_start = bangkok_today().replace(day=1)
    total = db.query(func.coalesce(func.sum(FleetExpense.amount), 0)).filter(
        FleetExpense.user_id == user.id, FleetExpense.expense_date >= month_start
    ).scalar()
    due_docs = db.query(FleetDocument).filter(
        FleetDocument.user_id == user.id,
        FleetDocument.expiry_date <= bangkok_today() + timedelta(days=30),
    ).count()
    return {
        "vehicles": vehicle_ids.count(),
        "monthly_expense": total,
        "documents_due": due_docs,
    }


@router.get("/vehicles")
def vehicles(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.query(FleetVehicle).filter(FleetVehicle.user_id == user.id).order_by(FleetVehicle.name).all()


@router.post("/vehicles", status_code=201)
def add_vehicle(payload: VehicleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if payload.default_account_id and not db.query(Account).filter(
        Account.id == payload.default_account_id, Account.user_id == user.id
    ).first():
        raise HTTPException(404, "ไม่พบบัญชีเริ่มต้น")
    vehicle = FleetVehicle(user_id=user.id, **payload.model_dump())
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


@router.post("/mileages", status_code=201)
def add_mileage(payload: MileageIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    vehicle = owned_vehicle(db, user.id, payload.vehicle_id, lock=True)
    if payload.mileage < vehicle.current_mileage:
        raise HTTPException(422, "เลขไมล์ใหม่ต้องไม่น้อยกว่าเลขไมล์ปัจจุบัน")
    item = FleetMileage(user_id=user.id, **payload.model_dump())
    vehicle.current_mileage = payload.mileage
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/expenses")
def expenses(vehicle_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = db.query(FleetExpense).filter(FleetExpense.user_id == user.id)
    if vehicle_id:
        owned_vehicle(db, user.id, vehicle_id)
        query = query.filter(FleetExpense.vehicle_id == vehicle_id)
    return query.order_by(FleetExpense.expense_date.desc(), FleetExpense.id.desc()).limit(200).all()


@router.post("/expenses", status_code=201)
def add_expense(payload: ExpenseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    vehicle = owned_vehicle(db, user.id, payload.vehicle_id)
    account_id = payload.account_id or vehicle.default_account_id
    if not account_id:
        raise HTTPException(422, "กรุณาเลือกบัญชีที่ใช้จ่ายหรือตั้งบัญชีเริ่มต้นให้รถ")
    if payload.expense_date > bangkok_today():
        raise HTTPException(422, "ไม่สามารถบันทึกค่าใช้จ่ายในอนาคตได้")
    note = f"{vehicle.name} ({vehicle.plate_number}) · {payload.category}"
    if payload.garage_name:
        note += f" · {payload.garage_name}"
    if payload.note:
        note += f" · {payload.note}"
    try:
        tx = create_transaction(
            db, user_id=user.id, tx_type="expense", amount=payload.amount,
            account_id=account_id, category_id=payload.category_id,
            tx_date=payload.expense_date, note=note, source="fleet",
        )
        item = FleetExpense(
            user_id=user.id, vehicle_id=vehicle.id, transaction_id=tx.id,
            category=payload.category, amount=payload.amount,
            expense_date=payload.expense_date, garage_name=payload.garage_name,
            note=payload.note,
        )
        db.add(item)
        db.flush()
        tx.external_id = f"fleet-expense:{item.id}"
        db.commit()
        db.refresh(item)
        return item
    except Exception:
        db.rollback()
        raise


@router.put("/expenses/{expense_id}")
def update_expense(expense_id: int, payload: ExpenseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.query(FleetExpense).filter(
        FleetExpense.id == expense_id, FleetExpense.user_id == user.id
    ).with_for_update().first()
    if not item:
        raise HTTPException(404, "ไม่พบค่าใช้จ่าย")
    vehicle = owned_vehicle(db, user.id, payload.vehicle_id)
    tx = db.query(Transaction).filter(
        Transaction.id == item.transaction_id, Transaction.user_id == user.id
    ).with_for_update().first()
    if not tx:
        raise HTTPException(409, "ไม่พบรายการบัญชีที่เชื่อมกับค่าใช้จ่ายนี้")
    account_id = payload.account_id or vehicle.default_account_id
    if not account_id:
        raise HTTPException(422, "กรุณาเลือกบัญชีที่ใช้จ่าย")
    if payload.expense_date > bangkok_today():
        raise HTTPException(422, "ไม่สามารถบันทึกค่าใช้จ่ายในอนาคตได้")
    try:
        revert_transaction(db, tx)
        apply_effect(db, user_id=user.id, tx_type="expense", amount=payload.amount,
                     account_id=account_id, to_account_id=None)
        tx.amount, tx.account_id, tx.category_id, tx.date = (
            payload.amount, account_id, payload.category_id, payload.expense_date
        )
        note = f"{vehicle.name} ({vehicle.plate_number}) · {payload.category}"
        if payload.garage_name:
            note += f" · {payload.garage_name}"
        if payload.note:
            note += f" · {payload.note}"
        tx.note = note
        item.vehicle_id, item.category, item.amount = payload.vehicle_id, payload.category, payload.amount
        item.expense_date, item.garage_name, item.note = payload.expense_date, payload.garage_name, payload.note
        db.commit()
        db.refresh(item)
        return item
    except Exception:
        db.rollback()
        raise


@router.delete("/expenses/{expense_id}")
def delete_expense(expense_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.query(FleetExpense).filter(
        FleetExpense.id == expense_id, FleetExpense.user_id == user.id
    ).with_for_update().first()
    if not item:
        raise HTTPException(404, "ไม่พบค่าใช้จ่าย")
    tx = db.query(Transaction).filter(
        Transaction.id == item.transaction_id, Transaction.user_id == user.id
    ).with_for_update().first()
    try:
        if tx:
            revert_transaction(db, tx)
            db.delete(item)
            db.flush()
            db.delete(tx)
        else:
            db.delete(item)
        db.commit()
        return {"message": "ลบค่าใช้จ่ายรถและคืนยอดบัญชีแล้ว"}
    except Exception:
        db.rollback()
        raise


@router.post("/documents", status_code=201)
def add_document(payload: DocumentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_vehicle(db, user.id, payload.vehicle_id)
    item = FleetDocument(user_id=user.id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/documents")
def documents(vehicle_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = db.query(FleetDocument).filter(FleetDocument.user_id == user.id)
    if vehicle_id:
        owned_vehicle(db, user.id, vehicle_id)
        query = query.filter(FleetDocument.vehicle_id == vehicle_id)
    return query.order_by(FleetDocument.expiry_date, FleetDocument.id.desc()).all()


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.query(FleetDocument).filter(
        FleetDocument.id == document_id, FleetDocument.user_id == user.id
    ).first()
    if not item:
        raise HTTPException(404, "ไม่พบเอกสารรถ")
    db.delete(item)
    db.commit()
    return {"message": "ลบเอกสารรถแล้ว"}
