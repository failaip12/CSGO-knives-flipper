import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.sync_api import Page, TimeoutError, sync_playwright
from psycopg2.extensions import connection, cursor
from tqdm import tqdm

from common import copy_user_data_dir, load_failed_knives_csv, log_failed_knives
from CustomLogger import CustomLogger
from DB.Postgres.config_postgres import (
    DATABASE_PASSWORD,
    DATABASE_PORT,
    DATABASE_USERNAME,
)
from DB.Postgres.db_operations_postgres import (
    add_new_knives_to_db,
    check_if_knife_id_is_correct,
    connect_to_db,
    connect_to_db_threaded,
    get_and_save_historical_pricing_helper,
    get_knife_from_db,
    get_knife_list_from_db,
    save_knives_to_db,
    update_all,
)
from Knife import Knife

ExtractedData = Dict[str, List[Any]]

DEFAULT_NUMBER_OF_PAGES = 200
MAX_RETRY_COUNT = 5
MAX_FAILED_KNIVES = 10
THREAD_COUNT = 6
WAIT_TIME = 6
FAIL_BATCH_SIZE = 15
BATCH_SIZE = 15


def navigate_and_wait(
    page: Page, url: str, wait_time: int, logger: CustomLogger, retries: int = 3
) -> bool:
    for i in range(retries):
        try:
            page.goto(url)
            page.wait_for_selector(
                '//span[@class="market_listing_item_name"]',
                timeout=wait_time * 1000,
            )
            return True
        except TimeoutError:
            logger.warning(f"Timeout loading {url}, retry {i + 1}/{retries}")
            time.sleep(5)
    logger.warning(f"Failed to load {url} after {retries} retries.")
    return False


def get_knife_list(page: Page, wait_time: int, logger: CustomLogger) -> Set[str]:
    """
    Fetches the list of all knife names from the Steam market.
    """
    base_url = "https://steamcommunity.com/market/search?q=&category_730_ItemSet[]=any&category_730_ProPlayer[]=any&category_730_StickerCapsule[]=any&category_730_TournamentTeam[]=any&category_730_Weapon[]=any&category_730_Type[]=tag_CSGO_Type_Knife&appid=730#p{}_name_asc"
    knife_name_set = set()

    if not navigate_and_wait(page, base_url.format(1), wait_time, logger):
        logger.error("Failed to load the first page of knife listings.")
        return knife_name_set

    soup = BeautifulSoup(page.content(), "html.parser")

    number_of_pages_span = soup.find("span", id="searchResults_links")
    number_of_pages = DEFAULT_NUMBER_OF_PAGES
    if isinstance(number_of_pages_span, Tag):
        page_links = number_of_pages_span.find_all(
            "span", class_="market_paging_pagelink"
        )
        if page_links:
            last_page_text = page_links[-1].text.strip()
            try:
                number_of_pages = int(last_page_text)
            except ValueError:
                logger.warning(
                    f"Could not parse the number of pages, defaulting to {DEFAULT_NUMBER_OF_PAGES}."
                )

    for page_num in tqdm(range(1, number_of_pages + 1), desc="Scraping knife list"):
        if page_num > 1 and not navigate_and_wait(
            page, base_url.format(page_num), wait_time, logger
        ):
            logger.warning(f"Failed to load page {page_num}, skipping.")
            continue
        page.wait_for_timeout(1000)
        soup = BeautifulSoup(page.content(), "html.parser")
        names = soup.find_all("span", class_="market_listing_item_name")
        if not names:
            logger.warning(f"No knife names found on page {page_num}.")
            continue

        for name in names:
            knife_name_set.add(name.text.strip())

    return knife_name_set


def extract_knife_data(
    page: Page, url: str, wait_time: int, logger: CustomLogger
) -> Optional[ExtractedData]:
    """
    Extracts data from a single knife page.
    """
    try:
        page.goto(url, timeout=wait_time * 1000)
        page.wait_for_load_state("networkidle", timeout=wait_time * 1000)
    except TimeoutError:
        logger.warning(f"Timeout loading {url}.")
        return None

    img_src = page.locator(".market_listing_largeimage img").get_attribute(
        "src", timeout=1000
    )

    buy_orders_elements = page.query_selector_all(
        ".market_commodity_orders_header_promote"
    )
    buy_orders_text = [element.inner_text() for element in buy_orders_elements]

    price_elements = page.query_selector_all(
        ".market_listing_price.market_listing_price_with_fee"
    )
    prices = [element.inner_text() for element in price_elements]

    return {
        "buy_orders": buy_orders_text,
        "current_min_price_with_fee": prices,
        "knife_image": [img_src] if img_src else [],
    }


def extract_knife_data_with_retry(
    page: Page, url: str, wait_time: int, logger: CustomLogger, retries: int = 3
) -> Optional[ExtractedData]:
    """
    Tries to extract knife data multiple times in case of transient errors.
    """
    for i in range(retries):
        data = extract_knife_data(page, url, wait_time, logger)
        if data:
            return data
        logger.warning(
            f"Attempt {i + 1} failed for {url}. Retrying in {i * 2 + 2} seconds."
        )
        time.sleep(i * 2 + 2)
    logger.error(f"Failed to extract data for {url} after {retries} retries.")
    return None


def get_and_save_historical_pricing(
    page: Page,
    cursor: cursor,
    connection: connection,
    knife_id: int,
    name: str,
    logger: CustomLogger,
    max_attempts: int = 3,
) -> Tuple[Optional[float], Optional[datetime]]:
    """
    Fetches and saves historical pricing data for a knife.
    """
    for attempt in range(max_attempts):
        try:
            console_log_result = page.evaluate(
                """
                () => {
                    const data = window.g_plotPriceHistory?.data?.[0];
                    return data ? JSON.stringify({ data }) : null;
                }
                """
            )
            if console_log_result:
                break
            logger.warning(
                f"Historical data not found for {name} on attempt {attempt + 1}."
            )
            time.sleep(attempt + 1)
        except Exception as e:
            logger.error(
                f"Error getting historical pricing for {name} on attempt {attempt + 1}: {e}"
            )
            time.sleep(attempt + 1)
    else:
        logger.error(
            f"Could not get historical pricing for {name} after {max_attempts} attempts."
        )
        return None, None

    try:
        data = json.loads(console_log_result)["data"]
    except (json.JSONDecodeError, KeyError):
        logger.error(f"Could not parse historical pricing JSON for {name}.")
        return None, None

    date_format = "%b %d %Y %H"
    knife = get_knife_from_db(cursor, name)
    price, parsed_date = None, None

    if knife and knife.last_sold:
        last_known_date = knife.last_sold
        new_data = [
            entry
            for entry in data
            if datetime.strptime(entry[0][:-4], date_format) > last_known_date
        ]
        if new_data:
            price, parsed_date = get_and_save_historical_pricing_helper(
                new_data, date_format, cursor, connection, knife_id
            )
        else:
            parsed_date = last_known_date
            price = knife.last_min_price_with_fee
    else:
        price, parsed_date = get_and_save_historical_pricing_helper(
            data, date_format, cursor, connection, knife_id
        )

    return price, parsed_date


def _parse_price(price_str: str, logger: CustomLogger, context: str) -> Optional[float]:
    """Parses a price string into a float."""
    if not price_str:
        return None
    try:
        return float(
            price_str.strip()
            .replace(",", ".")
            .replace("-", "0")
            .replace("€", "")
            .replace(" ", "")
        )
    except (ValueError, TypeError) as e:
        logger.error(f"Could not parse {context}: '{price_str}'. Error: {e}")
        return None


def _extract_knife_id(
    page: Page, wait_time: int, logger: CustomLogger, name: str
) -> Optional[int]:
    """Extracts the knife_id from the page's script tags."""
    try:
        page.wait_for_selector("script", state="attached", timeout=wait_time * 1000)
        script_tags = page.query_selector_all("script")
        for script_tag in script_tags:
            javascript_code = script_tag.inner_html()
            if javascript_code and "Market_LoadOrderSpread" in javascript_code:
                match = re.search(
                    r"Market_LoadOrderSpread\(\s*(\d+)\s*\)", javascript_code
                )
                if match:
                    return int(match.group(1))
    except TimeoutError:
        logger.error(f"Timed out waiting for script tags for {name}")
    except Exception as e:
        logger.error(f"An error occurred while extracting knife_id for {name}: {e}")
    return None


def get_knife_info(
    name: str,
    page: Page,
    cursor: cursor,
    connection: connection,
    wait_time: int,
    logger: CustomLogger,
) -> Optional[Knife]:
    """
    Fetches and processes all data for a single knife from its Steam market page.
    """
    url = f"https://steamcommunity.com/market/listings/730/{name}"
    data = extract_knife_data_with_retry(page, url, wait_time, logger)
    if not data:
        logger.warning(f"Could not extract data for {name}.")
        return None

    message = data.get("message")
    if isinstance(message, str):
        current_price = "no listings" not in message
    elif message and hasattr(message[0], "text"):
        current_price = "no listings" not in message[0].text
    else:
        current_price = True

    # Extract prices and other data
    buy_orders_list = data.get("buy_orders")
    buy_orders = buy_orders_list and len(buy_orders_list) >= 2
    if not buy_orders:
        logger.warning(f"No buy orders found for {name}")

    current_min_price_with_fee_list = data.get("current_min_price_with_fee")
    if not current_min_price_with_fee_list:
        logger.info(f"No current price for {name}")
        current_price = False

    current_min_price_with_fee = (
        _parse_price(
            current_min_price_with_fee_list[0], logger, "current_min_price_with_fee"
        )
        if current_price and current_min_price_with_fee_list
        else None
    )
    buy_order_price = (
        _parse_price(buy_orders_list[1], logger, "buy_order_price")
        if buy_orders and buy_orders_list
        else None
    )

    knife_image = data.get("knife_image")
    if isinstance(knife_image, list):
        knife_image = knife_image[0] if knife_image else None

    # Extract and verify knife_id
    knife_id = _extract_knife_id(page, wait_time, logger, name)
    if not knife_id:
        logger.error(f"Could not find knife_id for {name}.")
        return None

    if not check_if_knife_id_is_correct(cursor, knife_id, name):
        cursor.execute(
            "UPDATE knives SET knife_id = %s WHERE knives.knife_name = %s",
            (knife_id, name),
        )
        connection.commit()
        logger.info(f"Updated knife_id for {name} to {knife_id}.")

    # Get historical pricing data
    last_min_price_with_fee, last_sold = get_and_save_historical_pricing(
        page, cursor, connection, knife_id, name, logger
    )
    if not last_min_price_with_fee or not last_sold:
        logger.warning(f"Could not process price history for {name}")
    # Calculate derived prices
    last_min_price_without_fee = (
        float(last_min_price_with_fee) / 1.15 if last_min_price_with_fee else None
    )
    current_min_price_without_fee = (
        float(current_min_price_with_fee) / 1.15 if current_min_price_with_fee else None
    )
    return Knife(
        name,
        knife_id,
        current_min_price_with_fee,
        current_min_price_without_fee,
        last_min_price_with_fee,
        last_min_price_without_fee,
        buy_order_price,
        knife_image,
        last_sold,
        last_updated=datetime.now(),
    )


def safe_get_knife_info(
    name: Tuple[str],
    page: Page,
    cursor: cursor,
    connection: connection,
    wait_time: int,
    logger: CustomLogger,
) -> Optional[Knife]:
    """A wrapper that catches and logs errors for get_knife_info."""
    try:
        return get_knife_info(name[0], page, cursor, connection, wait_time, logger)
    except Exception as e:
        logger.error(f"Error processing knife {name[0]}: {e}")
        return None


def initialize_directory(logger: CustomLogger) -> str:
    project_root = Path(__file__).parent
    original_user_data_dir = project_root / "playwright_cache"
    if not original_user_data_dir.exists():
        raise FileNotFoundError(
            f"User data directory {original_user_data_dir} does not exist."
        )
    return copy_user_data_dir(str(original_user_data_dir), logger, "playwright_cache")


def fetch_all_knives_for_thread(
    knife_queue: Queue,
    wait_time: int,
    progress_bar: tqdm,
    logger: CustomLogger,
    failed_knives_name: str,
) -> None:
    """
    Fetches knife data for a list of knife names in a separate thread.
    Initializes its own Playwright instance and user data directory.
    """
    user_data_dir = initialize_directory(logger)
    failed_knives = []
    batch = []

    try:
        with sync_playwright() as p:
            connection, cursor = connect_to_db_threaded(
                "localhost",
                "knives",
                DATABASE_PORT,
                DATABASE_USERNAME,
                DATABASE_PASSWORD,
                logger,
            )
            browser = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir, headless=False
            )
            page = browser.new_page()
            page.route("**/*", route_intercept)

            while True:
                try:
                    knife_name = knife_queue.get_nowait()
                except Empty:
                    break

                knife_info = safe_get_knife_info(
                    knife_name, page, cursor, connection, wait_time, logger
                )
                if knife_info:
                    batch.append(knife_info)
                else:
                    failed_knives.append(knife_name[0])

                progress_bar.update(1)
                if len(batch) >= BATCH_SIZE:
                    save_knives_to_db(batch, cursor, connection)
                    batch.clear()
                if len(failed_knives) >= FAIL_BATCH_SIZE:
                    log_failed_knives(failed_knives, failed_knives_name, logger)
                    failed_knives.clear()

            if batch:
                save_knives_to_db(batch, cursor, connection)
            if failed_knives:
                log_failed_knives(failed_knives, failed_knives_name, logger)

            browser.close()
            connection.close()
            cursor.close()
    except Exception as e:
        logger.error(f"An error occurred in thread: {e}")
    finally:
        shutil.rmtree(user_data_dir)


def process_knives(
    logger: CustomLogger,
    knife_names: List[Tuple[str]],
    failed_knives_name: str,
    wait_time: int = WAIT_TIME,
):
    """
    Processes a list of knife names using a thread pool.
    """
    knife_queue = Queue()
    for name in knife_names:
        knife_queue.put(name)

    total_knives = len(knife_names)
    progress_bar = tqdm(total=total_knives, desc="Processing knives", unit="knife")

    with ThreadPoolExecutor(max_workers=THREAD_COUNT) as executor:
        futures = [
            executor.submit(
                fetch_all_knives_for_thread,
                knife_queue,
                wait_time,
                progress_bar,
                logger,
                failed_knives_name,
            )
            for _ in range(THREAD_COUNT)
        ]
        try:
            for future in as_completed(futures):
                future.result()
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt received. Cancelling tasks...")
            for future in futures:
                future.cancel()

    progress_bar.close()


def update_all_knife_data(
    failed_knives_name: str,
    logger: CustomLogger,
    date: Optional[str] = None,
    wait_time: int = WAIT_TIME,
):
    sql_connection, sql_cursor = connect_to_db(
        "localhost",
        "knives",
        DATABASE_PORT,
        DATABASE_USERNAME,
        DATABASE_PASSWORD,
        logger,
    )

    failed_knives = load_failed_knives_csv(failed_knives_name, logger)
    if len(failed_knives) > MAX_FAILED_KNIVES:
        knife_names = failed_knives
        os.rename(
            failed_knives_name,
            f"failed_knives_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
    else:
        knife_names = get_knife_list_from_db(sql_cursor, date)
    process_knives(
        logger, [(name,) for name in knife_names], failed_knives_name, wait_time
    )
    update_all(sql_cursor)
    retries = 0
    while retries < MAX_RETRY_COUNT and len(failed_knives) > MAX_FAILED_KNIVES:
        failed_knives = load_failed_knives_csv(failed_knives_name, logger)
        os.rename(
            failed_knives_name,
            f"failed_knives_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        logger.info(f"Reprocessing {len(failed_knives)} failed knives.")
        process_knives(
            logger, [(name,) for name in failed_knives], failed_knives_name, wait_time
        )
        update_all(sql_cursor)
        retries += 1

    sql_cursor.close()
    sql_connection.close()


def route_intercept(route):
    if route.request.resource_type in ["image", "stylesheet", "media", "fetch"]:
        return route.abort()
    return route.continue_()


def steam_login():
    with sync_playwright() as p:
        project_root = Path(
            __file__
        ).parent  # This will get the directory where this script is located

        original_user_data_dir = (
            project_root
            / "playwright_cache"  # Relative path to 'playwright_cache' directory
        )

        browser = p.chromium.launch_persistent_context(
            user_data_dir=original_user_data_dir, headless=False
        )
        page = browser.new_page()
        page.goto("https://steamcommunity.com/login/home")

        browser.storage_state(path="storage.json")

        input("Press Enter after completing the login process...")
        browser.close()


if __name__ == "__main__":
    logger = CustomLogger("knives_playwright.log")

    # steam_login()
    # sql_connection, sql_cursor = connect_to_db(
    #     "localhost",
    #     "knives",
    #     DATABASE_PORT,
    #     DATABASE_USERNAME,
    #     DATABASE_PASSWORD,
    #    logger,
    # )
    # with sync_playwright() as p:
    #    project_root = Path(
    #        __file__
    #    ).parent  # This will get the directory where this script is located
    #    original_user_data_dir = (
    #        project_root / "playwright_cache"
    #    )  # Relative path to 'playwright_cache' directory
    #    browser = p.chromium.launch_persistent_context(
    #        user_data_dir=original_user_data_dir, headless=False
    #    )  # Headless True doesnt transfer the log in state properly
    #    page = browser.new_page()
    #    page.route("**/*", route_intercept)
    #    # knife_list = get_knife_list(page, 6, logger)
    #    # print(f"Total knives found: {len(knife_list)}")
    #    # add_new_knives_to_db(knife_list, sql_cursor, sql_connection)
    #    print(get_knife_from_db(sql_cursor, "★ Bayonet | Boreal Forest (Factory New)"))
    #    knife = safe_get_knife_info(
    #        ("★ Bayonet | Boreal Forest (Factory New)",),
    #        page,
    #        sql_cursor,
    #        sql_connection,
    #        6,
    #       logger,
    #    )
    #    print(knife)
    #    if knife:
    #        save_knives_to_db([knife], sql_cursor, sql_connection)
    #    else:
    #        print("Knife not found or could not be processed.")
    update_all_knife_data("failed_knives.csv", logger)
    # update_all(sql_cursor)
