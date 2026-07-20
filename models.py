from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from personal_finance.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(String(255))
    line_user_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    accounts = relationship("Account", back_populates="user", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")
    budgets = relationship("Budget", back_populates="user", cascade="all, delete-orphan")
    savings_goals = relationship("SavingsGoal", back_populates="user", cascade="all, delete-orphan")
    categories = relationship("Category", cascade="all, delete-orphan")
    pair_codes = relationship("LinePairCode", cascade="all, delete-orphan")
    pending_line_transactions = relationship("PendingLineTransaction", cascade="all, delete-orphan")


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("type IN ('cash','bank','credit_card','investment','other')", name="account_type_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(30))
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user = relationship("User", back_populates="accounts")


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint("type IN ('income','expense')", name="category_type_check"),
        UniqueConstraint("user_id", "name", "type", name="uq_category_user_name_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20))
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("type IN ('income','expense','transfer')", name="transaction_type_check"),
        CheckConstraint("amount > 0", name="transaction_amount_positive"),
        CheckConstraint(
            "(type = 'transfer' AND to_account_id IS NOT NULL AND to_account_id <> account_id) "
            "OR (type <> 'transfer' AND to_account_id IS NULL)",
            name="transaction_transfer_accounts_check",
        ),
        UniqueConstraint("source", "external_id", name="uq_transaction_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    to_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True)
    date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="web")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="transactions")
    category = relationship("Category")
    account = relationship("Account", foreign_keys=[account_id])
    to_account = relationship("Account", foreign_keys=[to_account_id])


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", "month", "year", name="uq_budget_period"),
        CheckConstraint("limit_amount > 0", name="budget_limit_positive"),
        CheckConstraint("month BETWEEN 1 AND 12", name="budget_month_check"),
        CheckConstraint("year BETWEEN 2000 AND 2200", name="budget_year_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    month: Mapped[int] = mapped_column(Integer)
    year: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user = relationship("User", back_populates="budgets")
    category = relationship("Category")


class SavingsGoal(Base):
    __tablename__ = "savings_goals"
    __table_args__ = (
        CheckConstraint("target_amount > 0", name="savings_target_positive"),
        CheckConstraint("current_amount >= 0", name="savings_current_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    target_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    current_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    target_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user = relationship("User", back_populates="savings_goals")


class LinePairCode(Base):
    __tablename__ = "line_pair_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PendingLineTransaction(Base):
    __tablename__ = "pending_line_transactions"
    __table_args__ = (
        CheckConstraint("status IN ('pending','confirmed','cancelled','expired')", name="pending_line_status_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    line_user_id: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    receipt_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LineEvent(Base):
    __tablename__ = "line_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="processing")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

