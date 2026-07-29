from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from personal_finance.db import get_db
from personal_finance.local_time import bangkok_today
from personal_finance.models import Budget, Category, Transaction, User
from personal_finance.schemas import BudgetCreate, BudgetOut
from personal_finance.security import current_user


router = APIRouter(prefix="/api/budgets", tags=["Budgets"])


@router.get("")
def get_budgets(
    month: int | None = None,
    year: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    today = bangkok_today()
    month = month or today.month
    year = year or today.year
    if month < 1 or month > 12 or year < 2000 or year > 2200:
        raise HTTPException(status_code=422, detail="เดือนหรือปีไม่ถูกต้อง")
    budgets = db.query(Budget).filter(
        Budget.user_id == user.id,
        Budget.month == month,
        Budget.year == year,
    ).all()
    result = []
    for budget in budgets:
        start = date(budget.year, budget.month, 1)
        end = date(budget.year + 1, 1, 1) if budget.month == 12 else date(budget.year, budget.month + 1, 1)
        spent = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
            Transaction.user_id == user.id,
            Transaction.category_id == budget.category_id,
            Transaction.type == "expense",
            Transaction.date >= start,
            Transaction.date < end,
        ).scalar()
        result.append({
            "id": budget.id,
            "category_id": budget.category_id,
            "category_name": budget.category.name if budget.category else "ไม่มีหมวดหมู่",
            "category_color": budget.category.color if budget.category else "#6B7280",
            "category_icon": budget.category.icon if budget.category else "fa-tag",
            "limit_amount": float(budget.limit_amount),
            "spent_amount": float(spent),
            "month": budget.month,
            "year": budget.year,
        })
    return result


@router.post("", response_model=BudgetOut)
def create_or_update_budget(
    payload: BudgetCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    category = db.query(Category).filter(
        Category.id == payload.category_id,
        (Category.user_id == user.id) | (Category.user_id.is_(None)),
    ).first()
    if not category:
        raise HTTPException(status_code=404, detail="ไม่พบหมวดหมู่นี้")
    if category.type != "expense":
        raise HTTPException(status_code=422, detail="ตั้งงบประมาณได้เฉพาะหมวดรายจ่าย")
    budget = db.query(Budget).filter(
        Budget.user_id == user.id,
        Budget.category_id == payload.category_id,
        Budget.month == payload.month,
        Budget.year == payload.year,
    ).first()
    if budget:
        budget.limit_amount = payload.limit_amount
    else:
        budget = Budget(user_id=user.id, **payload.model_dump())
        db.add(budget)
    db.commit()
    db.refresh(budget)
    return budget


@router.delete("/{budget_id}")
def delete_budget(budget_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    budget = db.query(Budget).filter(Budget.id == budget_id, Budget.user_id == user.id).first()
    if not budget:
        raise HTTPException(status_code=404, detail="ไม่พบรายการงบประมาณนี้")
    db.delete(budget)
    db.commit()
    return {"message": "ลบรายการงบประมาณเรียบร้อยแล้ว"}
