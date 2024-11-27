from decimal import Decimal
import time
import re
import json
import tempfile
import shutil
import os
import random
import string
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pathlib import Path

import mysql.connector
from bs4 import BeautifulSoup, NavigableString, ResultSet, Tag
from seleniumwire2 import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.chrome.webdriver import WebDriver
from datetime import datetime

from bisect import bisect_left
from tqdm import tqdm

from mysql.connector import Error
from mysql.connector.connection import MySQLConnection
from mysql.connector.cursor import MySQLCursor

from multiprocessing.dummy import Pool as ThreadPool

from CustomLogger import CustomLogger, LogLevel
from Knife import Knife
ExtractedData = Dict[str, List[Any]]


# https://steamcommunity.com/market/listings/730/%E2%98%85%20Survival%20Knife%20%7C%20Crimson%20Web%20%28Factory%20New%29
# WEB SCRAPE IT
# https://steamcommunity.com/market/search?q=&category_730_ItemSet%5B%5D=any&category_730_ProPlayer%5B%5D=any&category_730_StickerCapsule%5B%5D=any&category_730_TournamentTeam%5B%5D=any&category_730_Weapon%5B%5D=any&category_730_Type%5B%5D=tag_CSGO_Type_Knife&appid=730#p1_name_asc

def parse_page(url: str, driver: WebDriver) -> str:
    driver.get(url)
    #driver.implicitly_wait(sleep_time)
    #time.sleep(sleep_time)
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
    request.headers['Access-Control-Allow-Origin'] = '*'

#TODO: Multithread this b
def get_knife_list(driver: WebDriver, wait_time: int) -> Set[str]:
    range_start = 0
    base_url = "https://steamcommunity.com/market/search?q=&category_730_ItemSet[]=any&category_730_ProPlayer[]=any&category_730_StickerCapsule[]=any&category_730_TournamentTeam[]=any&category_730_Weapon[]=any&category_730_Type[]=tag_CSGO_Type_Knife&appid=730#p{}_name_asc"
    knife_name_set = set()
    page = parse_page(base_url.format(range_start + 1), driver)
    WebDriverWait(driver, wait_time).until(
            EC.visibility_of_element_located((By.XPATH, '//span[@class="market_listing_item_name"]'))
    )
    soup = BeautifulSoup(page, "html.parser")
    number_of_pages = 250
    names = soup.find_all('span', class_='market_listing_item_name')
    new_names: List[str] = list()
    for name in names:
        knife_name_set.add(name.text.strip())
    for page_num in tqdm(range(range_start + 2, number_of_pages + 1)):
        page = parse_page(base_url.format(page_num), driver)    
        WebDriverWait(driver, wait_time).until(
            EC.visibility_of_element_located((By.XPATH, '//span[@class="market_listing_item_name"]'))
        )
        soup = BeautifulSoup(page, "html.parser")
        names = soup.find_all('span', class_='market_listing_item_name')
        error = soup.find_all("h3")
        while error or len(names) < 5 or names == new_names:
            #driver.implicitly_wait(30)
            time.sleep(10)
            driver.execute_script("location.reload(true);")
            page = parse_page(base_url.format(page_num), driver)
            WebDriverWait(driver, wait_time).until(
                EC.visibility_of_element_located((By.XPATH, '//span[@class="market_listing_item_name"]'))
            )
            soup = BeautifulSoup(page, "html.parser")
            names = soup.find_all('span', class_='market_listing_item_name')
            error = soup.find_all("h3")
        new_names = names.copy()
        print("\n", len(knife_name_set))
        for name in names:
            knife_name_set.add(name.text.strip())
    return knife_name_set


def add_new_knives_to_db(names: List[str], cursor: MySQLCursor, connection: MySQLConnection) -> None:
    for name in tqdm(names):
        cursor.execute("SELECT knife_name FROM knives WHERE knife_name = (%s)", (name,))
        if cursor.fetchone() is None:
            cursor.execute("INSERT INTO knives (knife_name) VALUES (%s)", (name,))
            connection.commit()

def extract_knife_data(driver: WebDriver, url: str, wait_time: int) -> ExtractedData:
    driver.execute_script("location.reload(true);")
    page = parse_page(url, driver)

    driver.execute_script("window.scrollTo(0, 1000);")
    try:   
        WebDriverWait(driver, wait_time).until(
            EC.visibility_of_element_located((By.XPATH, '//div[@id="market_commodity_buyrequests"]'))
        )
        WebDriverWait(driver, wait_time).until(
            EC.visibility_of_element_located((By.XPATH, '//span[@class="market_commodity_orders_header_promote"]'))
        )
        WebDriverWait(driver, wait_time).until(
            EC.visibility_of_element_located((By.XPATH, '//span[@class="market_listing_price market_listing_price_with_fee"]'))
        )
        WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located((By.XPATH, '//span[@class="market_listing_price market_listing_price_without_fee"]'))
        )
    except:
        pass

    buy_orders_text = []
    current_min_price_with_fee_text = []
    #current_min_price_without_fee_text = None

    try:
        buy_orders_text = [element.text for element in driver.find_elements(By.CLASS_NAME, "market_commodity_orders_header_promote")]
        current_min_price_with_fee_text = [element.text for element in driver.find_elements(By.CLASS_NAME, "market_listing_price.market_listing_price_with_fee")]
        #current_min_price_without_fee_text = [element.text for element in driver.find_elements(By.CLASS_NAME, "market_listing_price.market_listing_price_without_fee")]
    except:
        pass
    soup = BeautifulSoup(page, "html.parser")
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


def extract_knife_data_with_retry(driver: WebDriver, url: str, wait_time: int) -> Optional[ExtractedData]:
    # Function to extract knife data with retries
    retries = 3
    data = None
    for _ in range(retries):
        data = extract_knife_data(driver, url, wait_time)
        if(isinstance(data.get('message'), str)):
            if "many" not in data.get('message'):
                return data
        else:
            if not data.get('message') or "error" not in data.get('message')[0].text or "too many requests" not in data.get('message')[0].text:
                return data
        time.sleep(10)
        #driver.implicitly_wait(30)  # Wait for 30 seconds before retrying
    return data

#TODO: Optimize this b
def get_and_save_historical_pricing_helper(data: List[List], date_format: str, cursor: MySQLCursor, connection: MySQLConnection, knife_id: int) -> Tuple[Optional[float], Optional[datetime]]:
    price = None
    parsed_date = None
    #TODO: Batch insert
    for result in data:
        date_string = result[0][:-4]
        parsed_date = datetime.strptime(date_string, date_format)
        price = result[1]
        sold_count = result[2]
        #TODO:                 sell_time_id
        cursor.execute("SELECT sell_time_id FROM SellTimes WHERE sell_time = (%s)", (parsed_date,))
        existing_date = cursor.fetchone()
        if not existing_date:
            cursor.execute("INSERT INTO SellTimes (sell_time) VALUES (%s)", (parsed_date,))
            connection.commit()
            date_id = cursor.lastrowid
        else:
            value = existing_date[0]
            if isinstance(value, (int, float, Decimal)):
                date_id = int(value)
            elif isinstance(value, str) and value.isdigit():
                date_id = int(value)
            else:
                date_id = None
        #TODO: IGNORE should in theory be unnecessary
        if(date_id is not None):
            cursor.execute("INSERT IGNORE INTO SellHistory (knife_id, sell_time_id, price, quantity) VALUES (%s, %s, %s, %s)", (knife_id, date_id, price, sold_count))
            connection.commit()
    return price, parsed_date


def get_and_save_historical_pricing(driver: WebDriver, cursor: MySQLCursor, connection: MySQLConnection, knife_id: int, name: str, logger: CustomLogger) -> Tuple[Optional[float], Optional[datetime]]:
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
            logger.error("Could not get historical pricing {name}" + str(e))
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

#TODO: Add failed knives to a csv or something
def get_knife_info(name: str, driver: WebDriver, cursor: MySQLCursor, connection: MySQLConnection, wait_time: int, logger: CustomLogger) -> Optional[Knife]:
    url = f"https://steamcommunity.com/market/listings/730/{name}"
    logger.info(f"Processing knife {name}")

    # Extract knife data with retries
    data = extract_knife_data_with_retry(driver, url, wait_time)
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
    script_tags = driver.find_elements(By.TAG_NAME, 'script')

    for script_tag in script_tags:
        try:
            # Attempt to get the JavaScript code from the script tag
            javascript_code = script_tag.get_attribute('text')
            
            # Check for the specific pattern in the JavaScript code
            if javascript_code and 'Market_LoadOrderSpread' in javascript_code:
                desired_line = javascript_code.strip()
                break

        except StaleElementReferenceException:
            # If stale, re-locate the elements and try again
            script_tags = driver.find_elements(By.TAG_NAME, 'script')
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
    last_min_price_with_fee, last_sold = get_and_save_historical_pricing(driver, cursor, connection, knife_id,
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

def safe_get_knife_info(name: Tuple[str], driver: WebDriver, cursor: MySQLCursor, connection: MySQLConnection, wait_time: int, logger: CustomLogger) -> Optional[Knife]:
    """A wrapper that catches and logs errors for get_knife_info."""
    try:
        knife_info = get_knife_info(name[0], driver, cursor, connection, wait_time, logger)
        return knife_info
    except Exception as e:
        logger.error(f"Error processing knife {name[0]}: {e}")
        return None  # Return None or handle as needed

def save_knives_to_db(knives: List[Knife], cursor: MySQLCursor, connection: MySQLConnection) -> None:
    if not knives:
        return
    
    # Prepare the base update query
    base_query = """
        UPDATE knives SET
            current_min_price_with_fee = %s,
            current_min_price_without_fee = %s,
            last_min_price_with_fee = %s,
            last_min_price_without_fee = %s,
            buy_order_price = %s,
            last_updated = %s,
            last_sold = %s
        WHERE knife_name = %s
    """
    
    # Create a list of tuples with values for each knife
    values = []
    for knife in knives:
        # Populate the fields and handle optional fields with None
        values.append((
            knife.current_min_price_with_fee,
            knife.current_min_price_without_fee,
            knife.last_min_price_with_fee,
            knife.last_min_price_without_fee,
            knife.buy_order_price,
            knife.last_updated,
            knife.last_sold,
            knife.knife_name  # WHERE clause value
        ))
    
    # Execute the batch update
    cursor.executemany(base_query, values)
    connection.commit()



def get_knife_list_from_db(cursor: MySQLCursor, date: Optional[str] = None) -> List[Tuple[str]]:
    if(date is None):
        select_query = "SELECT knife_name FROM knives"
    else:
        select_query = f"SELECT knife_name FROM knives WHERE last_updated < {date}"
    cursor.execute(select_query)
    knife_list = cursor.fetchall()
    return knife_list

def get_knife_from_db(cursor: MySQLCursor, name: str) -> Optional[Knife]:
    cursor.execute("SELECT * FROM knives WHERE knife_name = %s", (name,))
    row = cursor.fetchone()  # Fetch a single row
    if row:
        # Create a Knife object from the fetched data
        knife = Knife(
            knife_id=row[0],
            knife_name=row[1],
            current_min_price_with_fee=row[2],
            current_min_price_without_fee=row[3],
            last_min_price_with_fee=row[4],
            last_min_price_without_fee=row[5],
            buy_order_price=row[6],
            last_sold=row[9] if len(row) > 9 else None  # Last sold is optional
        )
        return knife
    else:
        return None  # If no knife is found, return None


def connect_to_db(host: str, database: str, port: int, user: str, password: str, logger: CustomLogger) -> Tuple[MySQLConnection, MySQLCursor]:
    try:
        sql_connection = mysql.connector.connect(host=host, database=database, port=port, user=user, password=password)
        if sql_connection.is_connected():
            sql_cursor = sql_connection.cursor()
        else:
            logger.fatal("SQL connection error, likely invalid connection parameters")
            exit(1)

    except Error as e:
        logger.fatal("SQL connection error " + str(e))
        exit(1)
    return sql_connection, sql_cursor


def update_amount_sold(cursor: MySQLCursor) -> None:
    update_query = '''
    UPDATE `knives`.`Knives` k
    JOIN (
        SELECT `knife_id`, COUNT(*) AS `total_sold`
        FROM `knives`.`SellHistory`
        GROUP BY `knife_id`
    ) sh ON k.`knife_id` = sh.`knife_id`
    SET k.`amount_sold` = sh.`total_sold`;
    '''
    cursor.execute(update_query)


def update_selling_frequency(cursor: MySQLCursor) -> None:
    update_query = '''
    UPDATE `knives`.`Knives` k
    JOIN (
        SELECT sh.`knife_id`, 
            COUNT(*) / TIMESTAMPDIFF(MONTH, MIN(st.`sell_time`), NOW()) AS `frequency`
        FROM `knives`.`SellHistory` sh
        JOIN `knives`.`SellTimes` st ON sh.`sell_time_id` = st.`sell_time_id`
        GROUP BY sh.`knife_id`
    ) sf ON k.`knife_id` = sf.`knife_id`
    SET k.`selling_frequency` = sf.`frequency`;
    '''
    cursor.execute(update_query)

def copy_user_data_dir(source_dir: str) -> str:
    """
    Copy the user data directory to a unique directory for each thread.
    Ensures that the directory name is unique to avoid conflicts.
    """
    while True:
        # Generate a truly unique temporary directory using random suffix
        unique_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        temp_dir = os.path.join(tempfile.gettempdir(), f"chrome_user_data_{unique_suffix}")
        
        # Ensure the directory doesn't already exist
        if not os.path.exists(temp_dir):
            break  # We found a unique directory, break the loop

    # Now create the directory
    os.makedirs(temp_dir)
    # Copy the contents of the source directory to the unique directory
    try:
        shutil.copytree(source_dir, temp_dir, dirs_exist_ok=True)
    except Exception as e:
        logger.error(f"Error copying directory: {e}")
        shutil.rmtree(temp_dir)  # Cleanup in case of failure
        raise e

    return temp_dir

def initialize_driver(headless: bool) -> Tuple[WebDriver, str]:
    project_root = Path(__file__).parent  # This will get the directory where this script is located
    original_user_data_dir = project_root / "chrome-cache"  # Relative path to 'chrome-cache' directory

    # Ensure the path is valid
    if not original_user_data_dir.exists():
        raise FileNotFoundError(f"User data directory {original_user_data_dir} does not exist.")

    # Copy the user-data-dir to a new unique directory for this thread
    user_data_dir = copy_user_data_dir(original_user_data_dir)
    
    options = Options()
    if(headless):
        options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument(f"user-data-dir={user_data_dir}")  # Use the copied unique user data dir per thread
    
    chrome_driver = webdriver.Chrome(options=options)
    chrome_driver.request_interceptor = interceptor  # Attach interceptor here
    return chrome_driver, user_data_dir

def connect_to_db_threaded() -> Tuple[MySQLConnection, MySQLCursor]:
    # Create a new connection per thread
    return connect_to_db('localhost', 'knives', 3306, 'root', '', logger)

def fetch_all_knives_for_thread(knife_names: List[Tuple[str]], wait_time: int, progress_bar: tqdm, logger: CustomLogger) -> None:
    batch_size = 15
    connection, cursor = connect_to_db_threaded()
    # Initialize the driver once per thread
    driver, user_data_dir = initialize_driver(True)
    batch = list()
    for knife_name in knife_names:
        knife_info = safe_get_knife_info(knife_name, driver, cursor, connection, wait_time, logger)
        if knife_info:
            batch.append(knife_info)
            
        progress_bar.update(1)
        if(len(batch) == batch_size):
            save_knives_to_db(batch, cursor, connection)
            batch.clear()

    driver.quit()  # Quit the driver after all knives in this thread are processed
    shutil.rmtree(user_data_dir)

#TODO: Ctrl C to properly close and clean up
def update_all_knife_data(date: Optional[str] = None, wait_time: int = 6) -> None:
    sql_connection, sql_cursor = connect_to_db('localhost', 'knives', 3306, 'root', '', logger)

    knife_names = get_knife_list_from_db(sql_cursor, date)
    #get_knife_info("★ Bayonet", chrome_driver, sql_cursor, sql_connection)
    #time.sleep(1000)
    #for knife_name in tqdm(knife_names):
        #try:
        #knife_info = get_knife_info(knife_name[0], chrome_driver, sql_cursor, sql_connection, wait_time)
        #save_knife_to_db([knife_info], sql_cursor, sql_connection)
        #except Error as e:
        #    print(f"Greška u azuriranju noza {knife_name[0]}", e)
        #    continue
    # Define the ThreadPool and number of threads
    thread_count = 4
    chunk_size = len(knife_names) // thread_count
    knife_name_chunks = [knife_names[i:i + chunk_size] for i in range(0, len(knife_names), chunk_size)]

    # Initialize tqdm for the overall progress of knives
    total_knives = len(knife_names)
    progress_bar = tqdm(total=total_knives, desc="Processing knives", unit="knife")
    # Function to update progress bar as each chunk finishes
    with ThreadPool(thread_count) as pool:
        # Use imap_unordered to process the chunks and update progress for each chunk
        for _ in pool.imap_unordered(lambda chunk: fetch_all_knives_for_thread(chunk, wait_time, progress_bar, logger), knife_name_chunks):
            pass

    progress_bar.close()  # Close the progress bar after completion

    pool.close()
    pool.join()
    
    update_amount_sold(sql_cursor)
    update_selling_frequency(sql_cursor)

    sql_cursor.close()
    sql_connection.close()


if __name__ == "__main__":
    logger = CustomLogger(log_file="knives.log", log_level=LogLevel.INFO)

    # Update the StreamHandler to explicitly use UTF-8 encoding
    update_all_knife_data()
    #connection, cursor = connect_to_db('localhost', 'knives', 3306, 'root', '', logger)
    #knife_name = "★ Shadow Daggers | Marble Fade (Minimal Wear)"
    #driver, user_data_dir = initialize_driver(False)
    #knife_info = safe_get_knife_info((knife_name, ), driver, cursor, connection, 6, logger)
    #driver.quit()
    #shutil.rmtree(user_data_dir)
    #update_all_knife_data("'2024-11-03'")
    #get_knife_info("★ StatTrak™ Flip Knife | Bright Water (Battle-Scarred)")
    # Update Knife List
    # knife_names = get_knife_list(driver)
    # add_new_knives_to_db(knife_names, cursor)
    # exit()
