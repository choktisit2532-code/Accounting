"""Initial production schema.

Revision ID: 20260716_0001
Revises:
"""
from alembic import op
import sqlalchemy as sa


revision = "20260716_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("line_user_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("email"), sa.UniqueConstraint("line_user_id"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_line_user_id", "users", ["line_user_id"])
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("type IN ('cash','bank','credit_card','investment','other')", name="account_type_check"),
    )
    op.create_index("ix_accounts_user_id", "accounts", ["user_id"])
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("color", sa.String(20), nullable=True),
        sa.CheckConstraint("type IN ('income','expense')", name="category_type_check"),
        sa.UniqueConstraint("user_id", "name", "type", name="uq_category_user_name_type"),
    )
    op.create_index("ix_categories_user_id", "categories", ["user_id"])
    op.create_table(
        "savings_goals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("target_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("current_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("target_amount > 0", name="savings_target_positive"),
        sa.CheckConstraint("current_amount >= 0", name="savings_current_nonnegative"),
    )
    op.create_index("ix_savings_goals_user_id", "savings_goals", ["user_id"])
    op.create_table(
        "line_pair_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_line_pair_codes_user_id", "line_pair_codes", ["user_id"])
    op.create_index("ix_line_pair_codes_code", "line_pair_codes", ["code"])
    op.create_index("ix_line_pair_codes_expires_at", "line_pair_codes", ["expires_at"])
    op.create_table(
        "line_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(150), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index("ix_line_events_event_key", "line_events", ["event_key"])
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("to_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("receipt_path", sa.String(500), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("type IN ('income','expense','transfer')", name="transaction_type_check"),
        sa.CheckConstraint("amount > 0", name="transaction_amount_positive"),
        sa.CheckConstraint("(type = 'transfer' AND to_account_id IS NOT NULL AND to_account_id <> account_id) OR (type <> 'transfer' AND to_account_id IS NULL)", name="transaction_transfer_accounts_check"),
        sa.UniqueConstraint("source", "external_id", name="uq_transaction_source_external"),
    )
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_date", "transactions", ["date"])
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("limit_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("limit_amount > 0", name="budget_limit_positive"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="budget_month_check"),
        sa.CheckConstraint("year BETWEEN 2000 AND 2200", name="budget_year_check"),
        sa.UniqueConstraint("user_id", "category_id", "month", "year", name="uq_budget_period"),
    )
    op.create_index("ix_budgets_user_id", "budgets", ["user_id"])
    op.create_table(
        "pending_line_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(150), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_user_id", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("receipt_path", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('pending','confirmed','cancelled','expired')", name="pending_line_status_check"),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index("ix_pending_line_transactions_event_key", "pending_line_transactions", ["event_key"])
    op.create_index("ix_pending_line_transactions_user_id", "pending_line_transactions", ["user_id"])
    op.create_index("ix_pending_line_transactions_line_user_id", "pending_line_transactions", ["line_user_id"])
    op.create_index("ix_pending_line_transactions_status", "pending_line_transactions", ["status"])
    op.create_index("ix_pending_line_transactions_expires_at", "pending_line_transactions", ["expires_at"])


def downgrade() -> None:
    for table in [
        "pending_line_transactions", "budgets", "transactions", "line_events",
        "line_pair_codes", "savings_goals", "categories", "accounts", "users",
    ]:
        op.drop_table(table)
