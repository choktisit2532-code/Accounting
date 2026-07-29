"""Add Fleet module to Accounting.

Revision ID: 20260729_0003
Revises: 20260716_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20260729_0003"
down_revision = "20260716_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_vehicles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("default_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("plate_number", sa.String(30), nullable=False),
        sa.Column("vehicle_type", sa.String(20), nullable=False),
        sa.Column("current_mileage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("vehicle_type IN ('car','motorcycle','other')", name="fleet_vehicle_type_check"),
        sa.UniqueConstraint("user_id", "plate_number", name="uq_fleet_vehicle_user_plate"),
    )
    op.create_index("ix_fleet_vehicles_user_id", "fleet_vehicles", ["user_id"])
    op.create_table(
        "fleet_mileages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), sa.ForeignKey("fleet_vehicles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mileage", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("image_path", sa.String(500)),
        sa.Column("note", sa.Text()),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("mileage >= 0", name="fleet_mileage_nonnegative"),
    )
    op.create_index("ix_fleet_mileages_user_id", "fleet_mileages", ["user_id"])
    op.create_index("ix_fleet_mileages_vehicle_id", "fleet_mileages", ["vehicle_id"])
    op.create_table(
        "fleet_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), sa.ForeignKey("fleet_vehicles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_type", sa.String(40), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("file_path", sa.String(500)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_fleet_documents_user_id", "fleet_documents", ["user_id"])
    op.create_index("ix_fleet_documents_vehicle_id", "fleet_documents", ["vehicle_id"])
    op.create_index("ix_fleet_documents_expiry_date", "fleet_documents", ["expiry_date"])
    op.create_table(
        "fleet_expenses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), sa.ForeignKey("fleet_vehicles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("garage_name", sa.String(150)),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount > 0", name="fleet_expense_positive"),
        sa.UniqueConstraint("transaction_id"),
    )
    op.create_index("ix_fleet_expenses_user_id", "fleet_expenses", ["user_id"])
    op.create_index("ix_fleet_expenses_vehicle_id", "fleet_expenses", ["vehicle_id"])
    op.create_index("ix_fleet_expenses_transaction_id", "fleet_expenses", ["transaction_id"], unique=True)
    op.create_index("ix_fleet_expenses_expense_date", "fleet_expenses", ["expense_date"])


def downgrade() -> None:
    op.drop_table("fleet_expenses")
    op.drop_table("fleet_documents")
    op.drop_table("fleet_mileages")
    op.drop_table("fleet_vehicles")
