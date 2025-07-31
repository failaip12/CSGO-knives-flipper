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
        knife_image: Optional[str] = None,
        last_sold: Optional[datetime] = None,
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
        self.knife_image = knife_image

    # For debugging or detailed inspection (called in interactive environments)
    def __repr__(self):
        return (
            f"Knife(knife_name={self.knife_name}, knife_id={self.knife_id}, "
            f"current_min_price_with_fee={self.current_min_price_with_fee}, "
            f"current_min_price_without_fee={self.current_min_price_without_fee}, "
            f"last_min_price_with_fee={self.last_min_price_with_fee}, "
            f"last_min_price_without_fee={self.last_min_price_without_fee}, "
            f"buy_order_price={self.buy_order_price}, "
            f"last_updated={self.last_updated}, last_sold={self.last_sold})"
            f"knife_image={self.knife_image}"
        )

    # For a user-friendly print output (called by print())
    def __str__(self):
        return (
            f"Knife: {self.knife_name}\n"
            f"ID: {self.knife_id}\n"
            f"Current Price (With Fee): {self.current_min_price_with_fee}\n"
            f"Current Price (Without Fee): {self.current_min_price_without_fee}\n"
            f"Last Min Price (With Fee): {self.last_min_price_with_fee}\n"
            f"Last Min Price (Without Fee): {self.last_min_price_without_fee}\n"
            f"Buy Order Price: {self.buy_order_price}\n"
            f"Last Updated: {self.last_updated}\n"
            f"Last Sold: {self.last_sold}\n"
            f"Knife Image: {self.knife_image}"
        )
