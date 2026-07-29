"""Optional local demo data.

This command refuses to create a known test account unless SEED_DEMO_DATA=true
and requires the caller to supply DEMO_PASSWORD.
"""
import os
from datetime import timedelta

from personal_finance.db import Base, SessionLocal, engine
from personal_finance.local_time import bangkok_today
from personal_finance.models import Account, Category, User
from personal_finance.routers.auth import DEFAULT_CATEGORIES
from personal_finance.security import hash_password
from personal_finance.services.ledger import create_transaction


def seed() -> None:
    if os.getenv("SEED_DEMO_DATA", "").lower() != "true":
        raise SystemExit("Set SEED_DEMO_DATA=true to create local demo data")
    password = os.getenv("DEMO_PASSWORD", "")
    if len(password) < 8:
        raise SystemExit("DEMO_PASSWORD must contain at least 8 characters")
    email = os.getenv("DEMO_EMAIL", "demo@example.local").lower()

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.query(User.id).filter(User.email == email).first():
            print("Demo user already exists")
            return
        user = User(email=email, full_name="ผู้ใช้ตัวอย่าง", password_hash=hash_password(password))
        db.add(user)
        db.flush()
        categories = [Category(user_id=user.id, **item) for item in DEFAULT_CATEGORIES]
        db.add_all(categories)
        db.flush()
        bank = Account(user_id=user.id, name="บัญชีธนาคารตัวอย่าง", type="bank", balance=0)
        cash = Account(user_id=user.id, name="เงินสด", type="cash", balance=0)
        db.add_all([bank, cash])
        db.flush()
        category_ids = {item.name: item.id for item in categories}
        create_transaction(
            db, user_id=user.id, tx_type="income", amount="35000", account_id=bank.id,
            category_id=category_ids["เงินเดือน"], tx_date=bangkok_today() - timedelta(days=5),
            note="ข้อมูลตัวอย่าง: เงินเดือน", source="demo",
        )
        create_transaction(
            db, user_id=user.id, tx_type="expense", amount="120", account_id=cash.id,
            category_id=category_ids["อาหารและเครื่องดื่ม"], tx_date=bangkok_today(),
            note="ข้อมูลตัวอย่าง: ค่าอาหาร", source="demo",
        )
        db.commit()
        print(f"Created demo user: {email}")


if __name__ == "__main__":
    seed()
