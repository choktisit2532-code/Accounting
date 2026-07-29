import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from personal_finance.config import settings
from personal_finance.db import get_db
from personal_finance.models import User


ALGORITHM = "HS256"
ISSUER = "personal-finance"
SECRET_KEY = settings.secret_key or secrets.token_urlsafe(48)
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": str(user.id), "iss": ISSUER, "iat": now, "exp": expiry, "jti": secrets.token_urlsafe(16)},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(settings.cookie_name, path="/", secure=settings.is_production, samesite="lax")


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session_cookie: str | None = Cookie(default=None, alias=settings.cookie_name),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials if credentials else session_cookie
    if not token:
        raise HTTPException(status_code=401, detail="กรุณาเข้าสู่ระบบ")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], issuer=ISSUER)
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Token ไม่ถูกต้องหรือหมดอายุ")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="ไม่พบบัญชีผู้ใช้")
    return user


class SlidingWindowRateLimiter:
    """Process-local safety net; use an edge or Redis limiter for multiple instances."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        threshold = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] < threshold:
                events.popleft()
            if len(events) >= limit:
                raise HTTPException(status_code=429, detail="มีคำขอมากเกินไป กรุณารอสักครู่แล้วลองใหม่")
            events.append(now)


rate_limiter = SlidingWindowRateLimiter()


def client_key(request: Request, scope: str) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    host = forwarded or (request.client.host if request.client else "unknown")
    return f"{scope}:{host}"
