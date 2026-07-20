import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from personal_finance.config import settings
from personal_finance.db import Base, SessionLocal, engine
from personal_finance.routers.accounts import router as accounts_router
from personal_finance.routers.auth import router as auth_router
from personal_finance.routers.budgets import router as budgets_router
from personal_finance.routers.categories import router as categories_router
from personal_finance.routers.line_webhook import router as line_router
from personal_finance.routers.reports import router as reports_router
from personal_finance.routers.savings import router as savings_router
from personal_finance.routers.transactions import router as transactions_router


logger = logging.getLogger("personal_finance")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
APP_BUILD = "20260720.3"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate()
    if settings.receipt_storage_backend == "local":
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite"):
        Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Personal Finance & Accounting API",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    expose_headers=["X-Applied-Transaction-Type", "X-Request-ID"],
)


@app.middleware("http")
async def security_and_request_id(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    origin = request.headers.get("origin")
    unsafe_api = request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/")
    if unsafe_api and request.url.path != "/api/line/webhook" and origin and origin not in settings.allowed_origins:
        return JSONResponse(status_code=403, content={"detail": "Origin ไม่ได้รับอนุญาต"})

    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request error request_id=%s path=%s", request_id, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "เกิดข้อผิดพลาดภายในระบบ", "request_id": request_id})

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith(("/api/", "/static/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "script-src 'self' https://cdn.jsdelivr.net; connect-src 'self'"
    )
    return response


for router in (
    auth_router,
    accounts_router,
    categories_router,
    transactions_router,
    budgets_router,
    savings_router,
    reports_router,
    line_router,
):
    app.include_router(router)


static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/health")
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok", "app": "personal_finance", "version": "2.0.0", "build": APP_BUILD}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error", "app": "personal_finance"})


@app.get("/login")
def login_page():
    return FileResponse(static_path / "login.html", headers={"Cache-Control": "no-cache, no-store"})


@app.get("/register")
def register_page():
    return FileResponse(static_path / "register.html", headers={"Cache-Control": "no-cache, no-store"})


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(static_path / "index.html", headers={"Cache-Control": "no-cache, no-store"})


@app.get("/")
def home():
    return FileResponse(static_path / "login.html", headers={"Cache-Control": "no-cache, no-store"})
