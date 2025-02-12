from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import List, Optional, Tuple
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page
from tqdm import tqdm
from common import copy_user_data_dir, load_failed_knives_csv, log_failed_knives
from db_operations import connect_to_db, connect_to_db_threaded, get_and_save_historical_pricing_helper, get_knife_from_db, get_knife_list_from_db, save_knives_to_db, update_all
from selenium_scraper import ExtractedData
from mysql.connector.connection import MySQLConnection
from mysql.connector.cursor import MySQLCursor

from CustomLogger import CustomLogger
from Knife import Knife

def extract_knife_data(page: Page, url: str, wait_time: int) -> ExtractedData:
    #page.evaluate("location.reload(true);") #TODO: May not be necessary
    #page = parse_page(url, driver)
    page.goto(url)
    #page.evaluate("window.scrollTo(0, 1000);")
    
    # We have a bit of a catch 22 here where we wait for the elements that may not exist, but checking for the other element which shows the fact that they dont exist takes the same amount of time
    try:
        page.wait_for_selector('//div[@id="market_commodity_buyrequests"]', state="visible", timeout=wait_time * 1000)
        page.wait_for_selector('//span[@class="market_commodity_orders_header_promote"]', state="visible", timeout=wait_time * 1000)
        page.wait_for_selector('//span[@class="market_listing_price market_listing_price_with_fee"]', state="visible", timeout=wait_time * 1000)
        page.wait_for_selector('//span[@class="market_listing_price market_listing_price_without_fee"]', state="attached", timeout=wait_time * 1000)
    except:
        pass
    buy_orders_text = []
    current_min_price_with_fee_text = []
    #current_min_price_without_fee_text = None

    try:
        buy_orders_elements = page.query_selector_all('.market_commodity_orders_header_promote')
        buy_orders_text = [element.inner_text() for element in buy_orders_elements]
        current_min_price_with_fee_elements = page.query_selector_all('.market_listing_price.market_listing_price_with_fee')
        current_min_price_with_fee_text = [element.inner_text() for element in current_min_price_with_fee_elements]
        #current_min_price_without_fee_text = [element.text for element in driver.find_elements(By.CLASS_NAME, "market_listing_price.market_listing_price_without_fee")]
    except:
        pass
    soup = BeautifulSoup(page.content(), "html.parser")   
    message = soup.find_all('div', class_='market_listing_table_message')
    #print("-----------------")
    #print(buy_orders_text)
    #print(soup.find_all('div', id='market_commodity_buyrequests'))
    #print(current_min_price_with_fee_text)
    #print(current_min_price_without_fee_text)
    #print(message)
    #print("+++++++++++++++++")
    #TODO: Clean up this mess
    if len(message) == 0:
        message_div = soup.find('div', id='message')
        if(message_div is not None):
            message = message_div.find('h3').text.strip()
    data = {
        'buy_orders': buy_orders_text,
        'current_min_price_with_fee': current_min_price_with_fee_text,
        #'current_min_price_without_fee': current_min_price_without_fee_text,
        'message': message
    }
    return data

def extract_knife_data_with_retry(page: Page, url: str, wait_time: int) -> Optional[ExtractedData]:
    # Function to extract knife data with retries
    retries = 3
    data = None
    for _ in range(retries):
        data = extract_knife_data(page, url, wait_time)
        message = data.get('message')
        if(isinstance(message, str)):
            if "many" not in message:
                return data
        else:
            if not message or "error" not in message[0].text or "too many requests" not in message[0].text:
                return data
        if message and message[0] and not isinstance(message[0], str) and message[0].text and "no listings" in message[0].text:
            return data
        time.sleep(10)
        #driver.implicitly_wait(30)  # Wait for 30 seconds before retrying
    return data

def get_and_save_historical_pricing(page: Page, cursor: MySQLCursor, connection: MySQLConnection, knife_id: int, name: str, logger: CustomLogger) -> Tuple[Optional[float], Optional[datetime]]:
    max_attempts = 3
    current_attempt = 0
    console_log_result = None
    price = None
    parsed_date = None
    while current_attempt < max_attempts:
        try:
            console_log_result = page.evaluate(
                """
                (function() {
                    var result = {
                        data: g_plotPriceHistory && g_plotPriceHistory.data && g_plotPriceHistory.data[0],
                    };
                    return JSON.stringify(result);
                })()
                """
            )
        except Exception as e:
            logger.error(f"Could not get historical pricing {name}" + str(e))
            current_attempt += 1
            time.sleep(current_attempt)
            continue

        # If data is not null, break out of the loop
        if console_log_result is not None:
            if json.loads(console_log_result).get('data') is not None:
                break

        current_attempt += 1
        time.sleep(current_attempt)
        #driver.implicitly_wait(current_attempt)  # Adjust the wait time as needed
    if console_log_result is None:
        logger.error(f"Could not execute javascript {name}")
        return None, None
    console_log_result_json = json.loads(console_log_result)
    data = console_log_result_json.get('data') #date, price, count
    if data is None:
        print(console_log_result_json)
        logger.error(f"Could not parse json {name}")
        return None, None
    
    date_format = "%b %d %Y %H"
    knife = get_knife_from_db(cursor, name)
    if knife and knife.last_sold: #Check if it exists and has a date
        knife_date = knife.last_sold
        knife_last_date = data[len(data) - 1][0][:-4]
        parsed_knife_last_date = datetime.strptime(knife_last_date, date_format)

        dates = [datetime.strptime(entry[0][:-4], date_format) for entry in data]

        index = bisect_left(dates, knife_date)

        filtered_data = data[index + 1:]
        if knife_date < parsed_knife_last_date:
            price, parsed_date = get_and_save_historical_pricing_helper(filtered_data, date_format,
                                                                        cursor, connection,
                                                                        knife_id)
        else:
            parsed_date = parsed_knife_last_date
            if(knife.last_min_price_with_fee is not None):
                price = float(knife.last_min_price_with_fee)
    else:
        price, parsed_date = get_and_save_historical_pricing_helper(data, date_format, cursor, connection, knife_id)
    return price, parsed_date

def get_knife_info(name: str, page: Page, cursor: MySQLCursor, connection: MySQLConnection, wait_time: int, logger: CustomLogger) -> Optional[Knife]:
    url = f"https://steamcommunity.com/market/listings/730/{name}"
    #logger.info(f"Processing knife {name}")

    # Extract knife data with retries
    data = extract_knife_data_with_retry(page, url, wait_time)
    if(data is None):
        return None
    current_price = True
    # Handle cases where there is an error message
    if(isinstance(data.get('message'), str)):
        if "made too many requests" in data.get('message'):
            #TODO: The detection is somehow wrong idk... steam bans us but we can continue anyways
            logger.fatal(f"Too many requests {name}, stopping...")
            logger.fatal(f"Message: {data.get('message')}")
            exit(1)
            return None
        
        if "no listings" in data.get('message'):
            current_price = False
    else:
        if data.get('message') and data.get('message')[0].text:
            if("no listings" in data.get('message')[0].text):
                current_price = False
            else:
                #TODO: Add a retry mechanism
                logger.error(f"Steam buggin {name}")
                time.sleep(10)
                return None

    # Handle cases where required data is not available
    buy_orders = True
    if len(data.get('buy_orders')) < 2:
        #print(data['buy_orders'])
        logger.error(f"No buy orders {name}")
        buy_orders = False
        #return None
    
    if len(data.get('current_min_price_with_fee')) < 1:
        logger.info(f"No current price {name}")
        current_price = False
        #return None
    
    current_min_price_with_fee = None
    #current_min_price_without_fee = None
    if(current_price):
        text = data.get('current_min_price_with_fee')[0].strip().replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "")
        try:
            current_min_price_with_fee = float(text)
        except Exception as e:
            logger.error(f"Could not parse current_min_price_with_fee {text} for knife {name}: {e}")
        
        #text = data['current_min_price_without_fee'][0].replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip()
        #try:
        #    current_min_price_without_fee = float(text)
        #except Exception as e:
        #    logging.error(f"Could not parse current_min_price_without_fee {text} for knife {name}: {e}")
        
    buy_order_price = None
    if(buy_orders):
        buy_order_price = float(data.get('buy_orders')[1].replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip())

    # Extract knife_id using regular expression
    knife_id = None
    desired_line = None
    page.wait_for_selector('script', state="attached", timeout=wait_time * 1000)
    script_tags = page.query_selector_all('script')
    for script_tag in script_tags:
        try:
            # Attempt to get the JavaScript code from the script tag
            javascript_code = script_tag.inner_html()
            # Check for the specific pattern in the JavaScript code
            if javascript_code and 'Market_LoadOrderSpread' in javascript_code:
                desired_line = javascript_code.strip()
                break

        except TimeoutError:
            # If stale, re-locate the elements and try again
            script_tags = page.query_selector_all('script')
            continue  # Retry processing the elements after refinding them

    if desired_line:
        match = re.search(r'Market_LoadOrderSpread\(\s*(\d+)\s*\)', desired_line)
        if match:
            knife_id = match.group(1)

    #if knife_id:
    #    print("--------------")
    #    print(knife_id, name)
    #    print("++++++++++++++")
    #    cursor.execute("UPDATE knives SET knife_id = %s WHERE knives.knife_name = %s", (knife_id, name)) # Stupid HACK
    #    connection.commit()
    
    if knife_id is None:
        return None
    knife_id = int(knife_id)
    last_min_price_with_fee, last_sold = get_and_save_historical_pricing(page, cursor, connection, knife_id,
                                                                         name, logger)
    if last_min_price_with_fee is None or last_sold is None:
        logger.error(f"Could not process price history {name}")
    last_min_price_without_fee = None
    if(last_min_price_with_fee is not None):
        last_min_price_without_fee = last_min_price_with_fee / 1.15
    
    current_min_price_without_fee = None
    if(current_min_price_with_fee is not None):
        current_min_price_without_fee = current_min_price_with_fee / 1.15
    
    knife = Knife(
        name,
        knife_id,
        current_min_price_with_fee,
        current_min_price_without_fee,
        last_min_price_with_fee,
        last_min_price_without_fee,
        buy_order_price,
        last_sold
    )
    return knife

def safe_get_knife_info(name: Tuple[str], page: Page, cursor: MySQLCursor, connection: MySQLConnection, wait_time: int, logger: CustomLogger) -> Optional[Knife]:
    """A wrapper that catches and logs errors for get_knife_info."""
    try:
        knife_info = get_knife_info(name[0], page, cursor, connection, wait_time, logger)
        return knife_info
    except Exception as e:
        logger.error(f"Error processing knife {name[0]}: {e}")
        return None  # Return None or handle as needed
def initialize_directory(logger: CustomLogger) -> str:
    project_root = Path(__file__).parent  # This will get the directory where this script is located
    original_user_data_dir = project_root / "playwright_cache"  # Relative path to 'playwright_cache' directory
    # Ensure the path is valid
    if not original_user_data_dir.exists():
        raise FileNotFoundError(f"User data directory {original_user_data_dir} does not exist.")

    # Copy the user-data-dir to a new unique directory for this thread
    user_data_dir = copy_user_data_dir(original_user_data_dir, logger, 'playwright_cache')
    #print(user_data_dir)
    return user_data_dir

def fetch_all_knives_for_thread(knife_names: List[Tuple[str]], wait_time: int, progress_bar: tqdm, logger: CustomLogger, failed_knives_name: str) -> None:
    failed_knives = list()
    fail_batch_size = 15
    batch = list()
    batch_size = 15
    # Store thread-specific resources in thread-local storage

    with sync_playwright() as p:
        connection, cursor = connect_to_db_threaded(host='localhost', database='knives', port=3306, user='root', password='', logger=logger)
        # Initialize the driver once per thread
        user_data_dir = initialize_directory(logger)

        thread_resources.connection = connection
        thread_resources.cursor = cursor
        thread_resources.user_data_dir = user_data_dir

        browser = p.chromium.launch_persistent_context(user_data_dir=initialize_directory(logger), headless=False) # Headless True doesnt transfer the log in state properly
        page = browser.new_page()

        thread_resources.page = page

        for knife_name in knife_names:
            if shutdown_event.is_set():
                logger.info("Shutdown signal received. Exiting thread...")
                break

            knife_info = safe_get_knife_info(knife_name, page, cursor, connection, wait_time, logger)
            if knife_info:
                batch.append(knife_info)
            else:
                failed_knives.append(knife_name[0])
                
            progress_bar.update(1)
            if(len(batch) == batch_size):
                save_knives_to_db(batch, cursor, connection)
                batch.clear()
            if len(failed_knives) == fail_batch_size:
                log_failed_knives(failed_knives, failed_knives_name, logger)  # Log all failed knives for this batch
                failed_knives.clear()  # Clear the list for the next batch
    
    if failed_knives:
        log_failed_knives(failed_knives, failed_knives_name, logger)
    browser.close()  # Quit the driver after all knives in this thread are processed
    shutil.rmtree(user_data_dir)
    connection.close()
    cursor.close()

def process_knives(logger: CustomLogger, knife_names: List[Tuple[str]], failed_knives_name: str, wait_time: int = 6):
    
    # Define ThreadPool parameters
    thread_count = 6
    chunk_size = len(knife_names) // thread_count
    knife_name_chunks = [knife_names[i:i + chunk_size] for i in range(0, len(knife_names), chunk_size)]

    # Initialize the progress bar
    total_knives = len(knife_names)
    progress_bar = tqdm(total=total_knives, desc="Processing knives", unit="knife")

    # Process chunks with a ThreadPool
    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = [executor.submit(fetch_all_knives_for_thread, chunk, wait_time, progress_bar, logger, failed_knives_name) 
                for chunk in knife_name_chunks]
        try:
            for future in as_completed(futures):
                if shutdown_event.is_set():
                    break
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt received. Cancelling tasks...")
            for future in futures:
                future.cancel()

    progress_bar.close()  # Close the progress bar after completion

def update_all_knife_data(failed_knives_name: str, logger: CustomLogger, date: Optional[str] = None, wait_time: int = 6):
    # Connect to the database
    sql_connection, sql_cursor = connect_to_db('localhost', 'knives', 3306, 'root', '', logger)

    # Get knife names from the database
    #knife_names = get_knife_list_from_db(sql_cursor, date)
    knife_names = load_failed_knives_csv('fk.csv', logger)
    # Update database with additional calculations
    process_knives(logger, knife_names, failed_knives_name, wait_time)
    update_all(sql_cursor)
    failed = load_failed_knives_csv(failed_knives_name, logger)
    os.remove(failed_knives_name)
    retries = 0
    while(retries < MAX_RETRY_COUNT and len(failed) > MAX_FAILED_KNIVES):
        process_knives(logger, failed, failed_knives_name, wait_time)
        update_all(sql_cursor)
        failed = load_failed_knives_csv(failed_knives_name, logger)
        retries+=1
    # Close database resources
    sql_cursor.close()
    sql_connection.close()
def graceful_shutdown(signal_received, frame, sql_cursor, sql_connection):
    # Close database connections
    if sql_cursor:
        print("closing sql_cursor")
        sql_cursor.close()
    if sql_connection:
        print("closing sql_connection")
        sql_connection.close()
    print("closed sql_connection")
    os._exit(0)
shutdown_event = threading.Event()
thread_resources = threading.local()
def steam_login():
    with sync_playwright() as p:
        project_root = Path(__file__).parent  # This will get the directory where this script is located
        original_user_data_dir = project_root / "playwright_cache"  # Relative path to 'playwright_cache' directory
        browser = p.chromium.launch_persistent_context(user_data_dir=original_user_data_dir, headless=False)
        page = browser.new_page()
        page.goto('https://steamcommunity.com/login/home')
        browser.storage_state(path="storage.json")
        input("Press Enter after completing the login process...")
        browser.close()
if __name__ == "__main__":
    logger = CustomLogger('knives_playwright.log')
    MAX_FAILED_KNIVES = 10
    MAX_RETRY_COUNT = 5
    #steam_login()
    update_all_knife_data('failed_knives.csv', logger)
