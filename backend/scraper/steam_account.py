import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

from CustomLogger import CustomLogger
from steam_market import APPID, EUR, HEADERS, REQUEST_TIMEOUT, SteamCurrencyError

COMMUNITY_URL = "https://steamcommunity.com"
# Seconds between asking Steam whether a buy order was confirmed in the mobile app.
CONFIRMATION_POLL_INTERVAL = 2.0
CONFIRMATION_TIMEOUT = 120.0

STEAM_ID_RE = re.compile(r'g_steamID = "(\d+)";')
WALLET_RE = re.compile(r"var g_rgWalletInfo = (\{.*?\});")
BUY_ORDER_ID_RE = re.compile(r"mybuyorder_(\d+)$")


class SteamAccountError(Exception):
    """Steam refused or failed a request made with the account."""


class SteamLoginError(SteamAccountError):
    """The login cookie is missing, wrong or expired."""


@dataclass
class BuyOrder:
    order_id: str
    item_name: str
    appid: int | None
    quantity: int
    price: int  # Per item, in cents.


@dataclass
class MarketHome:
    wallet_balance: int  # In cents.
    buy_orders: list[BuyOrder]


def parse_price(text: str) -> int:
    """Steam's formatted price ('1.234,56€', '224,--€', '12.50€') in cents."""
    number = re.sub(r"[^\d,.]", "", text.replace("--", "00"))
    if not re.search(r"\d", number):
        raise ValueError(f"No price in {text!r}")
    match = re.fullmatch(r"(.*?)[,.](\d{1,2})", number)
    whole, fraction = (match.group(1), match.group(2)) if match else (number, "0")
    return int(re.sub(r"\D", "", whole) or "0") * 100 + int(fraction.ljust(2, "0"))


def format_price(cents: int) -> str:
    return f"{cents / 100:.2f}€"


def parse_buy_order(row: Tag) -> BuyOrder:
    match = BUY_ORDER_ID_RE.search(str(row.get("id", "")))
    if not match:
        raise SteamAccountError(f"Buy order row without an id: {row.get('id')}")
    order_id = match.group(1)
    price_text = next(
        (
            span.get_text()
            for span in row.select("span.market_listing_price")
            if "@" in span.get_text()
        ),
        None,
    )
    if price_text is None:
        raise SteamAccountError(f"Buy order {order_id} has no price.")
    quantity, _, price = price_text.partition("@")

    link = row.select_one("a.market_listing_item_name_link") or row.find("a")
    if not isinstance(link, Tag):
        raise SteamAccountError(f"Buy order {order_id} has no item.")
    # The link holds the market hash name, the text is translated to the account's language.
    path = urllib.parse.urlparse(str(link.get("href", ""))).path
    listing = re.search(r"/market/listings/(\d+)/([^/]+)$", path)
    if listing:
        appid: int | None = int(listing.group(1))
        name = urllib.parse.unquote(listing.group(2))
    else:
        appid, name = None, link.get_text().strip()
    return BuyOrder(order_id, name, appid, int(quantity.strip()), parse_price(price))


def parse_market_home(page: str) -> MarketHome:
    if not STEAM_ID_RE.search(page):
        raise SteamLoginError(
            "Steam doesn't recognise the login cookie, it may have expired. "
            "Copy a fresh steamLoginSecure cookie from your browser."
        )
    wallet_match = WALLET_RE.search(page)
    if not wallet_match:
        raise SteamAccountError("The market page has no wallet info.")
    wallet = json.loads(wallet_match.group(1))
    if wallet.get("wallet_currency") != EUR:
        raise SteamCurrencyError(
            f"The wallet is in currency {wallet.get('wallet_currency')} instead of EUR ({EUR})."
        )

    document = BeautifulSoup(page, "html.parser")
    if document.find(id="myListings") is None:
        raise SteamAccountError(
            "The market page has no listings section, Steam may have changed it."
        )
    rows = document.find_all("div", id=BUY_ORDER_ID_RE)
    orders = [parse_buy_order(row) for row in rows if isinstance(row, Tag)]
    return MarketHome(int(wallet["wallet_balance"]), orders)


class SteamAccount:
    """
    Manages the buy orders of a logged in Steam account through the community market.
    Logs in with the steamLoginSecure cookie of a browser session.
    """

    def __init__(
        self,
        login_secure: str,
        logger: CustomLogger,
        session: requests.Session | None = None,
    ):
        if not login_secure:
            raise SteamLoginError("No steamLoginSecure cookie given.")
        self.logger = logger
        self.session = session or requests.Session()
        self.session.headers.clear()
        # Steam answers the market page with 429 to "gzip, deflate" while other values go through.
        self.session.headers.update({**HEADERS, "Accept-Encoding": "gzip"})
        domain = urllib.parse.urlparse(COMMUNITY_URL).hostname
        self.session.cookies.set("steamLoginSecure", login_secure, domain=domain)
        # The React market doesn't show your buy orders and wallet in the page source,
        # opting out of it gets the old market page that does.
        self.session.cookies.set("bMarketOptOut", "1", domain=domain, path="/market")

    def _session_id(self) -> str:
        """Steam hands out the session id that POSTs need with the first page you load."""
        domain = urllib.parse.urlparse(COMMUNITY_URL).hostname
        if not self.session.cookies.get("sessionid", domain=domain):
            self.load_market_home()
        session_id = self.session.cookies.get("sessionid", domain=domain)
        if not session_id:
            raise SteamAccountError("Steam didn't hand out a session id.")
        return session_id

    def load_market_home(self) -> MarketHome:
        """Your wallet balance and active buy orders."""
        try:
            response = self.session.get(
                f"{COMMUNITY_URL}/market/", timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as e:
            raise SteamAccountError(f"Could not load the market page: {e}") from e
        if response.status_code != 200:
            raise SteamAccountError(
                f"Steam returned {response.status_code} for the market page."
            )
        return parse_market_home(response.text)

    def _post(self, path: str, data: dict[str, Any], referer: str) -> tuple[int, Any]:
        """Posts a market action, returns (HTTP status, JSON answer)."""
        try:
            response = self.session.post(
                f"{COMMUNITY_URL}{path}",
                data={"sessionid": self._session_id(), **data},
                headers={"Referer": referer, "Origin": COMMUNITY_URL},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise SteamAccountError(f"Request to {path} failed: {e}") from e
        try:
            return response.status_code, response.json()
        except ValueError as e:
            raise SteamAccountError(
                f"Steam returned {response.status_code} without JSON for {path}."
            ) from e

    def create_buy_order(self, name: str, price: int, quantity: int = 1) -> str:
        """
        Places a buy order for quantity items at price cents each and returns its id.
        If Steam wants it confirmed in the mobile app, waits until it is.
        """
        referer = f"{COMMUNITY_URL}/market/listings/{APPID}/{urllib.parse.quote(name)}"
        data = {
            "currency": EUR,
            "appid": APPID,
            "market_hash_name": name,
            "price_total": price * quantity,
            "tradefee_tax": 0,
            "quantity": quantity,
            "confirmation": 0,
        }
        deadline = None
        while True:
            status, answer = self._post("/market/createbuyorder/", data, referer)
            if not isinstance(answer, dict):
                raise SteamAccountError(f"Unexpected answer to the buy order: {answer}")
            if (
                status == 200
                and answer.get("success") == 1
                and not answer.get("message")
            ):
                return str(answer["buy_orderid"])
            confirmation = (answer.get("confirmation") or {}).get("confirmation_id")
            if not answer.get("need_confirmation") or not confirmation:
                message = answer.get("message") or f"success {answer.get('success')}"
                raise SteamAccountError(
                    f"Steam refused the buy order on {name}: {message}"
                )
            if deadline is None:
                self.logger.warning(
                    f"Confirm the buy order on {name} at {format_price(price)} in the Steam mobile app."
                )
                deadline = time.monotonic() + CONFIRMATION_TIMEOUT
            elif time.monotonic() > deadline:
                raise SteamAccountError(
                    f"The buy order on {name} wasn't confirmed within {CONFIRMATION_TIMEOUT:.0f} seconds."
                )
            data["confirmation"] = confirmation
            time.sleep(CONFIRMATION_POLL_INTERVAL)

    def cancel_buy_order(self, order_id: str) -> None:
        status, answer = self._post(
            "/market/cancelbuyorder/",
            {"buy_orderid": order_id},
            f"{COMMUNITY_URL}/market/",
        )
        if status != 200 or not isinstance(answer, dict) or answer.get("success") != 1:
            raise SteamAccountError(
                f"Steam refused to cancel buy order {order_id}: {answer}"
            )
