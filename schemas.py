import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


AccountType = Literal["cash", "bank", "credit_card", "investment", "other"]
CategoryType = Literal["income", "expense"]
TransactionType = Literal["income", "expense", "transfer"]


class UserRegister(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=150)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    full_name: str
    line_user_id: str | None = None
    created_at: datetime.datetime


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: AccountType
    balance: Decimal = Field(default=Decimal("0.00"), ge=Decimal("-999999999999.99"), le=Decimal("999999999999.99"))

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("กรุณาระบุชื่อบัญชี")
        return value


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    type: AccountType | None = None


class AccountReconcile(BaseModel):
    actual_balance: Decimal = Field(ge=Decimal("-999999999999.99"), le=Decimal("999999999999.99"))
    note: str | None = Field(default=None, max_length=500)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    type: str
    balance: Decimal
    created_at: datetime.datetime


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: CategoryType
    icon: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9 _-]{1,50}$")
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int | None = None
    name: str
    type: str
    icon: str | None = None
    color: str | None = None


class TransactionCreate(BaseModel):
    type: TransactionType
    amount: Decimal = Field(gt=0, le=Decimal("999999999999.99"))
    category_id: int | None = Field(default=None, gt=0)
    account_id: int = Field(gt=0)
    to_account_id: int | None = Field(default=None, gt=0)
    date: datetime.date = Field(default_factory=datetime.date.today)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_transfer(self):
        if self.type == "transfer":
            if self.to_account_id is None:
                raise ValueError("ต้องระบุบัญชีปลายทาง")
            if self.to_account_id == self.account_id:
                raise ValueError("บัญชีต้นทางและปลายทางต้องไม่ใช่บัญชีเดียวกัน")
            if self.category_id is not None:
                raise ValueError("รายการโอนเงินไม่ใช้หมวดหมู่")
        elif self.to_account_id is not None:
            raise ValueError("บัญชีปลายทางใช้ได้เฉพาะรายการโอนเงิน")
        return self


class TransactionUpdate(BaseModel):
    type: TransactionType | None = None
    amount: Decimal | None = Field(default=None, gt=0, le=Decimal("999999999999.99"))
    category_id: int | None = Field(default=None, gt=0)
    account_id: int | None = Field(default=None, gt=0)
    to_account_id: int | None = Field(default=None, gt=0)
    date: datetime.date | None = None
    note: str | None = Field(default=None, max_length=1000)


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    type: str
    amount: Decimal
    category_id: int | None = None
    account_id: int
    to_account_id: int | None = None
    date: datetime.date
    note: str | None = None
    receipt_path: str | None = None
    source: str
    created_at: datetime.datetime


class BudgetCreate(BaseModel):
    category_id: int = Field(gt=0)
    limit_amount: Decimal = Field(gt=0, le=Decimal("999999999999.99"))
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2200)


class BudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category_id: int
    limit_amount: Decimal
    month: int
    year: int
    created_at: datetime.datetime


class SavingsGoalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    target_amount: Decimal = Field(gt=0, le=Decimal("999999999999.99"))
    current_amount: Decimal = Field(default=Decimal("0.00"), ge=0, le=Decimal("999999999999.99"))
    target_date: datetime.date


class SavingsGoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    target_amount: Decimal | None = Field(default=None, gt=0, le=Decimal("999999999999.99"))
    current_amount: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"))
    target_date: datetime.date | None = None


class SavingsContribution(BaseModel):
    amount: Decimal = Field(gt=0, le=Decimal("999999999999.99"))


class SavingsGoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    target_amount: Decimal
    current_amount: Decimal
    target_date: datetime.date
    created_at: datetime.datetime
