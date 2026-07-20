from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from personal_finance.db import get_db
from personal_finance.local_time import bangkok_today
from personal_finance.models import SavingsGoal, User
from personal_finance.schemas import SavingsGoalCreate, SavingsGoalOut, SavingsGoalUpdate
from personal_finance.security import current_user


router = APIRouter(prefix="/api/savings", tags=["Savings Goals"])


class SavingsContribution(BaseModel):
    amount: Decimal = Field(gt=0, le=Decimal("999999999999.99"))


@router.get("", response_model=list[SavingsGoalOut])
def get_savings_goals(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.query(SavingsGoal).filter(SavingsGoal.user_id == user.id).order_by(SavingsGoal.target_date).all()


@router.post("", response_model=SavingsGoalOut, status_code=201)
def create_savings_goal(payload: SavingsGoalCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if payload.target_date < bangkok_today():
        raise HTTPException(status_code=422, detail="วันที่เป้าหมายต้องไม่เป็นอดีต")
    goal = SavingsGoal(user_id=user.id, **payload.model_dump())
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


@router.post("/{goal_id}/contribute", response_model=SavingsGoalOut)
def contribute_savings(
    goal_id: int,
    payload: SavingsContribution,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    goal = db.query(SavingsGoal).filter(
        SavingsGoal.id == goal_id,
        SavingsGoal.user_id == user.id,
    ).with_for_update().first()
    if not goal:
        raise HTTPException(status_code=404, detail="ไม่พบเป้าหมายการออมนี้")
    goal.current_amount += payload.amount
    db.commit()
    db.refresh(goal)
    return goal


@router.put("/{goal_id}", response_model=SavingsGoalOut)
def update_savings_goal(
    goal_id: int,
    payload: SavingsGoalUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    goal = db.query(SavingsGoal).filter(SavingsGoal.id == goal_id, SavingsGoal.user_id == user.id).first()
    if not goal:
        raise HTTPException(status_code=404, detail="ไม่พบเป้าหมายการออมนี้")
    values = payload.model_dump(exclude_unset=True)
    if values.get("target_date") and values["target_date"] < bangkok_today():
        raise HTTPException(status_code=422, detail="วันที่เป้าหมายต้องไม่เป็นอดีต")
    for key, value in values.items():
        setattr(goal, key, value)
    db.commit()
    db.refresh(goal)
    return goal


@router.delete("/{goal_id}")
def delete_savings_goal(goal_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    goal = db.query(SavingsGoal).filter(SavingsGoal.id == goal_id, SavingsGoal.user_id == user.id).first()
    if not goal:
        raise HTTPException(status_code=404, detail="ไม่พบเป้าหมายการออมนี้")
    db.delete(goal)
    db.commit()
    return {"message": "ลบเป้าหมายการออมเงินเรียบร้อยแล้ว"}
