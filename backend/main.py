import datetime
import time
import re
import json

import mysql.connector
import requests
import steammarket as sm
import cloudscraper
from bs4 import BeautifulSoup
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from tqdm import tqdm
from mysql.connector import Error
from concurrent.futures import ThreadPoolExecutor

# https://steamcommunity.com/market/listings/730/%E2%98%85%20Survival%20Knife%20%7C%20Crimson%20Web%20%28Factory%20New%29
# WEB SCRAPE IT
# https://steamcommunity.com/market/search?q=&category_730_ItemSet%5B%5D=any&category_730_ProPlayer%5B%5D=any&category_730_StickerCapsule%5B%5D=any&category_730_TournamentTeam%5B%5D=any&category_730_Weapon%5B%5D=any&category_730_Type%5B%5D=tag_CSGO_Type_Knife&appid=730#p1_name_asc

'''
def get_csgo_knife_prices():
    item = sm.get_csgo_item('★ Gut Knife | Doppler (Factory New)')
    print(item)
    for listing in item.listings:
        print(listing.price)
    try:
        response = requests.get(market_url)
        data = response.json()
        knife_prices = {}

        if "response" in data and "assets" in data["response"]:
            assets = data["response"]["assets"]
            for asset in assets:
                name = asset.get("market_hash_name")
                if "Knife" in name:  # Check if the asset is a knife skin
                    buy_orders = asset.get("buy_order_graph")
                    last_sold_price = asset.get("last_sale_price")

                    if buy_orders and last_sold_price:
                        highest_buy_order = max(buy_orders, key=lambda x: x[0])
                        price_difference = highest_buy_order[0] - last_sold_price
                        knife_prices[name] = price_difference

        return knife_prices

    except requests.exceptions.RequestException as e:
        print("Error fetching data:", e)
        return None
    '''

def parse_page(url, driver, sleep_time):
    driver.get(url)
    time.sleep(sleep_time)
    page = driver.page_source
    return page

def interceptor(request):
    '''
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
    '''
    request.headers['Access-Control-Allow-Origin'] = '*'

def get_knife_list(driver):

    range_start = 0
    base_url = "https://steamcommunity.com/market/search?q=&category_730_ItemSet[]=any&category_730_ProPlayer[]=any&category_730_StickerCapsule[]=any&category_730_TournamentTeam[]=any&category_730_Weapon[]=any&category_730_Type[]=tag_CSGO_Type_Knife&appid=730#p{}_name_asc"
    knife_name_set = set()
    page = parse_page(base_url.format(range_start + 1), driver, 6)
    soup = BeautifulSoup(page, "html.parser")
    # pages = soup.find_all('span', class_='market_paging_pagelink')
    # numbers = [int(link.text.strip()) for link in pages]
    # number_of_pages = max(numbers)
    number_of_pages = 250
    names = soup.find_all('span', class_='market_listing_item_name')
    new_names = []
    for name in names:
        knife_name_set.add(name.text.strip())
    for page_num in tqdm(range(range_start + 2, number_of_pages + 1)):
        names = []
        page = parse_page(base_url.format(page_num), driver, 6)
        soup = BeautifulSoup(page, "html.parser")
        names = soup.find_all('span', class_='market_listing_item_name')
        error = soup.find_all("h3")
        while error or len(names) < 5 or names==new_names:
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

    driver.quit()
    return knife_name_set

def add_new_knives_to_db(knife_names, cursor):
    for knife_name in tqdm(knife_names):
        new_knife = {
            'knife_name': knife_name
        }
        select_query = "SELECT knife_name FROM knives WHERE knife_name = (%s)"
        cursor.execute(select_query, (new_knife['knife_name'],))
        existing_knife = cursor.fetchone()
        if not existing_knife:
            # Knife does not exist, insert it into the database
            insert_query = "INSERT INTO knives (knife_name) VALUES (%s)"
            cursor.execute(insert_query, (new_knife['knife_name'],))
            connection.commit()

def extract_knife_data(driver, url):
    
    driver.execute_script("location.reload(true);")
    page = parse_page(url, driver, 6)
    soup = BeautifulSoup(page, "html.parser")
    buy_orders = soup.find_all('span', class_='market_commodity_orders_header_promote')
    current_min_price_with_fee = soup.find_all('span', class_='market_listing_price market_listing_price_with_fee')
    current_min_price_without_fee = soup.find_all('span', class_='market_listing_price market_listing_price_without_fee')
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
    for _ in range(retries):
        data = extract_knife_data(driver, url)
        if not data['message'] or "error" not in data['message'][0].text:
            return data
        time.sleep(30)  # Wait for 30 seconds before retrying
    return data

def get_and_save_historical_pricing(driver, cursor, connection, knife_id):
        
    max_attempts = 3
    current_attempt = 0
    console_log_result = None

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
            if(json.loads(console_log_result)['data'] is not None):
                break

        current_attempt += 1
        time.sleep(1)  # Adjust the wait time as needed
    price = None
    parsed_date = None
    # Parse the JSON string back to a Python object
    if console_log_result is not None:
        console_log_result_json = json.loads(console_log_result)
        if(console_log_result_json['data'] is not None):
            for result in console_log_result_json['data']:
                date_string = result[0][:-4]
                date_format = "%b %d %Y %H"

                parsed_date = datetime.datetime.strptime(date_string, date_format)
                price = result[1]
                sold_count = result[2]
                select_query = "SELECT * FROM SellTimes WHERE sell_time = (%s)"
                cursor.execute(select_query, (parsed_date,))
                existing_date = cursor.fetchone()
                date_id = None
                if not existing_date:
                    insert_query = "INSERT INTO SellTimes (sell_time) VALUES (%s)"
                    cursor.execute(insert_query, (parsed_date,))
                    connection.commit()
                    date_id = cursor.lastrowid
                else:
                    date_id = existing_date[0]
                insert_query = "INSERT INTO SellHistory (knife_id, sell_time_id, price, quantity) VALUES (%s, %s, %s, %s)"
                cursor.execute(insert_query, (knife_id, date_id, price, sold_count))
                connection.commit()
    
    return (price, parsed_date)
    

def get_knife_info_GPT(knife_name, driver):
    url = f"https://steamcommunity.com/market/listings/730/{knife_name}"
    
    # Extract knife data with retries
    data = extract_knife_data_with_retry(driver, url)

    # Handle cases where there is an error message
    if data['message'] and "error" in data['message'][0].text:
        return None

    # Handle cases where required data is not available
    if len(data['buy_orders']) < 2 or len(data['current_min_price_with_fee']) < 1 or len(data['current_min_price_without_fee']) < 1:
        return None
    
    if str(data['current_min_price_with_fee'][0]) == "Sold!":
        return None
    # Process min_price_with_fee consistently
    current_min_price_with_fee = float(data['current_min_price_with_fee'][0].text.replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip())

    buy_order_price = float(data['buy_orders'][1].text.replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip())

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
        match = re.search(r'\(\s*(\d+)\s*\)', desired_line)
        if match:
            knife_id = match.group(1)
        
    update_query = "UPDATE knives SET knife_id = %s WHERE knives.knife_name = %s" #STUPID_HACK
    cursor.execute(update_query, (knife_id, knife_name))
    connection.commit()

    last_min_price_with_fee, last_sold = get_and_save_historical_pricing(driver, cursor, connection, knife_id)
    new_knife = {
        'knife_name': knife_name,
        'knife_id': knife_id,
        'current_min_price_with_fee': current_min_price_with_fee,
        'current_min_price_without_fee': float(data['current_min_price_without_fee'][0].text.replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip()),
        'last_min_price_with_fee': last_min_price_with_fee,
        'last_min_price_without_fee': last_min_price_with_fee/1.15,
        'buy_order_price': buy_order_price,
        'last_updated': datetime.datetime.now(),
        'last_sold': last_sold
    }
    return new_knife

def save_knife_to_db(knife, cursor, connection):
    if(knife):
        update_query = "UPDATE knives SET knife_id = %s, current_min_price_with_fee = %s, current_min_price_without_fee = %s, last_min_price_with_fee = %s, last_min_price_without_fee = %s, buy_order_price = %s, last_updated = %s, last_sold = %s WHERE knives.knife_name = %s"
        cursor.execute(update_query, (knife['knife_id'], knife['current_min_price_with_fee'], knife['current_min_price_without_fee'], knife['last_min_price_with_fee'], knife['last_min_price_without_fee'], knife['buy_order_price'], knife['last_updated'], knife['last_sold'], knife['knife_name']))
        
        connection.commit()

def update_knife_info_and_save_to_db(knife_id, knife_name, cursor, connection):
    url = f'https://steamcommunity.com/market/priceoverview?appid=730&market_hash_name={knife_name}&currency=3'
    request = requests.get(url)
    status = request.status_code
    while(status != 200):
        time.sleep(60)
        request = requests.get(url)
        status = request.status_code
    item = request.json()
    min_price_with_fee = float(item.get("lowest_price").replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip())

    url = 'https://steamcommunity.com/market/itemordershistogram'
    request = requests.get(url,params={
        'country': "US",
        'currency': 3,
        'language': 'english',
        'item_nameid': knife_id,
        'two_factor': 0 
    })
    status = request.status_code
    while(status != 200):
        time.sleep(60)
        request = requests.get(url)
        status = request.status_code
    
    #print(request.json(), knife_id)
    buy_order_price = float(request.json().get("buy_order_graph")[0][0])

    updated_knife = {
        'min_price_with_fee': min_price_with_fee,
        'min_price_without_fee': 0,
        'buy_order_price': buy_order_price,
        'last_updated': datetime.datetime.now()
    }

    update_query = """
        UPDATE knives
        SET min_price_with_fee = %s,
            min_price_without_fee = %s,
            buy_order_price = %s,
            last_updated = %s
        WHERE knife_id = %s
    """

    # Execute the update query with the data as parameters
    cursor.execute(update_query, (
        updated_knife['min_price_with_fee'],
        updated_knife['min_price_without_fee'],
        updated_knife['buy_order_price'],
        datetime.datetime.now(),
        knife_id  # Use the knife_id to identify the row to be updated
    ))

    # Commit the changes to the database
    connection.commit()

def get_knife_list_from_db(cursor):
    select_query = "SELECT knife_name FROM knives"
    cursor.execute(select_query)
    knife_list = cursor.fetchall()
    return knife_list

if __name__ == "__main__":

    try:
        connection = mysql.connector.connect(host='localhost',
                                            database='knives',
                                            port='3306',
                                            user='root',
                                            password='')
        if connection.is_connected():
            db_Info = connection.get_server_info()
            print("Povezan ", db_Info)
            cursor = connection.cursor()
            cursor.execute("select database();")
            record = cursor.fetchone()
            print("Povezan sa bazom: ", record)

    except Error as e:
        print("Greška u konekciji ", e)
    
    #knife_names = get_knife_list_from_db(cursor)
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument("user-data-dir=C:/Filip_projekti/steam amrket boi/chrome-cache")
    cloud_options = {}
    cloud_options['goog:loggingPrefs'] = {'browser': 'ALL'}
    options.set_capability('cloud:options', cloud_options)
    driver = webdriver.Chrome(options=options)
    driver.request_interceptor = interceptor
    knife_names = get_knife_list_from_db(cursor)
    for knife_name in tqdm(knife_names):
        save_knife_to_db(get_knife_info_GPT(knife_name[0], driver), cursor, connection)
    exit()
    # knife_names = get_knife_list(driver)
    # add_new_knives_to_db(knife_names, cursor)
    # exit()
    
    add_new_knives_to_db(knife_list)
    exit()
    for knife_name in tqdm(knife_list):
        save_knife_to_db(get_knife_info(knife_name, driver), cursor, connection)
    exit()
    select_query = "SELECT knife_id, knife_name FROM knives"

    # Execute the query
    cursor.execute(select_query)

    # Fetch all the results
    knife_data = cursor.fetchall()

    for row in tqdm(knife_data):
        knife_id = row[0]
        knife_name = row[1]
        knife_name = knife_name.rstrip(knife_name[-1])
        update_knife_info_and_save_to_db(knife_id, knife_name, cursor, connection)

    cursor.close()
    connection.close()
    '''
    knife_prices = get_csgo_knife_prices()
    if knife_prices:
        sorted_knife_prices = dict(sorted(knife_prices.items(), key=lambda item: item[1], reverse=True))
        for name, price_difference in sorted_knife_prices.items():
            print(f"{name}: Price Difference - {price_difference}")
    else:
        print("Failed to fetch knife prices.")
    '''
