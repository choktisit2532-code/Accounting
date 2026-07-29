from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_finance.db import get_db
from personal_finance.models import Budget, Category, Transaction, User
from personal_finance.schemas import CategoryCreate, CategoryOut
from personal_finance.security import current_user


router = APIRouter(prefix="/api/categories", tags=["Categories"])


@router.get("", response_model=list[CategoryOut])
def get_categories(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.query(Category).filter(
        (Category.user_id == user.id) | (Category.user_id.is_(None))
    ).order_by(Category.type, Category.name).all()


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(payload: CategoryCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    category = Category(
        user_id=user.id,
        name=payload.name,
        type=payload.type,
        icon=payload.icon or "fa-tag",
        color=payload.color or "#6B7280",
    )
    db.add(category)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="มีหมวดหมู่ชื่อนี้อยู่แล้ว")
    db.refresh(category)
    return category


@router.put("/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: int,
    payload: CategoryCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    category = db.query(Category).filter(Category.id == category_id, Category.user_id == user.id).first()
    if not category:
        raise HTTPException(status_code=404, detail="ไม่พบหมวดหมู่นี้หรือเป็นหมวดหมู่ระบบ")
    used_by_other_type = db.query(Transaction.id).filter(
        Transaction.user_id == user.id,
        Transaction.category_id == category.id,
        Transaction.type != payload.type,
    ).first()
    if used_by_other_type:
        raise HTTPException(status_code=409, detail="เปลี่ยนประเภทไม่ได้ เพราะมีธุรกรรมเดิมใช้งานหมวดหมู่นี้")
    category.name = payload.name
    category.type = payload.type
    category.icon = payload.icon or "fa-tag"
    category.color = payload.color or "#6B7280"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="มีหมวดหมู่ชื่อนี้อยู่แล้ว")
    db.refresh(category)
    return category


@router.delete("/{category_id}")
def delete_category(category_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == category_id, Category.user_id == user.id).first()
    if not category:
        raise HTTPException(status_code=404, detail="ไม่พบหมวดหมู่นี้หรือเป็นหมวดหมู่ระบบ")
    if db.query(Budget.id).filter(Budget.user_id == user.id, Budget.category_id == category_id).first():
        raise HTTPException(status_code=409, detail="กรุณาลบงบประมาณที่ใช้หมวดหมู่นี้ก่อน")
    db.query(Transaction).filter(
        Transaction.user_id == user.id,
        Transaction.category_id == category_id,
    ).update({Transaction.category_id: None}, synchronize_session=False)
    db.delete(category)
    db.commit()
    return {"message": "ลบหมวดหมู่เรียบร้อยแล้ว โดยเก็บประวัติธุรกรรมเดิมไว้"}
