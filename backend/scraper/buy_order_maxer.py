"""
Keeps your knife buy orders on top of the Steam market. Every few minutes it looks up the
highest buy order of every knife you have a buy order on, and when someone outbid you it
replaces your order with one a cent above theirs, as long as that stays within the maximum
price you set for the knife.

Needs STEAM_COOKIE_STEAM_LOGIN_SECURE in backend/scraper/.env: the value of the
steamLoginSecure cookie of a browser that is logged in to steamcommunity.com.
Your maximum prices are kept in data.csv, you're asked for them the first time an order shows up.
"""

import csv
import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

from CustomLogger import CustomLogger, LogLevel
from steam_account import (
    BuyOrder,
    SteamAccount,
    SteamAccountError,
    SteamLoginError,
    format_price,
    parse_price,
)
from steam_market import (
    APPID,
    SteamCurrencyError,
    SteamDataError,
    SteamMarketClient,
    SteamUnavailableError,
)

SETTINGS_FILE = "data.csv"
SLEEP_SECONDS = 300
KNIFE_KEYWORDS = ["knife", "bayonet", "karambit", "shadow daggers"]
CANCEL = "cancel"
IGNORE = "ignore"


@dataclass
class OrderSettings:
    max_price: int  # In cents.
    action: str  # CANCEL or IGNORE, what to do once you're outbid above max_price.


class Decision(Enum):
    KEEP = "keep"  # Your order is the highest.
    OUTBID = "outbid"
    OVER_MAX = "over_max"  # Outbidding would cost more than your maximum.
    NO_FUNDS = "no_funds"  # Outbidding would cost more than your wallet holds.


def is_knife(order: BuyOrder) -> bool:
    name = order.item_name.lower()
    return order.appid == APPID and any(keyword in name for keyword in KNIFE_KEYWORDS)


def decide(
    order: BuyOrder, settings: OrderSettings, highest: int | None, wallet: int
) -> tuple[Decision, int]:
    """What to do with the order given the highest buy order on the market, and at what price."""
    if highest is None or highest <= order.price:
        return Decision.KEEP, order.price
    price = highest + 1
    if price > settings.max_price:
        return Decision.OVER_MAX, price
    if price * order.quantity > wallet:
        return Decision.NO_FUNDS, price
    return Decision.OUTBID, price


def load_settings(path: str, logger: CustomLogger) -> dict[str, OrderSettings]:
    if not os.path.exists(path):
        return {}
    settings = {}
    with open(path, encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            try:
                action = row["action"]
                if action not in (CANCEL, IGNORE):
                    raise ValueError(f"unknown action {action!r}")
                settings[row["item_name"]] = OrderSettings(
                    parse_price(row["max_price"]), action
                )
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"Ignoring row {row} of {path}: {e}")
    return settings


def save_settings(path: str, settings: dict[str, OrderSettings]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["item_name", "max_price", "action"])
        for name, order_settings in sorted(settings.items()):
            writer.writerow(
                [name, f"{order_settings.max_price / 100:.2f}", order_settings.action]
            )


def ask_max_price(order: BuyOrder) -> int:
    while True:
        answer = input(
            f"Maximum price for {order.item_name}, your buy order is at {format_price(order.price)}: "
        )
        try:
            max_price = parse_price(answer)
        except ValueError:
            print("Please enter a price, e.g. 250.50")
            continue
        if max_price >= order.price:
            return max_price
        print(f"Please enter at least {format_price(order.price)}.")


def ask_action() -> str:
    while True:
        answer = input(
            "Once someone outbids your maximum: C to cancel your buy order, I to keep it: "
        )
        answer = answer.strip().lower()
        if answer == "c":
            return CANCEL
        if answer == "i":
            return IGNORE
        print("Please enter C or I.")


def sync_settings(
    orders: list[BuyOrder],
    settings: dict[str, OrderSettings],
    logger: CustomLogger,
    ask: bool,
) -> dict[str, OrderSettings]:
    """Settings for exactly the given orders. Asks for missing ones if ask is set."""
    names = {order.item_name for order in orders}
    for name in settings.keys() - names:
        logger.info(
            f"You no longer have a buy order on {name}, it was bought or canceled."
        )
    synced = {name: s for name, s in settings.items() if name in names}
    for order in orders:
        if order.item_name in synced:
            continue
        if ask:
            synced[order.item_name] = OrderSettings(ask_max_price(order), ask_action())
        else:
            logger.warning(
                f"New buy order on {order.item_name}, restart to set its maximum price."
            )
    return synced


def replace_order(
    account: SteamAccount, order: BuyOrder, price: int, logger: CustomLogger
) -> None:
    # Steam allows only one buy order per item, so the old one has to go first.
    account.cancel_buy_order(order.order_id)
    try:
        account.create_buy_order(order.item_name, price, order.quantity)
    except SteamAccountError as e:
        logger.error(f"{e}, placing your old buy order again.")
        try:
            account.create_buy_order(order.item_name, order.price, order.quantity)
        except SteamAccountError as e:
            logger.error(
                f"Your buy order on {order.item_name} was canceled and couldn't be placed again: {e}"
            )
        return
    logger.info(
        f"Raised your buy order on {order.item_name} from {format_price(order.price)} to {format_price(price)}."
    )


def update_order(
    account: SteamAccount,
    market: SteamMarketClient,
    order: BuyOrder,
    settings: OrderSettings,
    wallet: int,
    logger: CustomLogger,
) -> bool:
    """Outbids whoever is above the order if allowed. Returns False if the order was canceled."""
    book = market.get_order_book(order.item_name)
    highest = round(book.max_buy_price * 100) if book.max_buy_price else None
    decision, price = decide(order, settings, highest, wallet)
    name = order.item_name
    if decision is Decision.OUTBID:
        replace_order(account, order, price, logger)
    elif decision is Decision.NO_FUNDS:
        logger.warning(
            f"Can't outbid the {format_price(price - 1)} buy order on {name}, your wallet only holds {format_price(wallet)}."
        )
    elif decision is Decision.OVER_MAX:
        over_max = f"The highest buy order on {name} is {format_price(price - 1)}, above your maximum of {format_price(settings.max_price)}"
        if settings.action == IGNORE:
            logger.info(f"{over_max}, keeping your order.")
            return True
        account.cancel_buy_order(order.order_id)
        logger.info(f"{over_max}, canceled your order.")
        return False
    return True


def run(account: SteamAccount, settings_file: str, logger: CustomLogger) -> None:
    settings = load_settings(settings_file, logger)
    market = SteamMarketClient(logger)
    first_run = True
    while True:
        try:
            home = account.load_market_home()
        except SteamLoginError:
            raise
        except SteamAccountError as e:
            logger.error(f"{e} Trying again in {SLEEP_SECONDS} seconds.")
            time.sleep(SLEEP_SECONDS)
            continue

        orders = [order for order in home.buy_orders if is_knife(order)]
        settings = sync_settings(orders, settings, logger, ask=first_run)
        save_settings(settings_file, settings)
        if first_run:
            logger.info(
                f"Watching {len(orders)} knife buy orders, wallet: {format_price(home.wallet_balance)}."
            )
            for order in orders:
                if order.item_name in settings:
                    logger.info(
                        f"{order.item_name}: {order.quantity} @ {format_price(order.price)}, "
                        f"maximum {format_price(settings[order.item_name].max_price)}"
                    )
            first_run = False

        for order in orders:
            order_settings = settings.get(order.item_name)
            if order_settings is None:
                continue
            try:
                if not update_order(
                    account, market, order, order_settings, home.wallet_balance, logger
                ):
                    del settings[order.item_name]
                    save_settings(settings_file, settings)
            except SteamDataError as e:
                logger.warning(f"Could not check {order.item_name}: {e}")
            except SteamUnavailableError as e:
                # The client gives up on an endpoint for good, start over with a new one.
                logger.error(f"{e} Skipping the rest of the orders this round.")
                market = SteamMarketClient(logger)
                break
            except SteamLoginError:
                raise
            except SteamAccountError as e:
                logger.error(str(e))

        logger.info(f"Sleeping for {SLEEP_SECONDS} seconds.")
        time.sleep(SLEEP_SECONDS)


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    logger = CustomLogger(log_file="order_maxer.log", log_level=LogLevel.INFO)
    login_secure = os.environ.get("STEAM_COOKIE_STEAM_LOGIN_SECURE")
    if not login_secure:
        logger.fatal(
            "STEAM_COOKIE_STEAM_LOGIN_SECURE isn't set, add your steamLoginSecure cookie to backend/scraper/.env."
        )
        sys.exit(1)
    try:
        run(SteamAccount(login_secure, logger), SETTINGS_FILE, logger)
    except KeyboardInterrupt:
        logger.info("Stopped.")
    except (SteamLoginError, SteamCurrencyError) as e:
        logger.fatal(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
