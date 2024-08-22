import datetime
import time
import re
import json

import mysql.connector
from bs4 import BeautifulSoup
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from tqdm import tqdm
from mysql.connector import Error


# https://steamcommunity.com/market/listings/730/%E2%98%85%20Survival%20Knife%20%7C%20Crimson%20Web%20%28Factory%20New%29
# WEB SCRAPE IT
# https://steamcommunity.com/market/search?q=&category_730_ItemSet%5B%5D=any&category_730_ProPlayer%5B%5D=any&category_730_StickerCapsule%5B%5D=any&category_730_TournamentTeam%5B%5D=any&category_730_Weapon%5B%5D=any&category_730_Type%5B%5D=tag_CSGO_Type_Knife&appid=730#p1_name_asc

def parse_page(url, driver, sleep_time):
    driver.get(url)
    time.sleep(sleep_time)
    page = driver.page_source
    return page


def interceptor(request):
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


def get_knife_list(driver):
    range_start = 0
    base_url = "https://steamcommunity.com/market/search?q=&category_730_ItemSet[]=any&category_730_ProPlayer[]=any&category_730_StickerCapsule[]=any&category_730_TournamentTeam[]=any&category_730_Weapon[]=any&category_730_Type[]=tag_CSGO_Type_Knife&appid=730#p{}_name_asc"
    knife_name_set = set()
    page = parse_page(base_url.format(range_start + 1), driver, 6)
    soup = BeautifulSoup(page, "html.parser")
    number_of_pages = 250
    names = soup.find_all('span', class_='market_listing_item_name')
    new_names = []
    for name in names:
        knife_name_set.add(name.text.strip())
    for page_num in tqdm(range(range_start + 2, number_of_pages + 1)):
        page = parse_page(base_url.format(page_num), driver, 6)
        soup = BeautifulSoup(page, "html.parser")
        names = soup.find_all('span', class_='market_listing_item_name')
        error = soup.find_all("h3")
        while error or len(names) < 5 or names == new_names:
            time.sleep(30)
            driver.execute_script("location.reload(true);")
            page = parse_page(base_url.format(page_num), driver, 6)
            soup = BeautifulSoup(page, "html.parser")
            names = soup.find_all('span', class_='market_listing_item_name')
            error = soup.find_all("h3")
        new_names = names.copy()
        print("\n", len(knife_name_set))
        for name in names:
            knife_name_set.add(name.text.strip())
    return knife_name_set


def add_new_knives_to_db(names, cursor, connection):
    for name in tqdm(names):
        cursor.execute("SELECT knife_name FROM knives WHERE knife_name = (%s)", (name,))
        if cursor.fetchone() is None:
            cursor.execute("INSERT INTO knives (knife_name) VALUES (%s)", (name,))
            connection.commit()


def extract_knife_data(driver, url):
    driver.execute_script("location.reload(true);")
    page = parse_page(url, driver, 6)
    soup = BeautifulSoup(page, "html.parser")
    buy_orders = soup.find_all('span', class_='market_commodity_orders_header_promote')
    current_min_price_with_fee = soup.find_all('span', class_='market_listing_price market_listing_price_with_fee')
    current_min_price_without_fee = soup.find_all('span',
                                                  class_='market_listing_price market_listing_price_without_fee')
    message = soup.find_all('div', class_='market_listing_table_message')
    data = {
        'buy_orders': buy_orders,
        'current_min_price_with_fee': current_min_price_with_fee,
        'current_min_price_without_fee': current_min_price_without_fee,
        'message': message
    }
    return data


def extract_knife_data_with_retry(driver, url):
    # Function to extract knife data with retries
    retries = 3
    data = None
    for _ in range(retries):
        data = extract_knife_data(driver, url)
        if not data['message'] or "error" not in data['message'][0].text:
            return data
        time.sleep(30)  # Wait for 30 seconds before retrying
    return data


def get_and_save_historical_pricing_helper(console_log_result_json, date_format, cursor, connection, knife_id):
    price = None
    parsed_date = None
    for result in console_log_result_json['data']:
        date_string = result[0][:-4]
        parsed_date = datetime.datetime.strptime(date_string, date_format)
        price = result[1]
        sold_count = result[2]
        cursor.execute("SELECT * FROM SellTimes WHERE sell_time = (%s)", (parsed_date,))
        existing_date = cursor.fetchone()
        if not existing_date:
            cursor.execute("INSERT INTO SellTimes (sell_time) VALUES (%s)", (parsed_date,))
            connection.commit()
            date_id = cursor.lastrowid
        else:
            date_id = existing_date[0]
        cursor.execute("INSERT IGNORE INTO SellHistory (knife_id, sell_time_id, price, quantity) VALUES (%s, %s, %s, %s)", (knife_id, date_id, price, sold_count))
        connection.commit()
    return price, parsed_date


def get_and_save_historical_pricing(driver, cursor, connection, knife_id, name):
    max_attempts = 3
    current_attempt = 0
    console_log_result = None
    price = None
    parsed_date = None
    while current_attempt < max_attempts:
        console_log_result = driver.execute_script(
            """
            var result = {
                data: g_plotPriceHistory && g_plotPriceHistory.data && g_plotPriceHistory.data[0],
            };
            return JSON.stringify(result);
            """
        )

        # If data is not null, break out of the loop
        if console_log_result is not None:
            if json.loads(console_log_result)['data'] is not None:
                break

        current_attempt += 1
        time.sleep(1)  # Adjust the wait time as needed

    date_format = "%b %d %Y %H"
    # Parse the JSON string back to a Python object
    if console_log_result is not None:
        console_log_result_json = json.loads(console_log_result)
        if console_log_result_json['data'] is not None:
            knife = get_knife_from_db(cursor, name)
            if knife and knife[9]:
                knife_date = knife[9]
                knife_last_date = console_log_result_json['data'][len(console_log_result_json['data']) - 1][0][:-4]
                parsed_knife_last_date = datetime.datetime.strptime(knife_last_date, date_format)
                if knife_date < parsed_knife_last_date:
                    price, parsed_date = get_and_save_historical_pricing_helper(console_log_result_json, date_format,
                                                                                cursor, connection,
                                                                                knife_id)  # OPTIMIZE THIS SHIT
            else:
                price, parsed_date = get_and_save_historical_pricing_helper(console_log_result_json, date_format,
                                                                            cursor, connection, knife_id)
    return price, parsed_date


def get_knife_info(name, driver, cursor, connection):
    url = f"https://steamcommunity.com/market/listings/730/{name}"

    # Extract knife data with retries
    data = extract_knife_data_with_retry(driver, url)

    # Handle cases where there is an error message
    if data['message'] and "error" in data['message'][0].text:
        return None

    # Handle cases where required data is not available
    if len(data['buy_orders']) < 2 or len(data['current_min_price_with_fee']) < 1 or len(
            data['current_min_price_without_fee']) < 1:
        return None
    text = data['current_min_price_with_fee'][0].text.strip().replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "")

    try:
        current_min_price_with_fee = float(text)
    except:
        return None

    buy_order_price = float(
        data['buy_orders'][1].text.replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip())

    # Extract knife_id using regular expression
    knife_id = None
    desired_line = None
    script_tags = driver.find_elements(By.TAG_NAME, 'script')

    for script_tag in script_tags:
        javascript_code = script_tag.get_attribute('text')
        if 'Market_LoadOrderSpread' in javascript_code:
            desired_line = javascript_code.strip()
            break

    if desired_line:
        match = re.search(r'Market_LoadOrderSpread\(\s*(\d+)\s*\)', desired_line)
        if match:
            knife_id = match.group(1)

    if knife_id:
        cursor.execute("UPDATE knives SET knife_id = %s WHERE knives.knife_name = %s", (knife_id, name)) # Stupid HACK
        connection.commit()

    last_min_price_with_fee, last_sold = get_and_save_historical_pricing(driver, cursor, connection, knife_id,
                                                                         name)
    if last_min_price_with_fee is None or last_sold is None:
        return None
    new_knife = {
        'knife_name': name,
        'knife_id': knife_id,
        'current_min_price_with_fee': current_min_price_with_fee,
        'current_min_price_without_fee': float(
            data['current_min_price_without_fee'][0].text.replace(",", ".").replace("-", "0").replace("€", "").replace(
                " ", "").strip()),
        'last_min_price_with_fee': last_min_price_with_fee,
        'last_min_price_without_fee': last_min_price_with_fee / 1.15,
        'buy_order_price': buy_order_price,
        'last_updated': datetime.datetime.now(),
        'last_sold': last_sold
    }
    return new_knife


def save_knife_to_db(knife, cursor, connection):
    if knife:
        cursor.execute(
            """
            UPDATE knives SET
                knife_id = %s, current_min_price_with_fee = %s, current_min_price_without_fee = %s,
                last_min_price_with_fee = %s, last_min_price_without_fee = %s, buy_order_price = %s,
                last_updated = %s, last_sold = %s
            WHERE knife_name = %s
            """,
            (
                knife['knife_id'], knife['current_min_price_with_fee'], knife['current_min_price_without_fee'],
                knife['last_min_price_with_fee'], knife['last_min_price_without_fee'], knife['buy_order_price'],
                knife['last_updated'], knife['last_sold'], knife['knife_name']
            )
        )
        connection.commit()


def get_knife_list_from_db(cursor):
    select_query = "SELECT knife_name FROM knives"
    cursor.execute(select_query)
    knife_list = cursor.fetchall()
    return knife_list


def get_knife_from_db(cursor, name):
    cursor.execute("SELECT * FROM knives WHERE knife_name = %s", (name,))
    return cursor.fetchone()


def connect_to_db(host, database, port, user, password):
    try:
        sql_connection = mysql.connector.connect(host=host, database=database, port=port, user=user, password=password)
        if sql_connection.is_connected():
            sql_cursor = sql_connection.cursor()
        else:
            print("Greška u konekciji")
            exit(1)

    except Error as e:
        print("Greška u konekciji ", e)
        exit(1)
    return sql_connection, sql_cursor


def update_amount_sold(cursor):
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


def update_selling_frequency(cursor):
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


def update_all_knife_data():
    sql_connection, sql_cursor = connect_to_db('localhost', 'knives', '3306', 'root', '')
    knife_names = get_knife_list_from_db(sql_cursor)    
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument("user-data-dir=C:/Filip_projekti/steam amrket boi/chrome-cache")
    cloud_options = {'goog:loggingPrefs': {'browser': 'ALL'}}
    options.set_capability('cloud:options', cloud_options)
    chrome_driver = webdriver.Chrome(options=options)
    chrome_driver.request_interceptor = interceptor
    for knife_name in tqdm(knife_names):
        try:
            knife_info = get_knife_info(knife_name[0], chrome_driver, sql_cursor, sql_connection)
            save_knife_to_db(knife_info, sql_cursor, sql_connection)
        except Error as e:
            print(f"Greška u azuriranju noza {knife_name[0]}", e)
            continue

    update_amount_sold(sql_cursor)
    update_selling_frequency(sql_cursor)

    sql_cursor.close()
    sql_connection.close()


if __name__ == "__main__":
    update_all_knife_data()

    # Update Knife List
    # knife_names = get_knife_list(driver)
    # add_new_knives_to_db(knife_names, cursor)
    # exit()
