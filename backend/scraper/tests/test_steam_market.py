import json
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise

import pytest
from conftest import GROUP_PAGE_KEY

import steam_market as sm

BAYONET = "★ Bayonet | Boreal Forest (Field-Tested)"


@pytest.fixture
def client(fake_steam, logger, monkeypatch):
    """A client with fast rate limits that gives up on a blocked endpoint after 4 seconds."""
    monkeypatch.setattr(sm, "MAX_BLOCKED_SECONDS", 4)

    def create(requests_per_second: float = 50.0) -> sm.SteamMarketClient:
        client = sm.SteamMarketClient(logger)
        for limit in client._limits.values():
            limit.interval = limit.min_interval = 1.0 / requests_per_second
            limit.max_blocked = sm.MAX_BLOCKED_SECONDS
        return client

    return create


def query_params(path: str) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlparse(path).query)


def qp(path: str):
    return json.loads(query_params(path)["qp"][0])


def search_result(*names):
    return [
        {
            "strHash": f"{name} (Factory New)",
            "asset_description": {
                "market_bucket_group_id": f"G{name}",
                "market_bucket_group_name": name,
            },
        }
        for name in names
    ]


# --- Order books and price history -----------------------------------------------------------


def test_order_book_request_and_prices(client, fake_steam):
    book = client().get_order_book(BAYONET)

    assert (book.min_sell_price, book.max_buy_price) == (131.30, 122.21)
    _, path, headers = fake_steam.calls[0]
    assert urllib.parse.urlparse(path).path == "/market/orderbook"
    assert query_params(path)["q"] == ["Load"]
    assert qp(path) == [730, BAYONET]
    assert headers["User-Agent"] == sm.HEADERS["User-Agent"]
    assert "gzip" in headers["Accept-Encoding"]


def test_order_book_without_orders(client, fake_steam):
    fake_steam.reply_json(
        {
            "data": {
                "success": True,
                "data": {
                    "amtMaxBuyOrder": None,
                    "amtMinSellOrder": None,
                    "eCurrency": 3,
                },
            }
        }
    )

    book = client().get_order_book("x")

    assert book.min_sell_price is None
    assert book.max_buy_price is None


def test_order_book_for_unknown_item(client, fake_steam):
    fake_steam.reply_json({"data": {"success": False}})

    market = client()
    with pytest.raises(sm.SteamDataError):
        market.get_order_book("nope")


def test_order_book_in_other_currency(client, fake_steam):
    fake_steam.reply_json(
        {
            "data": {
                "success": True,
                "data": {"amtMaxBuyOrder": 1, "amtMinSellOrder": 2, "eCurrency": 1},
            }
        }
    )

    market = client()
    with pytest.raises(sm.SteamCurrencyError):
        market.get_order_book("usd")


def test_price_history_in_other_currency(client, fake_steam):
    fake_steam.reply_json({"data": {"ecurrency": 1, "prices": []}})

    market = client()
    with pytest.raises(sm.SteamCurrencyError):
        market.get_price_history("usd")


def test_price_history_is_sorted_utc_and_rounded(client, fake_steam):
    fake_steam.reply_json(
        {
            "data": {
                "ecurrency": 3,
                "prices": [
                    {
                        "time": 1760900400,
                        "price_median": 124.70000457763672,
                        "purchases": 2,
                    },
                    {
                        "time": 1406419200,
                        "price_median": 224.4482421875,
                        "purchases": 1,
                    },
                ],
            }
        }
    )

    history = client().get_price_history("a")

    assert [str(sold_at) for sold_at, _, _ in history] == [
        "2014-07-27 00:00:00",
        "2025-10-19 19:00:00",
    ]
    assert history[1][1:] == (124.7, 2)


@pytest.mark.parametrize(
    "body",
    [
        '{"data": null}',
        '{"data": {"ecurrency": 3, "prices": [{"time": "x"}]}}',
    ],
)
def test_unusable_price_history(client, fake_steam, body):
    fake_steam.reply((200, body))

    market = client()
    with pytest.raises(sm.SteamDataError):
        market.get_price_history("a")


@pytest.mark.parametrize("body", ["<html>nope", "[1,2]"])
def test_unexpected_response(client, fake_steam, body):
    fake_steam.reply((200, body))

    market = client()
    with pytest.raises(sm.SteamDataError):
        market.get_order_book("a")


def test_item_exists_only_on_explicit_success(client, fake_steam):
    fake_steam.reply_json(
        {"data": {"success": True, "data": {"eCurrency": 3}}},
        {"data": {"success": False}},
        {"data": None},
        {"data": {"success": 1}},
    )
    market = client()

    assert [market.item_exists(name) for name in "abcd"] == [True, False, False, False]
    assert qp(fake_steam.calls[0][1]) == [730, "a"]


# --- Group pages -----------------------------------------------------------------------------


def test_group_page(client, fake_steam, group_page):
    fake_steam.reply((200, group_page))

    group = client().get_knife_group(GROUP_PAGE_KEY, "★ Bayonet | Boreal Forest")

    assert fake_steam.calls[0][1] == f"/market/listings/730/{GROUP_PAGE_KEY}"
    assert len(group.items) == 10
    assert BAYONET in group.items
    assert "★ StatTrak™ Bayonet | Boreal Forest (Factory New)" in group.items
    for item in group.items.values():
        assert item.history
        assert item.image.startswith(
            "https://community.fastly.steamstatic.com/economy/image/"
        )


@pytest.mark.parametrize(
    "body",
    [
        "<html>new layout</html>",
        '<script type="application/json" id="valve-ssr-data">{"broken"</script>',
        '<script type="application/json" id="valve-ssr-data">{"renderContext": {"queryData": "{\\"queries\\": []}"}}</script>',
    ],
)
def test_unusable_group_page(client, fake_steam, body):
    fake_steam.reply((200, body))

    market = client()
    with pytest.raises(sm.SteamDataError):
        market.get_knife_group("G1", "x")


# --- Search ----------------------------------------------------------------------------------


def test_search_paging(client, fake_steam):
    fake_steam.reply_json(
        {"data": {"total_count": 103, "results": search_result("A", "B")}},
        {
            "data": {
                "total_count": 103,
                "results": [{"strHash": "★ C", "asset_description": {}}],
            }
        },
    )

    groups, total = client()._search_all(sm.KNIFE_FILTERS)

    assert groups == {"GA": "A", "GB": "B", "★ C": "★ C"}
    assert total == 103
    assert [qp(path)[0]["start"] for _, path, _ in fake_steam.calls] == [0, 100]


FACETS = {
    "success": True,
    "facets": {
        "Weapon": {
            "tags": {
                "weapon_ak47": {},
                "weapon_bayonet": {},
                "weapon_knife_karambit": {},
                "weapon_knife_new_one": {},
            }
        }
    },
}


def test_search_per_knife_type(client, fake_steam):
    fake_steam.reply_json(
        {"data": {"total_count": 4, "results": search_result("x")}},
        FACETS,
        {"data": {"total_count": 1, "results": search_result("B1")}},
        {"data": {"total_count": 2, "results": search_result("K1", "K2")}},
        {"data": {"total_count": 1, "results": search_result("N1")}},
    )

    groups = client().search_knife_groups()

    assert set(groups) == {"GB1", "GK1", "GK2", "GN1"}
    searched = [
        qp(path)[0]["filters"].get("Weapon")
        for _, path, _ in fake_steam.calls
        if "Search" in path
    ]
    # Guns are skipped, knife types Steam adds later are picked up.
    assert searched == [
        None,
        ["weapon_bayonet"],
        ["weapon_knife_karambit"],
        ["weapon_knife_new_one"],
    ]


def test_search_falls_back_to_all_knives_when_groups_are_missing(client, fake_steam):
    fake_steam.reply_json(
        {"data": {"total_count": 3, "results": search_result("x")}},
        FACETS,
        {"data": {"total_count": 1, "results": search_result("B1")}},
        {"data": {"total_count": 0, "results": []}},
        {"data": {"total_count": 0, "results": []}},
        {"data": {"total_count": 3, "results": search_result("B1", "Z1", "Z2")}},
    )

    assert set(client().search_knife_groups()) == {"GB1", "GZ1", "GZ2"}


def test_search_without_knife_types(client, fake_steam):
    fake_steam.reply_json({"data": {"total_count": 2, "results": search_result("x")}})
    fake_steam.reply(*[(500, "")] * sm.MAX_ATTEMPTS)
    fake_steam.reply_json(
        {"data": {"total_count": 2, "results": search_result("A1", "A2")}}
    )

    assert set(client().search_knife_groups()) == {"GA1", "GA2"}


def test_failing_knife_type_search_keeps_the_others(client, fake_steam):
    fake_steam.reply_json(
        {"data": {"total_count": 2, "results": search_result("x")}},
        FACETS,
        {"data": {"total_count": 1, "results": search_result("B1")}},
    )
    fake_steam.reply((404, ""))
    fake_steam.reply_json({"data": {"total_count": 1, "results": search_result("N1")}})

    assert set(client().search_knife_groups()) == {"GB1", "GN1"}


# --- Errors, retries and rate limits ---------------------------------------------------------


def test_rejected_requests_wait_and_retry(client, fake_steam):
    fake_steam.reply((429, ""), (429, ""))
    start = time.monotonic()

    book = client().get_order_book("a")

    elapsed = time.monotonic() - start
    assert book.max_buy_price == 122.21
    assert len(fake_steam.calls) == 3
    assert 2.5 < elapsed < 5  # cooldowns of 1s then 2s


def test_403_is_a_block_and_success_resets_the_cooldowns(client, fake_steam):
    market = client()
    fake_steam.reply((403, ""))
    market.get_order_book("a")
    fake_steam.reply((403, ""))
    start = time.monotonic()

    market.get_order_book("b")

    assert 0.8 < time.monotonic() - start < 1.8


def test_endpoint_blocked_for_good(client, fake_steam):
    fake_steam.reply(*[(429, "")] * 100)
    market = client()
    start = time.monotonic()

    with pytest.raises(sm.SteamUnavailableError) as error:
        market.get_order_book("a")

    assert error.value.endpoint == "orderbook"
    assert 4 <= time.monotonic() - start < 8


def test_server_errors_are_retried(client, fake_steam):
    fake_steam.reply((502, ""), (500, ""))

    client().get_order_book("a")

    assert len(fake_steam.calls) == 3


def test_server_errors_give_up(client, fake_steam):
    fake_steam.reply(*[(500, "")] * 10)

    market = client()
    with pytest.raises(sm.SteamDataError):
        market.get_order_book("a")

    assert len(fake_steam.calls) == sm.MAX_ATTEMPTS


def test_client_errors_are_not_retried(client, fake_steam):
    fake_steam.reply((404, ""))

    market = client()
    with pytest.raises(sm.SteamDataError):
        market.get_order_book("a")

    assert len(fake_steam.calls) == 1


def test_dropped_connection_is_retried(client, fake_steam):
    fake_steam.reply(("drop", ""))

    client().get_order_book("a")

    assert len(fake_steam.calls) == 2


def test_rate_limit_is_shared_by_threads(client, fake_steam):
    market = client(requests_per_second=10)

    with ThreadPoolExecutor(4) as executor:
        list(executor.map(lambda i: market.get_order_book(str(i)), range(20)))

    times = [call[0] for call in fake_steam.calls]
    assert times[-1] - times[0] >= 1.8
    assert min(b - a for a, b in pairwise(times)) > 0.07


def test_simultaneous_rejections_start_one_cooldown(client, fake_steam):
    market = client(requests_per_second=100)
    fake_steam.reply(*[("slow429", "")] * 4)
    start = time.monotonic()

    with ThreadPoolExecutor(4) as executor:
        list(executor.map(lambda i: market.get_order_book(str(i)), range(4)))

    assert time.monotonic() - start < 2.5
    assert len(fake_steam.calls) == 8


def test_blocked_order_books_dont_pause_group_pages(client, fake_steam, group_page):
    market = client()
    fake_steam.reply((429, ""), (200, group_page))
    start = time.monotonic()

    with ThreadPoolExecutor(2) as executor:
        book = executor.submit(market.get_order_book, "a")
        time.sleep(0.2)
        executor.submit(market.get_knife_group, "G1", "g").result()
        page_time = time.monotonic() - start
        book.result()
        book_time = time.monotonic() - start

    assert page_time < 0.8
    assert book_time >= 1.0


def test_blocked_search_doesnt_pause_price_history(client, fake_steam):
    market = client()
    fake_steam.reply((429, ""))
    fake_steam.reply_json(
        {"data": {"ecurrency": 3, "prices": []}},
        {"data": {"total_count": 0, "results": []}},
    )

    with ThreadPoolExecutor(2) as executor:
        search = executor.submit(market._search, sm.KNIFE_FILTERS, 0, 1)
        time.sleep(0.2)
        start = time.monotonic()
        market.get_price_history("a")
        elapsed = time.monotonic() - start
        search.result()

    assert elapsed < 0.5


def test_adaptive_rate(client, fake_steam):
    market = client()
    limit = market._limits["orderbook"]
    limit.interval, limit.min_interval = 0.01, 0.005

    fake_steam.reply((429, ""))
    market.get_order_book("a")
    assert limit.interval == pytest.approx(0.02)  # twice as slow after a block

    for _ in range(sm.SPEED_UP_AFTER - 1):
        market.get_order_book("a")
    # 15% faster after SPEED_UP_AFTER successes
    assert limit.interval == pytest.approx(0.017)

    fake_steam.reply((429, ""), (429, ""))
    market.get_order_book("a")
    assert limit.interval == pytest.approx(0.034)  # one block only slows down once

    for _ in range(sm.SPEED_UP_AFTER * 20):
        market._on_success(limit)
    assert limit.interval == 0.005

    limit.interval, limit.blocked_until = 20, 0
    market._on_blocked(limit, 429, "x")
    assert limit.interval == sm.MAX_INTERVAL
