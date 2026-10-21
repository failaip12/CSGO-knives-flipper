import json
import urllib.parse
from typing import Any

import pytest
import requests

import buy_order_maxer as bom
import steam_account as sa
from steam_market import OrderBook, SteamCurrencyError, SteamDataError

KARAMBIT = "★ Karambit | Doppler (Factory New)"
BAYONET = "★ StatTrak™ Bayonet | Autotronic (Minimal Wear)"
GLOVES = "★ Sport Gloves | Vice (Field-Tested)"


def buy_order_row(order_id: str, name: str, price: str, quantity: int = 1) -> str:
    """A buy order row the way the old market page renders it."""
    href = f"https://steamcommunity.com/market/listings/730/{urllib.parse.quote(name)}"
    return f"""
    <div class="market_listing_row market_recent_listing_row" id="mybuyorder_{order_id}">
        <img id="mybuyorder_{order_id}_image" src="https://community.fastly.steamstatic.com/economy/image/abc/38fx38f" class="market_listing_item_img" alt="">
        <div class="market_listing_right_cell market_listing_edit_buttons">
            <a href="javascript:CancelMarketBuyOrder('{order_id}')" class="item_market_action_button">Cancel</a>
        </div>
        <div class="market_listing_right_cell market_listing_my_price market_listing_buyorder_qty">
            <span class="market_table_value"><span class="market_listing_price">{quantity}</span></span>
        </div>
        <div class="market_listing_right_cell market_listing_my_price">
            <span class="market_table_value"><span class="market_listing_price">{quantity} @ {price}</span></span>
        </div>
        <div class="market_listing_item_name_block">
            <span class="market_listing_item_name"><a class="market_listing_item_name_link" href="{href}">{name} (translated)</a></span><br>
            <span class="market_listing_game_name">Counter-Strike 2</span>
        </div>
    </div>"""


def market_page(
    *rows: str,
    balance: int = 50000,
    currency: int = 3,
    steam_id: str = '"76561198000000000"',
) -> str:
    wallet = json.dumps({"wallet_currency": currency, "wallet_balance": str(balance)})
    return f"""<html><script>
    g_steamID = {steam_id};
    var g_rgWalletInfo = {wallet};
    </script>
    <div id="myListings">
        <div class="my_listing_section market_content_block market_home_listing_table">
            <h3 class="my_market_header">My buy orders</h3>
            {"".join(rows)}
        </div>
    </div></html>"""


def response(status: int, body: Any) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = (body if isinstance(body, str) else json.dumps(body)).encode()
    return result


class FakeSession(requests.Session):
    def __init__(self, page: str = ""):
        super().__init__()
        self.page = page
        self.posts: list[tuple[str, dict]] = []
        self.answers: list[requests.Response] = []

    def get(self, url, **kwargs):  # type: ignore[override]
        self.cookies.set("sessionid", "abc123", domain="steamcommunity.com")
        return response(200, self.page)

    def post(self, url, data=None, **kwargs):  # type: ignore[override]
        self.posts.append((urllib.parse.urlparse(url).path, dict(data or {})))
        return self.answers.pop(0)


@pytest.fixture
def account(logger, monkeypatch):
    monkeypatch.setattr(sa, "CONFIRMATION_POLL_INTERVAL", 0)

    def create(page: str = "") -> tuple[sa.SteamAccount, FakeSession]:
        session = FakeSession(page)
        return sa.SteamAccount("cookie", logger, session), session

    return create


# --- Parsing the market page -----------------------------------------------------------


@pytest.mark.parametrize(
    "text, cents",
    [
        ("224,60€", 22460),
        ("224,6€", 22460),
        ("224,--€", 22400),
        ("1.234,56€", 123456),
        ("1 234,56€", 123456),
        ("1,234.56", 123456),
        ("1.234€", 123400),
        ("250", 25000),
        ("250.5", 25050),
        (" 0,03€ ", 3),
    ],
)
def test_parse_price(text, cents):
    assert sa.parse_price(text) == cents


def test_parse_price_rejects_text_without_a_number():
    with pytest.raises(ValueError):
        sa.parse_price("abc")


def test_market_home_buy_orders_and_wallet():
    page = market_page(
        buy_order_row("111", KARAMBIT, "1.234,56€"),
        buy_order_row("222", BAYONET, "224,--€", quantity=2),
        balance=123456,
    )

    home = sa.parse_market_home(page)

    assert home.wallet_balance == 123456
    assert home.buy_orders == [
        sa.BuyOrder("111", KARAMBIT, 730, 1, 123456),
        sa.BuyOrder("222", BAYONET, 730, 2, 22400),
    ]


def test_market_home_without_buy_orders():
    assert sa.parse_market_home(market_page()).buy_orders == []


def test_market_home_when_logged_out():
    with pytest.raises(sa.SteamLoginError):
        sa.parse_market_home(market_page(steam_id="false"))


def test_market_home_in_another_currency():
    with pytest.raises(SteamCurrencyError):
        sa.parse_market_home(market_page(currency=1))


def test_market_home_without_listings_section():
    page = market_page().replace('id="myListings"', 'id="somethingElse"')

    with pytest.raises(sa.SteamAccountError):
        sa.parse_market_home(page)


def test_account_asks_for_the_old_market_page(account):
    steam, session = account(market_page())

    steam.load_market_home()

    assert session.cookies.get("bMarketOptOut", path="/market") == "1"
    assert session.cookies.get("steamLoginSecure") == "cookie"


# --- Placing and canceling buy orders ------------------------------------------------------


def test_create_buy_order(account):
    steam, session = account(market_page())
    session.answers = [response(200, {"success": 1, "buy_orderid": "999"})]

    assert steam.create_buy_order(KARAMBIT, 22461, 2) == "999"

    path, data = session.posts[0]
    assert path == "/market/createbuyorder/"
    assert data["sessionid"] == "abc123"
    assert data["market_hash_name"] == KARAMBIT
    assert (data["price_total"], data["quantity"], data["currency"]) == (44922, 2, 3)


def test_create_buy_order_waits_for_mobile_confirmation(account):
    steam, session = account(market_page())
    need_confirmation = {
        "success": 0,
        "need_confirmation": True,
        "confirmation": {"confirmation_id": "4242"},
    }
    session.answers = [
        response(406, need_confirmation),
        response(406, need_confirmation),
        response(200, {"success": 1, "buy_orderid": "999"}),
    ]

    assert steam.create_buy_order(KARAMBIT, 100) == "999"

    assert [data["confirmation"] for _, data in session.posts] == [0, "4242", "4242"]


def test_create_buy_order_gives_up_on_unconfirmed_order(account, monkeypatch):
    monkeypatch.setattr(sa, "CONFIRMATION_TIMEOUT", 0)
    steam, session = account(market_page())
    need_confirmation = {
        "success": 0,
        "need_confirmation": True,
        "confirmation": {"confirmation_id": "4242"},
    }
    session.answers = [response(406, need_confirmation)] * 3

    with pytest.raises(sa.SteamAccountError, match="confirmed"):
        steam.create_buy_order(KARAMBIT, 100)


def test_create_buy_order_refused(account):
    steam, session = account(market_page())
    session.answers = [
        response(
            500, {"success": 29, "message": "You already have an active buy order"}
        )
    ]

    with pytest.raises(sa.SteamAccountError, match="already have"):
        steam.create_buy_order(KARAMBIT, 100)


def test_cancel_buy_order(account):
    steam, session = account(market_page())
    session.answers = [response(200, {"success": 1}), response(200, {"success": 8})]

    steam.cancel_buy_order("111")
    with pytest.raises(sa.SteamAccountError):
        steam.cancel_buy_order("222")

    assert session.posts[0] == (
        "/market/cancelbuyorder/",
        {"sessionid": "abc123", "buy_orderid": "111"},
    )


# --- Deciding what to do ---------------------------------------------------------------------


def order(price: int = 10000, quantity: int = 1, name: str = KARAMBIT) -> sa.BuyOrder:
    return sa.BuyOrder("111", name, 730, quantity, price)


@pytest.mark.parametrize(
    "highest, wallet, expected",
    [
        (None, 50000, (bom.Decision.KEEP, 10000)),
        (9000, 50000, (bom.Decision.KEEP, 10000)),
        (10000, 50000, (bom.Decision.KEEP, 10000)),
        (10500, 50000, (bom.Decision.OUTBID, 10501)),
        (11999, 50000, (bom.Decision.OUTBID, 12000)),
        (12000, 50000, (bom.Decision.OVER_MAX, 12001)),
        (10500, 10500, (bom.Decision.NO_FUNDS, 10501)),
    ],
)
def test_decide(highest, wallet, expected):
    settings = bom.OrderSettings(12000, bom.CANCEL)

    assert bom.decide(order(), settings, highest, wallet) == expected


def test_decide_counts_every_item_of_the_order_against_the_wallet():
    settings = bom.OrderSettings(12000, bom.CANCEL)

    assert bom.decide(order(quantity=3), settings, 10500, 30000)[0] == (
        bom.Decision.NO_FUNDS
    )


def test_only_knives_are_watched():
    assert bom.is_knife(order(name=KARAMBIT))
    assert bom.is_knife(order(name=BAYONET))
    assert not bom.is_knife(order(name=GLOVES))
    assert not bom.is_knife(sa.BuyOrder("1", KARAMBIT, 440, 1, 100))


# --- Settings file ---------------------------------------------------------------------------


def test_settings_round_trip(tmp_path, logger):
    path = str(tmp_path / "data.csv")
    settings = {
        KARAMBIT: bom.OrderSettings(123456, bom.CANCEL),
        BAYONET: bom.OrderSettings(22460, bom.IGNORE),
    }

    bom.save_settings(path, settings)

    assert bom.load_settings(path, logger) == settings


def test_settings_from_the_old_file_format(tmp_path, logger):
    path = tmp_path / "data.csv"
    path.write_text(
        "price,order_id,item_name,max_price,action\n"
        f"224.6,111,{KARAMBIT},250.0,cancel\n"
        f"100,222,{BAYONET},abc,ignore\n",
        encoding="utf-8",
    )

    assert bom.load_settings(str(path), logger) == {
        KARAMBIT: bom.OrderSettings(25000, bom.CANCEL)
    }


def test_sync_settings_drops_gone_orders_and_asks_for_new_ones(logger, monkeypatch):
    answers = iter(["99", "130,5", "x", "i"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    settings = {GLOVES: bom.OrderSettings(1, bom.CANCEL)}

    synced = bom.sync_settings([order(price=10000)], settings, logger, ask=True)

    assert synced == {KARAMBIT: bom.OrderSettings(13050, bom.IGNORE)}


def test_sync_settings_skips_new_orders_without_asking(logger, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("asked"))

    assert bom.sync_settings([order()], {}, logger, ask=False) == {}


# --- Updating orders ---------------------------------------------------------------------


class FakeAccount:
    def __init__(self, home: sa.MarketHome | None = None, fail_creates: int = 0):
        self.home = home
        self.fail_creates = fail_creates
        self.actions: list[tuple] = []

    def load_market_home(self) -> sa.MarketHome:
        assert self.home is not None
        return self.home

    def cancel_buy_order(self, order_id: str) -> None:
        self.actions.append(("cancel", order_id))

    def create_buy_order(self, name: str, price: int, quantity: int = 1) -> str:
        self.actions.append(("create", name, price, quantity))
        if self.fail_creates:
            self.fail_creates -= 1
            raise sa.SteamAccountError("refused")
        return "999"


class FakeMarket:
    def __init__(self, books: dict[str, OrderBook | Exception]):
        self.books = books

    def get_order_book(self, name: str) -> OrderBook:
        book = self.books[name]
        if isinstance(book, Exception):
            raise book
        return book


def update(account, highest, settings, wallet=50000, quantity=1, logger=None):
    market = FakeMarket({KARAMBIT: OrderBook(200.0, highest)})
    return bom.update_order(
        account,
        market,  # type: ignore[arg-type]
        order(quantity=quantity),
        settings,
        wallet,
        logger,
    )


def test_outbids_a_higher_buy_order(logger):
    account = FakeAccount()

    kept = update(
        account, 105.0, bom.OrderSettings(12000, bom.CANCEL), quantity=2, logger=logger
    )

    assert kept
    assert account.actions == [("cancel", "111"), ("create", KARAMBIT, 10501, 2)]


def test_outbid_prices_are_exact_cents(logger):
    account = FakeAccount()

    update(account, 224.6, bom.OrderSettings(30000, bom.CANCEL), logger=logger)

    assert account.actions[-1] == ("create", KARAMBIT, 22461, 1)


def test_places_the_old_order_again_if_the_new_one_fails(logger):
    account = FakeAccount(fail_creates=1)

    update(account, 105.0, bom.OrderSettings(12000, bom.CANCEL), logger=logger)

    assert account.actions == [
        ("cancel", "111"),
        ("create", KARAMBIT, 10501, 1),
        ("create", KARAMBIT, 10000, 1),
    ]


def test_keeps_the_highest_order(logger):
    account = FakeAccount()

    assert update(account, 100.0, bom.OrderSettings(12000, bom.CANCEL), logger=logger)
    assert account.actions == []


def test_cancels_when_outbid_above_the_maximum(logger):
    account = FakeAccount()

    kept = update(account, 120.0, bom.OrderSettings(12000, bom.CANCEL), logger=logger)

    assert not kept
    assert account.actions == [("cancel", "111")]


def test_ignores_being_outbid_above_the_maximum(logger):
    account = FakeAccount()

    assert update(account, 120.0, bom.OrderSettings(12000, bom.IGNORE), logger=logger)
    assert account.actions == []


def test_doesnt_outbid_without_funds(logger):
    account = FakeAccount()

    assert update(
        account,
        105.0,
        bom.OrderSettings(12000, bom.CANCEL),
        wallet=10000,
        logger=logger,
    )
    assert account.actions == []


class StopLoop(Exception):
    pass


def test_one_round(tmp_path, logger, monkeypatch):
    path = tmp_path / "data.csv"
    bom.save_settings(
        str(path),
        {
            KARAMBIT: bom.OrderSettings(12000, bom.CANCEL),
            BAYONET: bom.OrderSettings(30000, bom.CANCEL),
            "★ Gone Knife": bom.OrderSettings(100, bom.IGNORE),
        },
    )
    home = sa.MarketHome(
        50000,
        [
            sa.BuyOrder("111", KARAMBIT, 730, 1, 10000),
            sa.BuyOrder("222", BAYONET, 730, 1, 20000),
            sa.BuyOrder("333", GLOVES, 730, 1, 5000),
        ],
    )
    account = FakeAccount(home)
    market = FakeMarket(
        {KARAMBIT: OrderBook(None, 130.0), BAYONET: SteamDataError("no book")}
    )
    monkeypatch.setattr(bom, "SteamMarketClient", lambda logger: market)

    def stop(seconds):
        raise StopLoop

    monkeypatch.setattr(bom.time, "sleep", stop)

    with pytest.raises(StopLoop):
        bom.run(account, str(path), logger)  # type: ignore[arg-type]

    assert account.actions == [("cancel", "111")]
    assert bom.load_settings(str(path), logger) == {
        BAYONET: bom.OrderSettings(30000, bom.CANCEL)
    }
