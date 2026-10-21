import os
import re
import time
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

import psycopg2
from tqdm import tqdm

from common import log_failed_knives
from CustomLogger import CustomLogger
from DB.Postgres.config_postgres import (
    DATABASE_PASSWORD,
    DATABASE_PORT,
    DATABASE_USERNAME,
)
from DB.Postgres.db_operations_postgres import (
    add_knife_to_db,
    connect_to_db,
    get_knife_list_from_db,
    save_knives_to_db,
    save_price_history,
    update_all,
)
from Knife import Knife
from steam_market import (
    OrderBook,
    PriceHistory,
    SteamCurrencyError,
    SteamDataError,
    SteamMarketClient,
    SteamUnavailableError,
)

WORKERS = 3
MAX_RETRY_COUNT = 3
RETRY_WAIT = 60
BATCH_SIZE = 15
STEAM_FEE = 1.15

WEARS = ["Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"]
WEAR_RE = re.compile(rf" \(({'|'.join(WEARS)})\)$")

# Errors that mean the rest of the run can't succeed either.
FATAL_ERRORS = (SteamCurrencyError, SteamUnavailableError)


@dataclass
class KnifeResult:
    name: str
    image: str | None = None
    history: PriceHistory | None = None
    order_book: OrderBook | None = None
    error: str | None = None

    @property
    def complete(self) -> bool:
        return self.history is not None and self.order_book is not None


def knife_group_name(name: str) -> str:
    """'★ StatTrak™ Bayonet | Boreal Forest (Field-Tested)' -> '★ Bayonet | Boreal Forest'"""
    return WEAR_RE.sub("", name.replace("StatTrak™ ", ""))


def unlisted_group_candidates(known_groups: set[str]) -> list[str]:
    """
    Knife skins that may exist but weren't found: every knife type combined with every
    finish seen on any knife type, plus every vanilla knife.
    These are only guesses, they're checked with Steam before anything is loaded.
    """
    knife_types, finishes = set(), set()
    for group in known_groups:
        knife_type, _, finish = group.partition(" | ")
        knife_types.add(knife_type)
        if finish:
            finishes.add(finish)
    candidates = {f"{t} | {f}" for t in knife_types for f in finishes} | knife_types
    return sorted(candidates - known_groups)


def group_variants(group: str) -> list[str]:
    """Every name a knife of this skin can have: each wear with and without StatTrak."""
    names = [f"{group} ({wear})" for wear in WEARS] if " | " in group else [group]
    return names + [name.replace("★ ", "★ StatTrak™ ", 1) for name in names]


def find_existing_variant(client: SteamMarketClient, group: str) -> str | None:
    """Returns the name of a variant of the group that exists on the market, if any."""
    # Every skin exists without StatTrak, so the StatTrak names don't need checking.
    names = [f"{group} ({wear})" for wear in WEARS] if " | " in group else [group]
    for name in names:
        if client.item_exists(name):
            return name
    return None


def complete_knife(client: SteamMarketClient, result: KnifeResult) -> KnifeResult:
    """Fetches whatever is still missing for a knife."""
    try:
        if result.order_book is None:
            result.order_book = client.get_order_book(result.name)
        if result.history is None:
            result.history = client.get_price_history(result.name)
        result.error = None
    except SteamDataError as e:
        result.error = str(e)
    except SteamUnavailableError as e:
        if e.endpoint is None:
            raise  # The client was stopped.
        # Fails fast from now on, every knife that needs this endpoint gets reported.
        result.error = str(e)
    return result


def fetch_group(client: SteamMarketClient, key: str, name: str) -> list[KnifeResult]:
    group = client.get_knife_group(key, name)
    return [
        complete_knife(client, KnifeResult(item.name, item.image, item.history))
        for item in group.items.values()
    ]


@contextmanager
def run_parallel(
    jobs: Mapping[str, Callable[[], object]], description: str
) -> Iterator[Iterator[tuple[str, Future]]]:
    """
    Runs the jobs in worker threads, the yielded iterator gives (job key, future) as they finish.
    Pending jobs are cancelled as soon as the caller leaves the with block.
    """
    executor = ThreadPoolExecutor(WORKERS)
    futures: dict[Future, str] = {
        executor.submit(job): key for key, job in jobs.items()
    }
    try:
        yield (
            (futures[future], future)
            for future in tqdm(
                as_completed(futures), total=len(futures), desc=description
            )
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


class KnifeUpdater:
    def __init__(self, logger: CustomLogger, discover: bool):
        self.logger = logger
        self.discover = discover
        self.logger.info("Loading the knives from the database.")
        self._connect()
        self.knives: dict[str, Knife] = {
            knife.knife_name: knife for knife in get_knife_list_from_db(self.cursor)
        }
        self.logger.info(f"Loaded {len(self.knives)} knives from the database.")
        self.pending: list[Knife] = []
        self.unsaved: dict[str, KnifeResult] = {}
        self.updated_names: set[str] = set()
        self.updated = 0
        self.added = 0
        self.pages_unavailable = False
        # Group pages loaded this run, a knife missing from its page isn't on it at all.
        self.loaded_groups: set[str] = set()
        self.images: dict[str, str] = {
            name: knife.knife_image
            for name, knife in self.knives.items()
            if knife.knife_image
        }

    def _connect(self) -> None:
        self.connection, self.cursor = connect_to_db(
            "localhost",
            "knives",
            DATABASE_PORT,
            DATABASE_USERNAME,
            DATABASE_PASSWORD,
            self.logger,
        )

    def _recover_db(self) -> None:
        if self.connection.closed:
            self.logger.warning("Lost the database connection, reconnecting.")
            self._connect()
        else:
            self.connection.rollback()

    def flush(self) -> None:
        if not self.pending:
            return
        try:
            save_knives_to_db(self.pending, self.cursor, self.connection)
            saved = list(self.pending)
        except psycopg2.Error as e:
            self.logger.error(
                f"Could not save {len(self.pending)} knives at once ({e}), saving them one by one."
            )
            self._recover_db()
            saved = []
            for knife in self.pending:
                try:
                    save_knives_to_db([knife], self.cursor, self.connection)
                    saved.append(knife)
                except psycopg2.Error as e:
                    self.logger.error(f"Could not save {knife.knife_name}: {e}")
                    self.unsaved[knife.knife_name] = KnifeResult(
                        knife.knife_name, error=f"Database error: {e}"
                    )
                    self._recover_db()
        self.updated += len(saved)
        self.updated_names.update(knife.knife_name for knife in saved)
        self.pending.clear()

    def save(self, result: KnifeResult) -> bool:
        """Writes a fully fetched knife to the database. Returns False if it should be retried."""
        try:
            self._save(result)
        except Exception as e:
            self.logger.exception(f"Error while saving {result.name}.")
            result.error = f"Error while saving: {e!r}"
            self._recover_db()
            return False
        return True

    def _save(self, result: KnifeResult) -> None:
        assert result.history is not None and result.order_book is not None
        knife = self.knives.get(result.name)
        if knife is None:
            if not self.discover:
                return
            knife_id = add_knife_to_db(result.name, self.cursor, self.connection)
            knife = Knife(result.name, knife_id, None, None, None, None, None)
            self.knives[result.name] = knife
            self.added += 1
            self.logger.info(f"Added new knife {result.name}.")

        # Older rows came from the previous scraper, only add what is new since then.
        new_history = [
            entry
            for entry in result.history
            if knife.last_sold is None or entry[0] >= knife.last_sold
        ]
        save_price_history(new_history, self.cursor, self.connection, knife.knife_id)

        last_sold: datetime | None
        last_price: float | None
        if result.history:
            last_sold, last_price, _ = result.history[-1]
        else:
            last_sold, last_price = knife.last_sold, knife.last_min_price_with_fee
        min_sell = result.order_book.min_sell_price
        image = result.image or knife.knife_image or self.sibling_image(result.name)
        self.pending.append(
            Knife(
                result.name,
                knife.knife_id,
                min_sell,
                min_sell / STEAM_FEE if min_sell else None,
                last_price,
                float(last_price) / STEAM_FEE if last_price else None,
                result.order_book.max_buy_price,
                image,
                last_sold,
                last_updated=datetime.now(),
            )
        )
        knife.last_sold = last_sold
        if image:
            self.images[result.name] = image
        if len(self.pending) >= BATCH_SIZE:
            self.flush()

    def process_groups(
        self,
        client: SteamMarketClient,
        groups: dict[str, str],
        seen: set[str],
        incomplete: dict[str, KnifeResult],
        description: str,
    ) -> dict[str, str]:
        """Updates every knife in the given groups. Returns the groups that failed to load."""
        failed_groups = {}
        jobs = {
            key: (lambda key=key, name=name: fetch_group(client, key, name))
            for key, name in groups.items()
        }
        with run_parallel(jobs, description) as finished:
            for key, future in finished:
                results = self._group_results(future, groups[key])
                if self.pages_unavailable:
                    return {}
                if results is None:
                    failed_groups[key] = groups[key]
                    continue
                for result in results:
                    self.loaded_groups.add(knife_group_name(result.name))
                    seen.add(result.name)
                    if not result.complete or not self.save(result):
                        incomplete[result.name] = result
        return failed_groups

    def _group_results(self, future: Future, name: str) -> list[KnifeResult] | None:
        """The knives of a loaded group, None if the group failed to load."""
        try:
            return future.result()
        except SteamUnavailableError as e:
            if e.endpoint != "listings":
                raise
            # Knives that weren't updated yet get updated one by one instead.
            self.logger.error(f"{e} Updating the remaining knives one by one.")
            self.pages_unavailable = True
        except FATAL_ERRORS:
            raise
        except SteamDataError as e:
            self.logger.warning(f"Could not load knife group {name}: {e}")
        except Exception:
            self.logger.exception(f"Unexpected error while loading knife group {name}.")
        return None

    def process_knives(
        self,
        client: SteamMarketClient,
        knives: dict[str, KnifeResult],
        description: str,
    ) -> dict[str, KnifeResult]:
        """Updates knives one by one. Returns the ones that still failed."""
        failed = {}
        jobs = {
            name: (lambda result=result: complete_knife(client, result))
            for name, result in knives.items()
        }
        with run_parallel(jobs, description) as finished:
            for name, future in finished:
                try:
                    result = future.result()
                except FATAL_ERRORS:
                    raise
                except Exception as e:
                    self.logger.exception(f"Unexpected error while updating {name}.")
                    knives[name].error = repr(e)
                    failed[name] = knives[name]
                    continue
                if not result.complete or not self.save(result):
                    failed[name] = result
        return failed

    def update_groups(
        self,
        client: SteamMarketClient,
        groups: dict[str, str],
        seen: set[str],
        incomplete: dict[str, KnifeResult],
        description: str,
    ) -> None:
        for attempt in range(MAX_RETRY_COUNT + 1):
            if not groups or self.pages_unavailable:
                break
            if attempt:
                self.logger.info(
                    f"Retrying {len(groups)} knife groups in {RETRY_WAIT} seconds (retry {attempt}/{MAX_RETRY_COUNT})."
                )
                time.sleep(RETRY_WAIT)
            groups = self.process_groups(
                client,
                groups,
                seen,
                incomplete,
                f"{description} (attempt {attempt + 1})",
            )
        self.flush()

    def sibling_image(self, name: str) -> str | None:
        """Image of another variant of the same skin, the same wear if possible."""
        group = knife_group_name(name)
        wear = WEAR_RE.search(name)
        siblings = [n for n in self.images if knife_group_name(n) == group]
        siblings.sort(key=lambda n: not (wear and n.endswith(wear.group(0))))
        return self.images[siblings[0]] if siblings else None

    def find_missing_variants(self, client: SteamMarketClient) -> list[str]:
        """
        Group pages leave out some variants (e.g. a StatTrak wear that hasn't sold in years).
        Returns the names of such variants that Steam confirms exist.
        """
        groups = {knife_group_name(name) for name in self.knives}
        candidates = sorted(
            {name for group in groups for name in group_variants(group)}
            - self.knives.keys()
        )
        found = []
        jobs = {
            name: (lambda name=name: client.item_exists(name)) for name in candidates
        }
        with run_parallel(jobs, "Looking for missing knife variants") as finished:
            for name, future in finished:
                try:
                    exists = future.result()
                except FATAL_ERRORS:
                    raise
                except SteamDataError as e:
                    self.logger.warning(f"Could not check whether {name} exists: {e}")
                    continue
                except Exception:
                    self.logger.exception(
                        f"Unexpected error while checking whether {name} exists."
                    )
                    continue
                if exists:
                    found.append(name)
        self.logger.info(
            f"Found {len(found)} knife variants missing from their group pages (checked {len(candidates)})."
        )
        return found

    def find_unlisted_groups(
        self, client: SteamMarketClient, known_groups: set[str]
    ) -> dict[str, str]:
        """
        Steam's search leaves out skins that have no listings at all right now. Returns
        {name of an existing variant: group name} for such skins. Loading the group page
        by that name only ever adds knives Steam itself describes, never guessed names.
        """
        candidates = unlisted_group_candidates(known_groups)
        if not candidates:
            return {}
        found: dict[str, str] = {}
        jobs = {
            group: (lambda group=group: find_existing_variant(client, group))
            for group in candidates
        }
        with run_parallel(jobs, "Looking for unlisted knives") as finished:
            for group, future in finished:
                try:
                    name = future.result()
                except FATAL_ERRORS:
                    raise
                except SteamDataError as e:
                    self.logger.warning(f"Could not check whether {group} exists: {e}")
                    continue
                except Exception:
                    self.logger.exception(
                        f"Unexpected error while checking whether {group} exists."
                    )
                    continue
                if name:
                    found[name] = group
        self.logger.info(
            f"Found {len(found)} knife groups that aren't listed in the search (checked {len(candidates)})."
        )
        return found

    def search_groups(self, client: SteamMarketClient) -> dict[str, str]:
        """Every knife group on the market, none if the search fails."""
        fallback = "only knives already in the database will be updated"
        try:
            groups = client.search_knife_groups()
        except SteamCurrencyError:
            raise
        except (SteamDataError, SteamUnavailableError) as e:
            if isinstance(e, SteamUnavailableError) and e.endpoint != "search":
                raise
            self.logger.error(f"Could not load the knife list ({e}), {fallback}.")
            return {}
        except Exception:
            self.logger.exception(
                f"Unexpected error while loading the knife list, {fallback}."
            )
            return {}
        self.logger.info(f"Found {len(groups)} knife groups on the market.")
        return groups

    def run(self, client: SteamMarketClient) -> dict[str, KnifeResult]:
        groups = self.search_groups(client)

        seen: set[str] = set()
        incomplete: dict[str, KnifeResult] = {}
        self.update_groups(client, groups, seen, incomplete, "Knife groups")

        if self.discover:
            known = set(groups.values()) | {knife_group_name(n) for n in self.knives}
            unlisted = self.find_unlisted_groups(client, known)
            self.update_groups(
                client, unlisted, seen, incomplete, "Unlisted knife groups"
            )

        # Knives that weren't on the loaded group pages, because the search failed, their
        # group failed to load or they're not listed in the search anymore. A knife's own
        # page redirects to its group page, so one page per group is still enough.
        known_groups: dict[str, str] = {}
        for name in sorted(self.knives.keys() - seen):
            if knife_group_name(name) not in self.loaded_groups:
                known_groups.setdefault(knife_group_name(name), name)
        groups = {name: group for group, name in known_groups.items()}
        self.update_groups(client, groups, seen, incomplete, "Known knife groups")

        for name in self.knives.keys() - seen:
            incomplete[name] = KnifeResult(name)
        # Added only if Steam returns both an order book and a price history for them.
        if self.discover and not self.pages_unavailable:
            for name in self.find_missing_variants(client):
                incomplete.setdefault(name, KnifeResult(name))

        for attempt in range(MAX_RETRY_COUNT + 1):
            if not incomplete:
                break
            if attempt:
                self.logger.info(
                    f"Retrying {len(incomplete)} knives in {RETRY_WAIT} seconds (retry {attempt}/{MAX_RETRY_COUNT})."
                )
                time.sleep(RETRY_WAIT)
            incomplete = self.process_knives(
                client, incomplete, f"Single knives (attempt {attempt + 1})"
            )
            self.flush()
        return incomplete

    def not_updated(self, reason: str) -> dict[str, KnifeResult]:
        return {
            name: KnifeResult(name, error=reason)
            for name in self.knives.keys() - self.updated_names
        }

    def finish(
        self,
        failed: dict[str, KnifeResult],
        failed_knives_file: str,
        stopped_early: bool,
    ) -> None:
        self.flush()
        if stopped_early:
            failed = {**self.not_updated("Run stopped early"), **failed}
        for name, result in self.unsaved.items():
            if name not in self.updated_names:
                failed.setdefault(name, result)
        try:
            update_all(self.cursor)
        except psycopg2.Error as e:
            self.logger.error(f"Could not update the sales statistics: {e}")

        if os.path.exists(failed_knives_file):
            os.remove(failed_knives_file)
        if failed:
            for result in failed.values():
                self.logger.warning(f"Failed to update {result.name}: {result.error}")
            log_failed_knives(list(failed), failed_knives_file, self.logger)
        self.logger.info(
            f"Updated {self.updated} knives ({self.added} new), {len(failed)} failed."
        )
        self.cursor.close()
        self.connection.close()


def update_all_knife_data(
    failed_knives_file: str, logger: CustomLogger, discover: bool = True
) -> None:
    """
    Updates prices, buy orders and sell history of every knife.
    With discover, knives that are on the market but not in the database are added.
    Knives that still fail after all retries are written to failed_knives_file.
    """
    updater = KnifeUpdater(logger, discover)
    client = SteamMarketClient(logger)
    failed: dict[str, KnifeResult] = {}
    stopped_early = True
    try:
        failed = updater.run(client)
        stopped_early = False
    except FATAL_ERRORS as e:
        logger.fatal(f"Stopping early: {e}")
    finally:
        client.stop()
        updater.finish(failed, failed_knives_file, stopped_early)
