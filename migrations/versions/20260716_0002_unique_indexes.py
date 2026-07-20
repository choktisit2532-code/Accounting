"""Align unique indexes with ORM metadata.

Revision ID: 20260716_0002
Revises: 20260716_0001
"""
from alembic import op


revision = "20260716_0002"
down_revision = "20260716_0001"
branch_labels = None
depends_on = None


INDEXES = [
    ("ix_users_email", "users", ["email"]),
    ("ix_users_line_user_id", "users", ["line_user_id"]),
    ("ix_line_pair_codes_code", "line_pair_codes", ["code"]),
    ("ix_line_events_event_key", "line_events", ["event_key"]),
    ("ix_pending_line_transactions_event_key", "pending_line_transactions", ["event_key"]),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.drop_index(name, table_name=table)
        op.create_index(name, table, columns, unique=True)


def downgrade() -> None:
    for name, table, columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
        op.create_index(name, table, columns, unique=False)
