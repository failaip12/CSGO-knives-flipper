import json
import re
import shutil
import signal
import sys
import threading
import time
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup
from mysql.connector.connection import MySQLConnection
from mysql.connector.cursor import MySQLCursor
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from seleniumwire2 import webdriver
from tqdm import tqdm

from common import copy_user_data_dir, log_failed_knives
from CustomLogger import CustomLogger
from DB.MySQL.config_mysql import DATABASE_PASSWORD, DATABASE_PORT, DATABASE_USERNAME
from DB.MySQL.db_operations_mysql import (
    connect_to_db,
    connect_to_db_threaded,
    get_and_save_historical_pricing_helper,
    get_knife_from_db,
    get_knife_list_from_db,
    save_knives_to_db,
    update_all,
)
from Knife import Knife

# https://steamcommunity.com/market/listings/730/%E2%98%85%20Survival%20Knife%20%7C%20Crimson%20Web%20%28Factory%20New%29
# WEB SCRAPE IT
# https://steamcommunity.com/market/search?q=&category_730_ItemSet%5B%5D=any&category_730_ProPlayer%5B%5D=any&category_730_StickerCapsule%5B%5D=any&category_730_TournamentTeam%5B%5D=any&category_730_Weapon%5B%5D=any&category_730_Type%5B%5D=tag_CSGO_Type_Knife&appid=730#p1_name_asc


def parse_page(url: str, driver: WebDriver) -> str:
    driver.get(url)
    # driver.implicitly_wait(sleep_time)
    # time.sleep(sleep_time)
    page = driver.page_source
    return page


def interceptor(request) -> None:
    """
    request.headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
    request.headers['Accept-Encoding'] = 'gzip, deflate, br'
    request.headers['Accept-Language'] = 'en-US,en;q=0.5'
    request.headers['Host'] = 'steamcommunity.com'
    request.headers['Sec-Fetch-Dest'] = 'document'
    request.headers['Sec-Fetch-Mode'] = 'navigate'
    request.headers['Sec-Fetch-Site'] = 'cross-site'
    request.headers['Sec-Fetch-User'] = '?1'
    request.headers['Upgrade-Insecure-Requests'] = '1'
    request.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT …/20100101 Firefox/115.0'
    request.headers['X-Amzn-Trace-Id'] = 'Root=1-64bbe270-19d868e26f99458b749c6728'
    """
    request.headers["Access-Control-Allow-Origin"] = "*"


# TODO: Multithread this b
def get_knife_list(driver: WebDriver, wait_time: int) -> Set[str]:
    range_start = 0
    base_url = "https://steamcommunity.com/market/search?q=&category_730_ItemSet[]=any&category_730_ProPlayer[]=any&category_730_StickerCapsule[]=any&category_730_TournamentTeam[]=any&category_730_Weapon[]=any&category_730_Type[]=tag_CSGO_Type_Knife&appid=730#p{}_name_asc"
    knife_name_set = set()
    page = parse_page(base_url.format(range_start + 1), driver)
    WebDriverWait(driver, wait_time).until(
        EC.visibility_of_element_located(
            (By.XPATH, '//span[@class="market_listing_item_name"]')
        )
    )
    soup = BeautifulSoup(page, "html.parser")
    number_of_pages = 250
    names = soup.find_all("span", class_="market_listing_item_name")
    new_names: List[str] = list()
    for name in names:
        knife_name_set.add(name.text.strip())
    for page_num in tqdm(range(range_start + 2, number_of_pages + 1)):
        page = parse_page(base_url.format(page_num), driver)
        WebDriverWait(driver, wait_time).until(
            EC.visibility_of_element_located(
                (By.XPATH, '//span[@class="market_listing_item_name"]')
            )
        )
        soup = BeautifulSoup(page, "html.parser")
        names = soup.find_all("span", class_="market_listing_item_name")
        error = soup.find_all("h3")
        while error or len(names) < 5 or names == new_names:
            # driver.implicitly_wait(30)
            time.sleep(10)
            driver.execute_script("location.reload(true);")
            page = parse_page(base_url.format(page_num), driver)
            WebDriverWait(driver, wait_time).until(
                EC.visibility_of_element_located(
                    (By.XPATH, '//span[@class="market_listing_item_name"]')
                )
            )
            soup = BeautifulSoup(page, "html.parser")
            names = soup.find_all("span", class_="market_listing_item_name")
            error = soup.find_all("h3")
        new_names = names.copy()
        print("\n", len(knife_name_set))
        for name in names:
            knife_name_set.add(name.text.strip())
    return knife_name_set


ExtractedData = Dict[str, List[Any]]


def extract_knife_data(driver: WebDriver, url: str, wait_time: int) -> ExtractedData:
    driver.execute_script("location.reload(true);")  # TODO: This may be unnecessary
    page = parse_page(url, driver)

    driver.execute_script("window.scrollTo(0, 1000);")
    try:
        WebDriverWait(driver, wait_time).until(
            EC.visibility_of_element_located(
                (By.XPATH, '//div[@id="market_commodity_buyrequests"]')
            )
        )
        WebDriverWait(driver, wait_time).until(
            EC.visibility_of_element_located(
                (By.XPATH, '//span[@class="market_commodity_orders_header_promote"]')
            )
        )
        WebDriverWait(driver, wait_time).until(
            EC.visibility_of_element_located(
                (
                    By.XPATH,
                    '//span[@class="market_listing_price market_listing_price_with_fee"]',
                )
            )
        )
        WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    '//span[@class="market_listing_price market_listing_price_without_fee"]',
                )
            )
        )
    except:
        pass

    buy_orders_text = []
    current_min_price_with_fee_text = []
    # current_min_price_without_fee_text = None

    try:
        buy_orders_text = [
            element.text
            for element in driver.find_elements(
                By.CLASS_NAME, "market_commodity_orders_header_promote"
            )
        ]
        current_min_price_with_fee_text = [
            element.text
            for element in driver.find_elements(
                By.CLASS_NAME, "market_listing_price.market_listing_price_with_fee"
            )
        ]
        # current_min_price_without_fee_text = [element.text for element in driver.find_elements(By.CLASS_NAME, "market_listing_price.market_listing_price_without_fee")]
    except:
        pass
    soup = BeautifulSoup(page, "html.parser")
    message = soup.find_all("div", class_="market_listing_table_message")
    # print("-----------------")
    # print(buy_orders_text)
    # print(soup.find_all('div', id='market_commodity_buyrequests'))
    # print(current_min_price_with_fee_text)
    # print(current_min_price_without_fee_text)
    # print(message)
    # print("+++++++++++++++++")
    # TODO: Clean up this mess
    if len(message) == 0:
        message_div = soup.find("div", id="message")
        if message_div is not None:
            message = message_div.find("h3").text.strip()
    data = {
        "buy_orders": buy_orders_text,
        "current_min_price_with_fee": current_min_price_with_fee_text,
        #'current_min_price_without_fee': current_min_price_without_fee_text,
        "message": message,
    }
    return data


def extract_knife_data_with_retry(
    driver: WebDriver, url: str, wait_time: int
) -> Optional[ExtractedData]:
    # Function to extract knife data with retries
    retries = 3
    data = None
    for _ in range(retries):
        data = extract_knife_data(driver, url, wait_time)
        message = data.get("message")
        if isinstance(message, str):
            if "many" not in message:
                return data
        else:
            if (
                not message
                or "error" not in message[0].text
                or "too many requests" not in message[0].text
            ):
                return data
        if (
            message
            and message[0]
            and message[0].text
            and "no listings" in message[0].text
        ):
            return data
        time.sleep(10)
        # driver.implicitly_wait(30)  # Wait for 30 seconds before retrying
    return data


def get_and_save_historical_pricing(
    driver: WebDriver,
    cursor: MySQLCursor,
    connection: MySQLConnection,
    knife_id: int,
    name: str,
    logger: CustomLogger,
) -> Tuple[Optional[float], Optional[datetime]]:
    max_attempts = 3
    current_attempt = 0
    console_log_result = None
    price = None
    parsed_date = None
    while current_attempt < max_attempts:
        try:
            console_log_result = driver.execute_script(
                """
                var result = {
                    data: g_plotPriceHistory && g_plotPriceHistory.data && g_plotPriceHistory.data[0],
                };
                return JSON.stringify(result);
                """
            )
        except Exception as e:
            logger.error(f"Could not get historical pricing {name}" + str(e))
            current_attempt += 1
            time.sleep(current_attempt)
            continue

        # If data is not null, break out of the loop
        if console_log_result is not None:
            if json.loads(console_log_result).get("data") is not None:
                break

        current_attempt += 1
        time.sleep(current_attempt)
        # driver.implicitly_wait(current_attempt)  # Adjust the wait time as needed
    if console_log_result is None:
        logger.error(f"Could not execute javascript {name}")
        return None, None
    console_log_result_json = json.loads(console_log_result)
    data = console_log_result_json.get("data")  # date, price, count
    if data is None:
        logger.error(f"Could not parse json {name}")
        return None, None

    date_format = "%b %d %Y %H"
    knife = get_knife_from_db(cursor, name)
    if knife and knife.last_sold:  # Check if it exists and has a date
        knife_date = knife.last_sold
        knife_last_date = data[len(data) - 1][0][:-4]
        parsed_knife_last_date = datetime.strptime(knife_last_date, date_format)

        dates = [datetime.strptime(entry[0][:-4], date_format) for entry in data]

        index = bisect_left(dates, knife_date)

        filtered_data = data[index + 1 :]
        if knife_date < parsed_knife_last_date:
            price, parsed_date = get_and_save_historical_pricing_helper(
                filtered_data, date_format, cursor, connection, knife_id
            )
        else:
            parsed_date = parsed_knife_last_date
            if knife.last_min_price_with_fee is not None:
                price = float(knife.last_min_price_with_fee)
    else:
        price, parsed_date = get_and_save_historical_pricing_helper(
            data, date_format, cursor, connection, knife_id
        )
    return price, parsed_date


def get_knife_info(
    name: str,
    driver: WebDriver,
    cursor: MySQLCursor,
    connection: MySQLConnection,
    wait_time: int,
    logger: CustomLogger,
) -> Optional[Knife]:
    url = f"https://steamcommunity.com/market/listings/730/{name}"
    # logger.info(f"Processing knife {name}")

    # Extract knife data with retries
    data = extract_knife_data_with_retry(driver, url, wait_time)
    if data is None:
        return None
    current_price = True
    # Handle cases where there is an error message
    if isinstance(data.get("message"), str):
        if "made too many requests" in data.get("message"):
            # TODO: The detection is somehow wrong idk... steam bans us but we can continue anyways
            logger.fatal(f"Too many requests {name}, stopping...")
            logger.fatal(f"Message: {data.get('message')}")
            exit(1)
            return None

        if "no listings" in data.get("message"):
            current_price = False
    else:
        if data.get("message") and data.get("message")[0].text:
            if "no listings" in data.get("message")[0].text:
                current_price = False
            else:
                # TODO: Add a retry mechanism
                logger.error(f"Steam buggin {name}")
                time.sleep(10)
                return None

    # Handle cases where required data is not available
    buy_orders = True
    if len(data.get("buy_orders")) < 2:
        # print(data['buy_orders'])
        logger.error(f"No buy orders {name}")
        buy_orders = False
        # return None

    if len(data.get("current_min_price_with_fee")) < 1:
        logger.info(f"No current price {name}")
        current_price = False
        # return None

    current_min_price_with_fee = None
    # current_min_price_without_fee = None
    if current_price:
        text = (
            data.get("current_min_price_with_fee")[0]
            .strip()
            .replace(",", ".")
            .replace("-", "0")
            .replace("€", "")
            .replace(" ", "")
        )
        try:
            current_min_price_with_fee = float(text)
        except Exception as e:
            logger.error(
                f"Could not parse current_min_price_with_fee {text} for knife {name}: {e}"
            )

        # text = data['current_min_price_without_fee'][0].replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip()
        # try:
        #    current_min_price_without_fee = float(text)
        # except Exception as e:
        #    logging.error(f"Could not parse current_min_price_without_fee {text} for knife {name}: {e}")

    buy_order_price = None
    if buy_orders:
        buy_order_price = float(
            data.get("buy_orders")[1]
            .replace(",", ".")
            .replace("-", "0")
            .replace("€", "")
            .replace(" ", "")
            .strip()
        )

    # Extract knife_id using regular expression
    knife_id = None
    desired_line = None
    script_tags = driver.find_elements(By.TAG_NAME, "script")

    for script_tag in script_tags:
        try:
            # Attempt to get the JavaScript code from the script tag
            javascript_code = script_tag.get_attribute("text")

            # Check for the specific pattern in the JavaScript code
            if javascript_code and "Market_LoadOrderSpread" in javascript_code:
                desired_line = javascript_code.strip()
                break

        except StaleElementReferenceException:
            # If stale, re-locate the elements and try again
            script_tags = driver.find_elements(By.TAG_NAME, "script")
            continue  # Retry processing the elements after refinding them

    if desired_line:
        match = re.search(r"Market_LoadOrderSpread\(\s*(\d+)\s*\)", desired_line)
        if match:
            knife_id = match.group(1)

    # if knife_id:
    #    print("--------------")
    #    print(knife_id, name)
    #    print("++++++++++++++")
    #    cursor.execute("UPDATE knives SET knife_id = %s WHERE knives.knife_name = %s", (knife_id, name)) # Stupid HACK
    #    connection.commit()
    if knife_id is None:
        return None
    knife_id = int(knife_id)
    last_min_price_with_fee, last_sold = get_and_save_historical_pricing(
        driver, cursor, connection, knife_id, name, logger
    )
    if last_min_price_with_fee is None or last_sold is None:
        logger.error(f"Could not process price history {name}")
    last_min_price_without_fee = None
    if last_min_price_with_fee is not None:
        last_min_price_without_fee = last_min_price_with_fee / 1.15

    current_min_price_without_fee = None
    if current_min_price_with_fee is not None:
        current_min_price_without_fee = current_min_price_with_fee / 1.15

    knife = Knife(
        name,
        knife_id,
        current_min_price_with_fee,
        current_min_price_without_fee,
        last_min_price_with_fee,
        last_min_price_without_fee,
        buy_order_price,
        last_sold,
    )
    return knife


def safe_get_knife_info(
    name: Tuple[str],
    driver: WebDriver,
    cursor: MySQLCursor,
    connection: MySQLConnection,
    wait_time: int,
    logger: CustomLogger,
) -> Optional[Knife]:
    """A wrapper that catches and logs errors for get_knife_info."""
    try:
        knife_info = get_knife_info(
            name[0], driver, cursor, connection, wait_time, logger
        )
        return knife_info
    except Exception as e:
        logger.error(f"Error processing knife {name[0]}: {e}")
        return None  # Return None or handle as needed


def initialize_driver(headless: bool, logger: CustomLogger) -> Tuple[WebDriver, str]:
    project_root = Path(
        __file__
    ).parent  # This will get the directory where this script is located
    original_user_data_dir = (
        project_root / "chrome-cache"
    )  # Relative path to 'chrome-cache' directory

    # Ensure the path is valid
    if not original_user_data_dir.exists():
        raise FileNotFoundError(
            f"User data directory {original_user_data_dir} does not exist."
        )

    # Copy the user-data-dir to a new unique directory for this thread
    user_data_dir = copy_user_data_dir(original_user_data_dir, logger, "chrome-cache")

    options = Options()
    if headless:
        options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument(
        f"user-data-dir={user_data_dir}"
    )  # Use the copied unique user data dir per thread

    chrome_driver = webdriver.Chrome(options=options)
    chrome_driver.request_interceptor = interceptor  # Attach interceptor here
    return chrome_driver, user_data_dir


def steam_login() -> WebDriver:
    project_root = Path(
        __file__
    ).parent  # This will get the directory where this script is located
    original_user_data_dir = (
        project_root / "chrome-cache"
    )  # Relative path to 'chrome-cache' directory
    options = Options()
    options.add_argument("--disable-gpu")
    options.add_argument(
        f"user-data-dir={original_user_data_dir}"
    )  # Use the copied unique user data dir per thread
    chrome_driver = webdriver.Chrome(options=options)
    chrome_driver.request_interceptor = interceptor  # Attach interceptor here
    return chrome_driver


def fetch_all_knives_for_thread(
    knife_names: List[Tuple[str]],
    wait_time: int,
    progress_bar: tqdm,
    logger: CustomLogger,
    failed_knives_name: str,
) -> None:
    failed_knives = []
    fail_batch_size = 15
    batch_size = 15
    connection, cursor = connect_to_db_threaded(
        host="localhost",
        database="knives",
        port=DATABASE_PORT,
        user=DATABASE_USERNAME,
        password=DATABASE_PASSWORD,
        logger=logger,
    )
    # Initialize the driver once per thread
    driver, user_data_dir = initialize_driver(True, logger)
    batch = []

    # Store thread-specific resources in thread-local storage
    thread_resources.driver = driver
    thread_resources.connection = connection
    thread_resources.cursor = cursor
    thread_resources.user_data_dir = user_data_dir

    for knife_name in knife_names:
        if shutdown_event.is_set():
            logger.info("Shutdown signal received. Exiting thread...")
            break

        knife_info = safe_get_knife_info(
            knife_name, driver, cursor, connection, wait_time, logger
        )
        if knife_info:
            batch.append(knife_info)
        else:
            failed_knives.append(knife_name[0])

        progress_bar.update(1)
        if len(batch) == batch_size:
            save_knives_to_db(batch, cursor, connection)
            batch.clear()
        if len(failed_knives) == fail_batch_size:
            log_failed_knives(
                failed_knives, failed_knives_name, logger
            )  # Log all failed knives for this batch
            failed_knives.clear()  # Clear the list for the next batch

    if failed_knives:
        log_failed_knives(failed_knives, failed_knives_name, logger)
    driver.quit()  # Quit the driver after all knives in this thread are processed
    shutil.rmtree(user_data_dir)
    connection.close()
    cursor.close()


def process_knives(
    logger: CustomLogger,
    knife_names: List[Tuple[str]],
    failed_knives_name: str,
    wait_time: int = 6,
):
    # Define ThreadPool parameters
    thread_count = 4
    chunk_size = len(knife_names) // thread_count
    knife_name_chunks = [
        knife_names[i : i + chunk_size] for i in range(0, len(knife_names), chunk_size)
    ]

    # Initialize the progress bar
    total_knives = len(knife_names)
    progress_bar = tqdm(total=total_knives, desc="Processing knives", unit="knife")

    # Process chunks with a ThreadPool
    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = [
            executor.submit(
                fetch_all_knives_for_thread,
                chunk,
                wait_time,
                progress_bar,
                logger,
                failed_knives_name,
            )
            for chunk in knife_name_chunks
        ]
        try:
            for future in as_completed(futures):
                if shutdown_event.is_set():
                    break
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt received. Cancelling tasks...")
            for future in futures:
                future.cancel()

    progress_bar.close()  # Close the progress bar after completion


# TODO: Ctrl C to properly close and clean up
def update_all_knife_data(
    failed_knives_name: str,
    logger: CustomLogger,
    date: Optional[str] = None,
    wait_time: int = 6,
) -> None:
    try:
        # Connect to the database
        sql_connection, sql_cursor = connect_to_db(
            "localhost",
            "knives",
            DATABASE_PORT,
            DATABASE_USERNAME,
            DATABASE_PASSWORD,
            logger,
        )

        # Get knife names from the database
        knife_names = get_knife_list_from_db(sql_cursor, date)
        process_knives(logger, knife_names, failed_knives_name, wait_time)

        # Update database with additional calculations
        update_all(sql_cursor)

        # Close database resources
        sql_cursor.close()
        sql_connection.close()

    except KeyboardInterrupt:
        sql_cursor.close()
        sql_connection.close()
        logger.info("\nKeyboardInterrupt caught, initiating shutdown...")
        handle_shutdown(
            None, None, logger
        )  # Trigger the signal handler for a clean shutdown


# Thread-local storage for managing resources for each thread
thread_resources = threading.local()

# An event to signal the threads to stop when interrupted
shutdown_event = threading.Event()


def handle_shutdown(signal, frame, logger: CustomLogger):
    """Signal handler for graceful shutdown on Ctrl+C (SIGINT)."""
    logger.info("\nReceived shutdown signal (Ctrl+C), cleaning up...")

    # Set the shutdown event, which threads can check to gracefully exit
    shutdown_event.set()
    # Perform cleanup for each thread's resources
    for thread in threading.enumerate():
        if hasattr(thread, "resources"):
            resources = thread.resources
            if getattr(resources, "driver", None):
                resources.driver.quit()
            if getattr(resources, "connection", None):
                resources.connection.close()
            if getattr(resources, "cursor", None):
                resources.cursor.close()
            if getattr(resources, "user_data_dir", None):
                shutil.rmtree(resources.user_data_dir)
    # Exit the program cleanly
    sys.exit(0)


# Register the signal handler for SIGINT (Ctrl+C)
# signal.signal(signal.SIGINT, handle_shutdown)
