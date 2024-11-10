from datetime import datetime
from typing import Any, Optional

class Knife:
    def __init__(
        self,
        knife_name: str,
        knife_id: Optional[str | Any],
        current_min_price_with_fee: Optional[float],
        current_min_price_without_fee: Optional[float],
        last_min_price_with_fee: Optional[float],
        last_min_price_without_fee: Optional[float],
        buy_order_price: Optional[float],
        last_sold: Optional[datetime] = None
    ):
        self.knife_name = knife_name
        self.knife_id = knife_id
        self.current_min_price_with_fee = current_min_price_with_fee
        self.current_min_price_without_fee = current_min_price_without_fee
        self.last_min_price_with_fee = last_min_price_with_fee
        self.last_min_price_without_fee = last_min_price_without_fee
        self.buy_order_price = buy_order_price
        self.last_updated = datetime.now()
        self.last_sold = last_sold