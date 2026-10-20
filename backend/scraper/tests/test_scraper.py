import json
import threading
import time
import urllib.parse
from datetime import datetime

import pytest

import scraper
import steam_market as sm
from conftest import GROUP_PAGE_KEY, ORDER_BOOK


def at(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H")


HISTORY = [
    (at("2026-10-01 00"), 100.0, 1),
    (at("2026-10-12 17"), 110.0, 2),
    (at("2026-10-19 11"), 120.0, 1),
]


class FakeClient:
    """
    Scripted stand-in for SteamMarketClient.
    pages: {group key: {knife name: (image, history or None)}}, books: {name: (ask, bid)},
    histories: standalone price histories, fail_*: {key: how many calls fail first}.
    """

    def __init__(
        self,
        groups,
        pages,
        books,
        histories=None,
        *,
        fail_page=None,
        fail_book=None,
        exists=(),
        currency_error_on=None,
        search_error=False,
        broken_page=None,
        on_book=None,
        delay=0.0,
    ):
        self.groups, self.pages, self.books = groups, pages, books
        self.histories = histories or {}
        self.fail_page, self.fail_book = dict(fail_page or {}), dict(fail_book or {})
        self.exists = set(exists)
        self.currency_error_on, self.search_error = currency_error_on, search_error
        self.broken_page, self.on_book, self.delay = broken_page, on_book, delay
        self.lock = threading.Lock()
        self.calls = []

    def _fails(self, counters, key):
        with self.lock:
            if counters.get(key, 0) > 0:
                counters[key] -= 1
                return True
        return False

    def stop(self):
        pass

    def search_knife_groups(self):
        if self.search_error:
            raise sm.SteamDataError("search down")
        return dict(self.groups)

    def item_exists(self, name):
        self.calls.append(("exists", name))
        return name in self.exists

    def get_knife_group(self, key, name):
        self.calls.append(("page", key))
        if key == self.broken_page:
            raise KeyError("unexpected layout")
        if self._fails(self.fail_page, key) or key not in self.pages:
            raise sm.SteamDataError(f"page {key} failed")
        group = sm.KnifeGroup(key, name)
        for item_name, (image, history) in self.pages[key].items():
            group.items[item_name] = sm.GroupItem(item_name, image, history)
        return group

    def get_order_book(self, name):
        self.calls.append(("book", name))
        time.sleep(self.delay)
        if self.on_book:
            self.on_book(name)
        if name == self.currency_error_on:
            raise sm.SteamCurrencyError("USD")
        if self._fails(self.fail_book, name) or name not in self.books:
            raise sm.SteamDataError(f"book {name} failed")
        return sm.OrderBook(*self.books[name])

    def get_price_history(self, name):
        self.calls.append(("hist", name))
        if name not in self.histories:
            raise sm.SteamDataError(f"history {name} failed")
        return self.histories[name]


# --- Helpers ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, group",
    [
        (
            "★ StatTrak™ Bayonet | Boreal Forest (Field-Tested)",
            "★ Bayonet | Boreal Forest",
        ),
        ("★ Karambit | Doppler (Factory New)", "★ Karambit | Doppler"),
        ("★ StatTrak™ Bayonet", "★ Bayonet"),
        ("★ Bayonet", "★ Bayonet"),
    ],
)
def test_knife_group_name(name, group):
    assert scraper.knife_group_name(name) == group


def test_group_variants():
    assert scraper.group_variants("★ Bayonet") == ["★ Bayonet", "★ StatTrak™ Bayonet"]
    variants = scraper.group_variants("★ Butterfly Knife | Fade")
    assert len(variants) == 10
    assert "★ StatTrak™ Butterfly Knife | Fade (Minimal Wear)" in variants


def test_unlisted_group_candidates():
    known = {"★ A | X", "★ A | Y", "★ B | X", "★ C"}

    assert scraper.unlisted_group_candidates(known) == [
        "★ A",
        "★ B",
        "★ B | Y",
        "★ C | X",
        "★ C | Y",
    ]


# --- Updating knives -------------------------------------------------------------------------

A = "★ A | X (Factory New)"
ST_A = "★ StatTrak™ A | X (Factory New)"
B = "★ B | Y (Minimal Wear)"
C = "★ C"
D = "★ D | Z (Field-Tested)"


def basic_market(**kwargs):
    pages = {
        "G1": {A: ("imgA", HISTORY), ST_A: ("imgST", [])},
        "G2": {B: ("imgB", None)},  # history missing from the page
        "G3": {C: ("imgC", HISTORY[:1])},
        "G4": {D: ("imgD", HISTORY)},
    }
    books = {
        A: (131.3, 122.21),
        ST_A: (None, None),
        B: (50.0, 40.0),
        C: (10.0, None),
        D: (5.0, 4.0),
    }
    groups = {"G1": "★ A | X", "G2": "★ B | Y", "G3": "★ C", "G4": "★ D | Z"}
    return FakeClient(groups, pages, books, {B: HISTORY[1:]}, **kwargs)


def test_first_run_discovers_and_saves_knives(db, run_scraper):
    client = basic_market(fail_page={"G3": 1}, fail_book={A: 1})

    assert run_scraper(client) == []

    assert db.names() == sorted([A, ST_A, B, C, D])
    knife = db.knife(A)
    assert (float(knife["ask"]), float(knife["bid"]), float(knife["last_price"])) == (
        131.3,
        122.21,
        120.0,
    )
    assert knife["last_sold"] == at("2026-10-19 11") and knife["image"] == "imgA"
    assert db.history(knife["knife_id"]) == HISTORY
    assert (knife["amount_sold"], knife["amount_sold_last_year"]) == (3, 4)
    stattrak = db.knife(ST_A)
    assert (
        stattrak["ask"],
        stattrak["bid"],
        stattrak["last_price"],
        stattrak["last_sold"],
    ) == (None, None, None, None)
    # History missing from the group page comes from the standalone endpoint.
    assert ("hist", B) in client.calls and float(db.knife(B)["last_price"]) == 120.0
    # A failed group page is retried, a failed order book is retried on its own.
    assert client.calls.count(("page", "G3")) == 2 and db.knife(C)["ask"] is not None
    assert client.calls.count(("book", A)) == 2


def test_rerun_updates_the_last_hour_and_adds_new_sales(db, run_scraper):
    run_scraper(basic_market())
    newer = HISTORY[:-1] + [
        (at("2026-10-19 11"), 121.0, 2),
        (at("2026-10-19 15"), 125.0, 1),
    ]
    client = basic_market()
    client.pages["G1"][A] = ("imgA2", newer)

    assert run_scraper(client) == []

    knife = db.knife(A)
    assert db.history(knife["knife_id"]) == newer
    assert knife["image"] == "imgA2" and float(knife["last_price"]) == 125.0
    assert db.query("SELECT count(*) FROM SellTimes")[0][0] == 4


def test_data_from_the_old_scraper(db, run_scraper):
    # Old knife_ids are Steam ids and old daily points were stored at 01:00.
    db.query(
        "INSERT INTO knives (knife_id, knife_name, last_sold, last_min_price_with_fee, knife_image) "
        "VALUES (176000123, %s, %s, 110, 'oldimg')",
        A,
        at("2026-10-12 17"),
    )
    db.query(
        "INSERT INTO SellTimes (sell_time) VALUES (%s), (%s)",
        at("2026-09-30 01"),
        at("2026-10-12 17"),
    )
    db.query(
        "INSERT INTO SellHistory (knife_id, sell_time_id, price, quantity) "
        "SELECT 176000123, sell_time_id, 99, 1 FROM SellTimes"
    )
    # The serial sequence is still at 1, so the next inserted knife collides with these ids.
    db.query("INSERT INTO knives (knife_id, knife_name) VALUES (1, 'old-serial-knife')")
    db.query(
        "INSERT INTO knives (knife_id, knife_name, last_sold, last_min_price_with_fee) VALUES (2, %s, %s, 77)",
        "★ Gone | Knife (Well-Worn)",
        at("2025-01-01 00"),
    )
    db.query(
        "INSERT INTO knives (knife_id, knife_name) VALUES (176000999, %s)",
        "★ Hidden | Knife (Field-Tested)",
    )
    pages = {
        "G1": {A: ("imgA", [(at("2026-09-30 00"), 98.0, 1)] + HISTORY)},
        "G2": {B: ("imgB", HISTORY)},
    }
    books = {
        A: (131.3, 122.21),
        B: (50.0, 40.0),
        "★ Hidden | Knife (Field-Tested)": (300.0, 200.0),
    }
    client = FakeClient(
        {"G1": "★ A | X", "G2": "★ B | Y"},
        pages,
        books,
        {"★ Hidden | Knife (Field-Tested)": HISTORY},
    )

    failed = run_scraper(client)

    assert failed == ["old-serial-knife", "★ Gone | Knife (Well-Worn)"]
    knife = db.knife(A)
    assert knife["knife_id"] == 176000123
    # Only sales since the last stored one are added, so old and new daily points don't mix.
    assert [(str(t), p) for t, p, _ in db.history(knife["knife_id"])] == [
        ("2026-09-30 01:00:00", 99.0),
        ("2026-10-12 17:00:00", 110.0),
        ("2026-10-19 11:00:00", 120.0),
    ]
    assert db.knife(B)["knife_id"] not in (1, 2)
    assert float(db.knife("★ Hidden | Knife (Field-Tested)")["ask"]) == 300.0
    gone = db.knife("★ Gone | Knife (Well-Worn)")
    assert float(gone["last_price"]) == 77 and gone["last_sold"] == at("2025-01-01 00")
    assert (
        client.calls.count(("book", "★ Gone | Knife (Well-Worn)"))
        == scraper.MAX_RETRY_COUNT + 1
    )


def test_without_discovery_only_known_knives_are_updated(db, run_scraper):
    db.query("INSERT INTO knives (knife_name) VALUES (%s)", A)
    client = basic_market(exists={"★ A | Y (Factory New)"})

    assert run_scraper(client, discover=False) == []

    assert db.names() == [A] and db.knife(A)["ask"] is not None
    assert not any(call[0] == "exists" for call in client.calls)


def test_search_failure_updates_known_knives(db, run_scraper):
    db.query("INSERT INTO knives (knife_name) VALUES (%s), (%s)", A, B)
    client = FakeClient(
        {},
        {},
        {A: (1.0, 1.0), B: (2.0, 2.0)},
        {A: HISTORY, B: HISTORY},
        search_error=True,
    )

    assert run_scraper(client) == []

    assert db.knife(A)["ask"] is not None and db.knife(B)["ask"] is not None


def many_knives(count):
    names = [f"★ K{i} | S (Factory New)" for i in range(count)]
    pages = {f"G{i}": {name: ("img", HISTORY)} for i, name in enumerate(names)}
    return names, pages, {name: (1.0, 1.0) for name in names}


def test_wrong_currency_stops_the_run_cleanly(db, run_scraper):
    names, pages, books = many_knives(40)
    for name in names:
        db.query("INSERT INTO knives (knife_name) VALUES (%s)", name)
    client = FakeClient(
        {key: key for key in pages},
        pages,
        books,
        currency_error_on=names[25],
        delay=0.05,
    )

    failed = run_scraper(client)

    updated = {
        row[0]
        for row in db.query(
            "SELECT knife_name FROM knives WHERE last_updated IS NOT NULL"
        )
    }
    assert updated and len(updated) < 40
    assert set(failed) == set(names) - updated
    assert db.query("SELECT count(*) FROM knives WHERE amount_sold > 0")[0][0] == len(
        updated
    )
    assert len(client.calls) < 80  # the remaining work was cancelled


def test_lost_database_connection(db, run_scraper):
    names, pages, books = many_knives(40)
    killed = []

    def kill_connections(name):
        if name == names[10] and not killed:
            killed.append(name)
            db.query(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )

    client = FakeClient(
        {key: key for key in pages}, pages, books, on_book=kill_connections
    )

    assert run_scraper(client) == []

    assert killed
    assert (
        db.query("SELECT count(*) FROM knives WHERE last_updated IS NOT NULL")[0][0]
        == 40
    )


def test_knife_that_cant_be_stored_doesnt_affect_others(db, run_scraper):
    names, pages, books = many_knives(20)
    pages["G7"] = {names[7]: ("x" * 500, HISTORY)}  # too long for knife_image

    assert run_scraper(FakeClient({key: key for key in pages}, pages, books)) == [
        names[7]
    ]

    assert (
        db.query("SELECT count(*) FROM knives WHERE last_updated IS NOT NULL")[0][0]
        == 19
    )


def test_unexpected_error_on_a_group_page(db, run_scraper):
    names, pages, books = many_knives(20)
    db.query("INSERT INTO knives (knife_name) VALUES (%s)", names[3])
    client = FakeClient(
        {key: key for key in pages}, pages, books, {names[3]: HISTORY}, broken_page="G3"
    )

    assert run_scraper(client) == []

    assert db.knife(names[3])["ask"] is not None and len(db.names()) == 20


def test_dead_price_history_only_fails_knives_that_need_it(db, run_scraper):
    class NoPriceHistory(FakeClient):
        def get_price_history(self, name):
            raise sm.SteamUnavailableError("price history blocked", "pricehistory")

    client = NoPriceHistory(
        {"G1": "g"},
        {"G1": {A: ("imgA", HISTORY), B: ("imgB", None)}},
        {A: (1.0, 1.0), B: (1.0, 1.0)},
    )

    assert run_scraper(client) == [B]

    assert db.knife(A)["ask"] is not None


# --- Knives Steam's search and group pages leave out -----------------------------------------


def test_skins_missing_from_the_search(db, run_scraper):
    ax, by = "★ A | X (Factory New)", "★ B | Y (Field-Tested)"
    ay, st_ay = "★ A | Y (Minimal Wear)", "★ StatTrak™ A | Y (Minimal Wear)"
    pages = {
        "G1": {ax: ("img", HISTORY)},
        "G2": {by: ("img", HISTORY)},
        # The group page Steam serves for the variant that exists, with its own names.
        ay: {ay: ("img", HISTORY), st_ay: ("img", [])},
    }
    books = {ax: (1.0, 1.0), by: (1.0, 1.0), ay: (None, 5.0), st_ay: (None, 3.0)}
    client = FakeClient({"G1": "★ A | X", "G2": "★ B | Y"}, pages, books, exists={ay})

    assert run_scraper(client) == []

    assert db.names() == sorted([ax, by, ay, st_ay])
    checked = {call[1] for call in client.calls if call[0] == "exists"}
    assert {"★ A | Y (Factory New)", ay, "★ A", "★ B"} <= checked
    assert all(f"★ B | X ({wear})" in checked for wear in scraper.WEARS)
    assert sum(1 for call in client.calls if call[0] == "page") == 3


def test_variants_missing_from_their_group_page(db, run_scraper):
    fn, mw = "★ A | Fade (Factory New)", "★ A | Fade (Minimal Wear)"
    st_fn, st_mw, st_ft = (
        f"★ StatTrak™ A | Fade ({wear})"
        for wear in ("Factory New", "Minimal Wear", "Field-Tested")
    )
    pages = {
        "G1": {
            fn: ("img-fn", HISTORY),
            mw: ("img-mw", HISTORY),
            st_fn: ("img-st-fn", HISTORY),
        }
    }
    books = {
        fn: (1.0, 1.0),
        mw: (1.0, 1.0),
        st_fn: (1.0, 1.0),
        st_mw: (None, 2.0),
        st_ft: (3.0, 2.0),
    }
    # StatTrak MW exists. StatTrak FT passes the existence check but Steam has no price history for it.
    client = FakeClient(
        {"G1": "★ A | Fade"}, pages, books, {st_mw: HISTORY[:1]}, exists={st_mw, st_ft}
    )

    assert run_scraper(client) == [st_ft]

    assert db.names() == sorted([fn, mw, st_fn, st_mw])
    knife = db.knife(st_mw)
    # Image of the same wear.
    assert knife["image"] == "img-mw" and float(knife["bid"]) == 2.0
    checked = {call[1] for call in client.calls if call[0] == "exists"}
    assert checked == (set(scraper.group_variants("★ A | Fade")) - {fn, mw, st_fn}) | {
        "★ A"
    }

    # Next run: updated on its own, its group page isn't loaded a second time for it.
    client = FakeClient(
        {"G1": "★ A | Fade"}, pages, books, {st_mw: HISTORY[:1]}, exists={st_mw}
    )
    assert run_scraper(client) == []
    assert client.calls.count(("page", "G1")) == 1 and ("hist", st_mw) in client.calls


# --- The real client against a fake Steam ----------------------------------------------------

BOREAL = [
    "★ Bayonet | Boreal Forest (Field-Tested)",
    "★ Bayonet | Boreal Forest (Factory New)",
]


def market_route(
    group_page, page_hits, *, search=True, pages=True, history_price=124.7
):
    """Answers like Steam does, endpoints can be switched off (429)."""

    def route(path):
        url = urllib.parse.urlparse(path)
        action = urllib.parse.parse_qs(url.query).get("q", [""])[0]
        if (
            url.path.startswith(("/market/listings/", "/market/appfacets"))
            and not pages
        ):
            return 429, ""
        if url.path == f"/market/listings/730/{GROUP_PAGE_KEY}":
            page_hits.append(url.path)
            return 200, group_page
        if url.path.startswith("/market/listings/730/"):
            name = urllib.parse.unquote(url.path.rsplit("/", 1)[1])
            page_hits.append(name)
            if "Boreal Forest" in name:
                return 302, "", {"Location": f"/market/listings/730/{GROUP_PAGE_KEY}"}
            return 404, ""
        if action == "Search":
            if not search:
                return 429, ""
            result = {
                "strHash": BOREAL[0],
                "asset_description": {
                    "market_bucket_group_id": GROUP_PAGE_KEY,
                    "market_bucket_group_name": "★ Bayonet | Boreal Forest",
                },
            }
            return 200, json.dumps({"data": {"total_count": 1, "results": [result]}})
        if action == "Load":
            return 200, json.dumps(ORDER_BOOK)
        if action == "QueryPriceHistory":
            prices = [
                {"time": 1760900400, "price_median": history_price, "purchases": 2}
            ]
            return 200, json.dumps({"data": {"ecurrency": 3, "prices": prices}})
        return 404, ""

    return route


def test_steam_blocking_everything(
    db, run_scraper, fake_steam, fast_limits, monkeypatch
):
    monkeypatch.setattr(sm, "COOLDOWNS", [1, 600])
    fast_limits(max_blocked=3)
    fake_steam.route = lambda path: (429, "")
    db.query("INSERT INTO knives (knife_name) VALUES (%s), (%s)", A, B)
    start = time.monotonic()

    failed = run_scraper()

    assert failed == sorted([A, B])
    assert time.monotonic() - start < 30  # doesn't sit out the 600s cooldown


def test_group_pages_blocked_for_good(
    db, run_scraper, fake_steam, fast_limits, group_page
):
    fast_limits(max_blocked=3)
    fake_steam.route = market_route(group_page, [], pages=False)
    for name in BOREAL:
        db.query("INSERT INTO knives (knife_name) VALUES (%s)", name)

    assert run_scraper() == []

    rows = db.query(
        "SELECT knife_name, current_min_price_with_fee, buy_order_price, last_min_price_with_fee FROM knives"
    )
    assert sorted((n, float(a), float(b), float(p)) for n, a, b, p in rows) == sorted(
        (name, 131.3, 122.21, 124.7) for name in BOREAL
    )


def test_search_blocked_for_good(db, run_scraper, fake_steam, fast_limits, group_page):
    fast_limits(max_blocked=3)
    page_hits = []
    fake_steam.route = market_route(
        group_page, page_hits, search=False, history_price=99.0
    )
    known = BOREAL + [
        "★ StatTrak™ Bayonet | Boreal Forest (Minimal Wear)",
        "★ Gone | Knife (Well-Worn)",
    ]
    for name in known:
        db.query("INSERT INTO knives (knife_name) VALUES (%s)", name)

    assert run_scraper(discover=False) == []

    # One knife's page redirects to the group page, which updates the whole group.
    assert page_hits.count(f"/market/listings/730/{GROUP_PAGE_KEY}") == 1
    assert float(db.knife(BOREAL[0])["last_price"]) != 99.0
    # Updated on its own.
    assert float(db.knife("★ Gone | Knife (Well-Worn)")["last_price"]) == 99.0
    assert len(db.names()) == 4
