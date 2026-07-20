from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_finance.db import get_db
from personal_finance.models import Category, User
from personal_finance.schemas import UserLogin, UserOut, UserRegister
from personal_finance.security import (
    clear_auth_cookie,
    client_key,
    create_token,
    current_user,
    hash_password,
    rate_limiter,
    set_auth_cookie,
    verify_password,
)


router = APIRouter(prefix="/api/auth", tags=["Authentication"])
DEFAULT_CATEGORIES = [
    {"name": "เงินเดือน", "type": "income", "icon": "fa-briefcase", "color": "#10B981"},
    {"name": "ธุรกิจส่วนตัว", "type": "income", "icon": "fa-store", "color": "#3B82F6"},
    {"name": "การลงทุน", "type": "income", "icon": "fa-chart-line", "color": "#F59E0B"},
    {"name": "รายรับอื่น ๆ", "type": "income", "icon": "fa-hand-holding-dollar", "color": "#8B5CF6"},
    {"name": "อาหารและเครื่องดื่ม", "type": "expense", "icon": "fa-utensils", "color": "#EF4444"},
    {"name": "การเดินทาง / ยานพาหนะ", "type": "expense", "icon": "fa-car", "color": "#3B82F6"},
    {"name": "ช้อปปิ้ง", "type": "expense", "icon": "fa-bag-shopping", "color": "#EC4899"},
    {"name": "ที่พักอาศัย / ค่าเช่า", "type": "expense", "icon": "fa-house", "color": "#10B981"},
    {"name": "ค่าสาธารณูปโภค (น้ำ, ไฟ, เน็ต)", "type": "expense", "icon": "fa-bolt", "color": "#F59E0B"},
    {"name": "ความบันเทิง / ท่องเที่ยว", "type": "expense", "icon": "fa-gamepad", "color": "#8B5CF6"},
    {"name": "สุขภาพ / รักษาพยาบาล", "type": "expense", "icon": "fa-heart-pulse", "color": "#06B6D4"},
    {"name": "การศึกษา", "type": "expense", "icon": "fa-graduation-cap", "color": "#6366F1"},
    {"name": "ของใช้ในบ้าน", "type": "expense", "icon": "fa-basket-shopping", "color": "#14B8A6"},
    {"name": "รายจ่ายอื่น ๆ", "type": "expense", "icon": "fa-ellipsis", "color": "#6B7280"},
]


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: UserRegister, request: Request, db: Session = Depends(get_db)):
    rate_limiter.check(client_key(request, "register"), limit=5, window_seconds=3600)
    email = str(payload.email).strip().lower()
    if db.query(User.id).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="อีเมลนี้ถูกใช้งานไปแล้ว")
    user = User(email=email, full_name=payload.full_name, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()
    db.add_all([Category(user_id=user.id, **category) for category in DEFAULT_CATEGORIES])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="อีเมลนี้ถูกใช้งานไปแล้ว")
    db.refresh(user)
    return user


@router.post("/login")
def login(
    payload: UserLogin,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    rate_limiter.check(client_key(request, "login"), limit=8, window_seconds=900)
    user = db.query(User).filter(User.email == str(payload.email).strip().lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="อีเมลหรือรหัสผ่านไม่ถูกต้อง")
    set_auth_cookie(response, create_token(user))
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "line_user_id": user.line_user_id,
        }
    }


@router.post("/logout")
def logout(response: Response):
    clear_auth_cookie(response)
    return {"message": "ออกจากระบบแล้ว"}


@router.get("/me", response_model=UserOut)
def get_me(user: User = Depends(current_user)):
    return user
