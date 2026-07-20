import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def _as_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _database_url() -> str:
    value = (
        os.getenv("PERSONAL_FINANCE_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or f"sqlite:///{BASE_DIR / 'data' / 'personal_finance.db'}"
    )
    if value.startswith("postgres://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgres://")
    elif value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development").strip().lower()
    database_url: str = _database_url()
    secret_key: str = os.getenv("SECRET_KEY", "")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
    cookie_name: str = os.getenv("AUTH_COOKIE_NAME", "pf_session")
    auto_create_tables: bool = _as_bool("AUTO_CREATE_TABLES", True)
    allowed_origins: tuple[str, ...] = tuple(
        x.strip() for x in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",") if x.strip()
    )
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "data" / "receipts"))).resolve()
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
    max_receipt_dimension: int = int(os.getenv("MAX_RECEIPT_DIMENSION", "1600"))
    max_receipt_pixels: int = int(os.getenv("MAX_RECEIPT_PIXELS", "40000000"))
    receipt_webp_quality: int = int(os.getenv("RECEIPT_WEBP_QUALITY", "80"))
    receipt_storage_backend: str = os.getenv("RECEIPT_STORAGE_BACKEND", "local").strip().lower()
    supabase_url: str = os.getenv("SUPABASE_URL", "").strip()
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    supabase_storage_bucket: str = os.getenv("SUPABASE_STORAGE_BUCKET", "receipts").strip()
    line_channel_access_token: str = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    line_channel_secret: str = os.getenv("LINE_CHANNEL_SECRET", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def validate(self) -> None:
        if self.is_production and len(self.secret_key) < 32:
            raise RuntimeError("SECRET_KEY must contain at least 32 characters in production")
        if self.is_production and self.auto_create_tables:
            raise RuntimeError("AUTO_CREATE_TABLES must be false in production; run Alembic migrations")
        if self.receipt_storage_backend not in {"local", "supabase"}:
            raise RuntimeError("RECEIPT_STORAGE_BACKEND must be local or supabase")
        if self.receipt_storage_backend == "supabase" and (
            not self.supabase_url
            or not self.supabase_service_role_key
            or not self.supabase_storage_bucket
        ):
            raise RuntimeError(
                "SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_STORAGE_BUCKET are required"
            )
        if self.receipt_storage_backend == "supabase" and not self.supabase_url.startswith(
            ("https://", "http://")
        ):
            raise RuntimeError(
                "SUPABASE_URL must be the HTTPS Project URL, not the PostgreSQL connection string"
            )
        if not 1 <= self.receipt_webp_quality <= 100:
            raise RuntimeError("RECEIPT_WEBP_QUALITY must be between 1 and 100")
        if self.max_receipt_dimension < 320 or self.max_receipt_pixels < 1_000_000:
            raise RuntimeError("Receipt image limits are invalid")


settings = Settings()
