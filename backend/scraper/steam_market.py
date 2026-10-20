import json
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import requests

from CustomLogger import CustomLogger

APPID = 730
EUR = 3
MARKET_URL = "https://steamcommunity.com/market"
IMAGE_URL = "https://community.fastly.steamstatic.com/economy/image/{}/360fx360f"
KNIFE_FILTERS = {"Type": ["CSGO_Type_Knife"]}

# Steam answers requests that claim to be a browser but lack the rest of a browser's
# headers with 429, so we identify as a plain HTTP client instead.
HEADERS = {
    "User-Agent": "curl/8.10.0",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
}
SSR_DATA_RE = re.compile(
    r'<script type="application/json" id="valve-ssr-data"[^>]*>(.*?)</script>', re.S
)

SEARCH_PAGE_SIZE = 100
SEARCH_FALLBACK_PASSES = 3
REQUEST_TIMEOUT = 30
MAX_ATTEMPTS = 4
# Seconds to pause an endpoint after Steam starts rejecting it (403/429).
# Steam's penalties observed so far lasted 3 to 15 minutes.
COOLDOWNS = [60, 120, 300, 300, 600]
MAX_BLOCKED_SECONDS = 45 * 60
MAX_INTERVAL = 30.0
# Speed an endpoint up by 15% after this many requests in a row went through.
SPEED_UP_AFTER = 10

# (sell time in UTC, median price with fee in EUR, amount sold)
PriceHistory = list[tuple[datetime, float, int]]


class SteamDataError(Exception):
    """A single request returned something unusable; other requests may still work."""


class SteamCurrencyError(Exception):
    """Steam is returning prices in a currency other than EUR."""


class SteamUnavailableError(Exception):
    """Steam kept rejecting requests to an endpoint for longer than its max_blocked."""

    def __init__(self, message: str, endpoint: str | None = None):
        super().__init__(message)
        self.endpoint = endpoint


@dataclass
class OrderBook:
    min_sell_price: float | None
    max_buy_price: float | None


@dataclass
class GroupItem:
    name: str
    image: str | None = None
    history: PriceHistory | None = None


@dataclass
class KnifeGroup:
    key: str
    name: str
    items: dict[str, GroupItem] = field(default_factory=dict)


def _check_currency(currency: Any, what: str) -> None:
    if currency != EUR:
        raise SteamCurrencyError(
            f"Steam returned currency {currency} instead of EUR ({EUR}) for {what}."
        )


def parse_price_history(data: Any, what: str) -> PriceHistory:
    if not isinstance(data, dict) or not isinstance(data.get("prices"), list):
        raise SteamDataError(f"No price history for {what}.")
    _check_currency(data.get("ecurrency"), what)
    history = []
    for entry in data["prices"]:
        sold_at = datetime.fromtimestamp(int(entry["time"]), UTC)
        history.append(
            (
                sold_at.replace(tzinfo=None),
                round(float(entry["price_median"]), 3),
                int(entry["purchases"]),
            )
        )
    history.sort(key=lambda entry: entry[0])
    return history


@dataclass
class RateLimit:
    """
    Adaptive rate limit for one Steam endpoint. Steam doesn't publish its limits and they
    differ per endpoint, so we slow down whenever we're rejected and speed up again slowly.
    """

    endpoint: str
    name: str
    interval: float  # Current seconds between requests.
    min_interval: float
    max_blocked: float = MAX_BLOCKED_SECONDS
    next_slot: float = 0.0
    blocked_until: float = 0.0
    blocked_since: float | None = None
    cooldown_step: int = 0
    successes: int = 0


def default_rate_limits() -> dict[str, RateLimit]:
    return {
        # Steam starts rejecting group pages at around 20-28 per minute, and blocks them
        # for longer after several hundred. The scraper can do without them, so give up sooner.
        "listings": RateLimit(
            "listings",
            "group pages",
            interval=2.5,
            min_interval=2.5,
            max_blocked=15 * 60,
        ),
        "orderbook": RateLimit(
            "orderbook", "order books", interval=0.2, min_interval=0.1
        ),
        # Search has the strictest limit, but known knives can be updated without it.
        "search": RateLimit(
            "search", "search", interval=2.0, min_interval=2.0, max_blocked=10 * 60
        ),
        "pricehistory": RateLimit(
            "pricehistory", "price history", interval=2.0, min_interval=1.0
        ),
    }


class SteamMarketClient:
    """
    Talks to the JSON endpoints behind the new (React) Steam Community Market.
    Safe to share between threads.
    """

    def __init__(self, logger: CustomLogger):
        self.logger = logger
        self._limits = default_rate_limits()
        self._local = threading.local()
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    def stop(self) -> None:
        """Makes waiting and future requests fail right away."""
        self._stopped.set()

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            session = requests.Session()
            session.headers.clear()
            session.headers.update(HEADERS)
            self._local.session = session
        return self._local.session

    def _wait_turn(self, limit: RateLimit) -> None:
        while True:
            if self._stopped.is_set():
                raise SteamUnavailableError("The client was stopped.")
            with self._lock:
                now = time.monotonic()
                if (
                    limit.blocked_since is not None
                    and now - limit.blocked_since > limit.max_blocked
                ):
                    raise SteamUnavailableError(
                        f"Steam has been rejecting {limit.name} for over {limit.max_blocked // 60:.0f} minutes.",
                        limit.endpoint,
                    )
                start = max(now, limit.next_slot, limit.blocked_until)
                if start <= now:
                    limit.next_slot = now + limit.interval
                    return
            self._stopped.wait(min(start - now, 5))

    def _on_blocked(self, limit: RateLimit, status: int, what: str) -> None:
        with self._lock:
            now = time.monotonic()
            if now < limit.blocked_until:
                return  # Another request already started the cooldown.
            if limit.blocked_since is None:
                # Only slow down once per block, a retry that is still rejected just
                # means Steam's penalty hasn't run out yet.
                limit.blocked_since = now
                limit.interval = min(limit.interval * 2, MAX_INTERVAL)
                limit.successes = 0
            cooldown = COOLDOWNS[min(limit.cooldown_step, len(COOLDOWNS) - 1)]
            limit.cooldown_step += 1
            limit.blocked_until = now + cooldown
        self.logger.warning(
            f"Steam rejected {what} with {status}, pausing {limit.name} for {cooldown}s "
            f"and slowing down to {60 / limit.interval:.0f} requests per minute."
        )

    def _on_success(self, limit: RateLimit) -> None:
        with self._lock:
            limit.blocked_since = None
            limit.cooldown_step = 0
            limit.successes += 1
            if limit.successes % SPEED_UP_AFTER == 0:
                limit.interval = max(limit.min_interval, limit.interval * 0.85)

    def _get(self, url: str, what: str, endpoint: str) -> requests.Response:
        limit = self._limits[endpoint]
        attempt = 0
        while True:
            self._wait_turn(limit)
            try:
                response = self._session().get(url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                attempt += 1
                if attempt >= MAX_ATTEMPTS:
                    raise SteamDataError(f"Request for {what} failed: {e}") from e
                self._stopped.wait(2**attempt)
                continue

            if response.status_code in (403, 429):
                self._on_blocked(limit, response.status_code, what)
                continue
            self._on_success(limit)
            if response.status_code == 200:
                return response
            if response.status_code < 500:
                raise SteamDataError(
                    f"Steam returned {response.status_code} for {what}."
                )
            attempt += 1
            if attempt >= MAX_ATTEMPTS:
                raise SteamDataError(
                    f"Steam returned {response.status_code} for {what}."
                )
            self._stopped.wait(2**attempt)

    def _action(self, endpoint: str, action: str, *args: Any, limit: str) -> Any:
        what = f"{action}{list(args)}"
        query = json.dumps(list(args), ensure_ascii=False, separators=(",", ":"))
        url = f"{MARKET_URL}/{endpoint}?q={action}&qp={urllib.parse.quote(query)}"
        response = self._get(url, what, limit)
        try:
            payload = response.json()
        except ValueError as e:
            raise SteamDataError(f"Invalid JSON for {what}.") from e
        if not isinstance(payload, dict) or "data" not in payload:
            raise SteamDataError(f"Unexpected response for {what}.")
        return payload["data"]

    def _search(
        self, filters: dict[str, list[str]], start: int, count: int
    ) -> tuple[dict[str, str], int]:
        """One search request, returns ({group key: group name}, total result count)."""
        data = self._action(
            "actions",
            "Search",
            {
                "appid": APPID,
                "strQuery": "",
                "filters": filters,
                "accessoryFilters": {},
                "start": start,
                "count": count,
            },
            limit="search",
        )
        if not isinstance(data, dict) or not isinstance(data.get("total_count"), int):
            raise SteamDataError(f"Unexpected search response for {filters}.")
        groups = {}
        for result in data.get("results") or []:
            description = result.get("asset_description") or {}
            key = description.get("market_bucket_group_id") or result["strHash"]
            groups[key] = (
                description.get("market_bucket_group_name") or result["strHash"]
            )
        return groups, data["total_count"]

    def _search_all(self, filters: dict[str, list[str]]) -> tuple[dict[str, str], int]:
        groups: dict[str, str] = {}
        start = 0
        while True:
            page, total = self._search(filters, start, SEARCH_PAGE_SIZE)
            groups.update(page)
            start += SEARCH_PAGE_SIZE
            if not page or start >= total:
                return groups, total

    def get_knife_weapon_tags(self) -> list[str]:
        response = self._get(
            f"{MARKET_URL}/appfacets/{APPID}", "market filters", "listings"
        )
        try:
            tags = response.json()["facets"]["Weapon"]["tags"]
        except (ValueError, KeyError, TypeError) as e:
            raise SteamDataError("Could not parse the market filters.") from e
        return [
            tag for tag in tags if tag.startswith(("weapon_knife", "weapon_bayonet"))
        ]

    def search_knife_groups(self) -> dict[str, str]:
        """
        Returns {group key: group name} for every knife skin on the market.
        Search results are grouped by skin, a group holds every wear/StatTrak variant.
        """
        # The order of search results changes between requests, so paging through all
        # knives skips some. Searching per knife type keeps every result on one page.
        _, total = self._search(KNIFE_FILTERS, 0, 1)
        groups: dict[str, str] = {}
        try:
            weapon_tags = self.get_knife_weapon_tags()
        except (SteamDataError, SteamUnavailableError) as e:
            if self._stopped.is_set():
                raise
            self.logger.warning(f"Could not load the knife types: {e}")
            weapon_tags = []
        for tag in weapon_tags:
            try:
                groups.update(self._search_all({**KNIFE_FILTERS, "Weapon": [tag]})[0])
            except SteamDataError as e:
                self.logger.warning(f"Could not search knives of type {tag}: {e}")

        for _ in range(SEARCH_FALLBACK_PASSES):
            if len(groups) >= total:
                break
            self.logger.warning(
                f"Knife search reported {total} groups but only {len(groups)} were found, searching all knives again."
            )
            try:
                groups.update(self._search_all(KNIFE_FILTERS)[0])
            except SteamDataError as e:
                self.logger.warning(f"Could not search all knives: {e}")
        if len(groups) < total:
            self.logger.warning(
                f"Knife search reported {total} groups but only {len(groups)} were found."
            )
        return groups

    def get_knife_group(self, key: str, name: str) -> KnifeGroup:
        """
        Loads a knife group page. Steam renders it server side with the description
        and the full price history of every variant in the group.
        """
        url = f"{MARKET_URL}/listings/{APPID}/{urllib.parse.quote(key)}"
        response = self._get(url, f"group page {name}", "listings")
        match = SSR_DATA_RE.search(response.text)
        if not match:
            raise SteamDataError(f"No page data found for group {name}.")
        try:
            ssr = json.loads(match.group(1))
            queries = json.loads(ssr["renderContext"]["queryData"])["queries"]
        except (ValueError, KeyError, TypeError) as e:
            raise SteamDataError(f"Could not parse page data for group {name}.") from e

        group = KnifeGroup(key, name)
        histories: dict[str, PriceHistory] = {}
        for query in queries:
            query_key = query.get("queryKey") or []
            state = query.get("state") or {}
            if (
                len(query_key) != 4
                or query_key[0] != "market"
                or query_key[2] != APPID
                or state.get("status") != "success"
                or not state.get("data")
            ):
                continue
            item_name, data = query_key[3], state["data"]
            if query_key[1] == "description":
                tags = {tag.get("internal_name") for tag in data.get("tags") or []}
                if "CSGO_Type_Knife" not in tags:
                    continue
                icon_url = data.get("icon_url")
                group.items[item_name] = GroupItem(
                    item_name, IMAGE_URL.format(icon_url) if icon_url else None
                )
            elif query_key[1] == "pricehistory":
                try:
                    histories[item_name] = parse_price_history(data, item_name)
                except (SteamDataError, KeyError, TypeError, ValueError) as e:
                    self.logger.warning(f"Ignoring price history in group page: {e}")

        if not group.items:
            raise SteamDataError(f"No knives found on the page of group {name}.")
        for item_name, item in group.items.items():
            item.history = histories.get(item_name)
        return group

    def item_exists(self, name: str) -> bool:
        """Whether Steam has a market item with exactly this name."""
        data = self._action("orderbook", "Load", APPID, name, limit="orderbook")
        return isinstance(data, dict) and data.get("success") is True

    def get_order_book(self, name: str) -> OrderBook:
        data = self._action("orderbook", "Load", APPID, name, limit="orderbook")
        if not isinstance(data, dict) or not data.get("success"):
            raise SteamDataError(f"No order book for {name}.")
        book = data.get("data") or {}
        _check_currency(book.get("eCurrency"), name)
        min_sell = book.get("amtMinSellOrder")
        max_buy = book.get("amtMaxBuyOrder")
        return OrderBook(
            min_sell / 100 if min_sell else None,
            max_buy / 100 if max_buy else None,
        )

    def get_price_history(self, name: str) -> PriceHistory:
        """Standalone price history, Steam rate limits this much harder than group pages."""
        data = self._action(
            "actions", "QueryPriceHistory", APPID, name, limit="pricehistory"
        )
        try:
            return parse_price_history(data, name)
        except (KeyError, TypeError, ValueError) as e:
            raise SteamDataError(f"Could not parse price history for {name}.") from e
