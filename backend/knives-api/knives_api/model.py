from typing import Optional
from datetime import datetime
from decimal import Decimal
from sqlalchemy import DECIMAL, Column
from sqlmodel import SQLModel, Field

class Knives(SQLModel, table=True):
    knife_id: Optional[int] = Field(default=None, primary_key=True)
    knife_name: str = Field(index=True, unique=True, max_length=150)
    current_min_price_with_fee: Optional[Decimal] = Field(default=None)
    current_min_price_without_fee: Optional[Decimal] = Field(default=None)
    last_min_price_with_fee: Optional[Decimal] = Field(default=None)
    last_min_price_without_fee: Optional[Decimal] = Field(default=None)
    buy_order_price: Optional[Decimal] = Field(default=None)
    last_updated: Optional[datetime] = Field(default=None)
    last_sold: Optional[datetime] = Field(default=None)
    amount_sold: int = Field(default=0)
    selling_frequency: Decimal = Field(default=Decimal("0.00"), sa_column=Column(DECIMAL(10, 2)))
    knife_image: str = Field(max_length=150)
