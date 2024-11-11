import os
import csv
import shutil
from decimal import Decimal, ROUND_CEILING
import time
from typing import Dict, List, Set, Tuple
from dotenv import load_dotenv

from steampy.client import SteamClient
from steampy.models import GameOptions, Currency

from Knife import Knife
from main import safe_get_knife_info, save_knives_to_db, connect_to_db, initialize_driver
from CustomLogger import CustomLogger
from selenium.webdriver.chrome.webdriver import WebDriver
from mysql.connector.connection import MySQLConnection

def exit_gracefully(driver: WebDriver, connection: MySQLConnection, user_data_dir: str, exit_code: int) -> None:
    driver.quit()
    connection.close()
    shutil.rmtree(user_data_dir)
    exit(exit_code)

def round_up_decimal(number: float | str) -> float:
    return float(Decimal(str(number)).quantize(Decimal('0.01'), rounding=ROUND_CEILING))

def get_price_from_user(actual_listing: Dict) -> float:
    name = actual_listing.get('item_name')
    if(name is None):
        logger.critical("Unexpectedly the item doesn't have a name, exiting...")
        exit_gracefully(driver, connection, user_data_dir, 1)
    price = actual_listing.get('price')
    if(price is None):
        logger.critical(f"Unexpectedly the {name} doesnt have your buy order, exiting...")
        exit_gracefully(driver, connection, user_data_dir, 1)
    assert price is not None
    price = float(price)
    while True:
        try:
            max_price = round_up_decimal(input(f"Set the maximum buy order at max for this item: {name}, your current buy order price: {price}\n"))
            if(max_price > price):
                return max_price
            else:
                print(f"Please provide a bigger price than {price}")
        except ValueError:
            print("Invalid input. Please enter a valid number.")

def get_action_from_user() -> str:
    print("Set the action if the buy order price goes above max. I or i to ignore, C or c to cancel the buy order.")
    
    user_input = input().strip().lower()

    if user_input == 'c':
        print("You chose to cancel the order.")
        return 'cancel'
    elif user_input == 'i':
        print("You chose to ignore the order.")
        return 'ignore'
    else:
        print("Invalid input, please press C or c, or I or i.")
        return get_action_from_user()

def set_all_prices(listings: List[Dict]) -> List[Dict]:
    knife_orders = list()
    for listing in listings:
        new_listing = listing
        new_listing['max_price'] = get_price_from_user(listing)
        new_listing['action'] = get_action_from_user()
        knife_orders.append(new_listing)
    return knife_orders

def filter_listings_to_knives(listings: Dict) -> List[Dict]:
    knife_orders: List[Dict] = list()
    orders = listings.get('buy_orders')
    if(orders is None):
        logger.critical("Unexpectedly there are no buy orders, exiting...")
        exit_gracefully(driver, connection, user_data_dir, 1)
    assert orders is not None
    for order_id in orders:
        actual_listing = orders.get(order_id)
        game_name = actual_listing.get('game_name')
        item_name = actual_listing.get('item_name').lower()

        if game_name == "Counter-Strike 2" and any(keyword in item_name for keyword in ["knife", "bayonet", "karambit", "shadow daggers"]):
            filtered_order = dict()
            filtered_order['price'] = actual_listing.get('price').replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip()
            filtered_order['order_id'] = actual_listing.get('order_id')
            filtered_order['item_name'] = actual_listing.get('item_name')
            knife_orders.append(filtered_order)
    return knife_orders

def add_knives_to_csv(knives_to_add: List[Dict], file_path: str) -> None:
    if(len(knives_to_add) < 1):
        return
    for knife in knives_to_add:
        if 'max_price' not in knife:
            knife['max_price'] = get_price_from_user(knife)
        if 'action' not in knife:
            knife['action'] = get_action_from_user()
    with open(file_path, 'a', newline='', encoding='utf-8') as file:
        fieldnames = knives_to_add[0].keys()
        
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        
        file.seek(0, 2)
        if file.tell() == 0:
            writer.writeheader()
        
        writer.writerows(knives_to_add)

    #print(f"{len(knives_to_add)} knives added successfully.")

def delete_knives_from_csv(knives_to_delete: List[str] | Set[str], file_path: str) -> None:
    if(len(knives_to_delete) < 1):
        return
    column_to_check = "item_name"

    with open(file_path, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            logger.critical("Unexpectedly csv file has no headers while deleting, exiting...")
            exit_gracefully(driver, connection, user_data_dir, 1)
        assert reader.fieldnames is not None
        rows = [row for row in reader if row[column_to_check] not in knives_to_delete]

    with open(file_path, 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def save_orders_to_csv(knife_orders: List[Dict], file_path: str) -> None:
    with open(file_path, 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=knife_orders[0].keys())
        writer.writeheader()
        writer.writerows(knife_orders)

def load_orders_from_csv(file_path: str) -> List[Dict]:
    data = []
    with open(file_path, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            data.append(dict(row))
    return data
#TODO: Handle the differences properly
#Difference found in order ★ StatTrak™ Bowie Knife | Autotronic (Minimal Wear)
#  Column 'price': '224.60' (actual) vs '224.6' (file
def compare_orders(actual: List[Dict], file: List[Dict], file_path: str) -> None:
    dict2 = {item['item_name']: item for item in file}
    missing = list()
    for item1 in actual:
        name = item1['item_name']
        item2 = dict2.get(name)

        if item2:
            for key in item1:
                value1 = int(item1[key]) if key == 'quantity' else item1[key]
                value2 = int(item2[key]) if key == 'quantity' else item2[key]
                if value1 != value2:
                    logger.info(f"Difference found in order {name}")
                    logger.info(f"  Column '{key}': '{item1[key]}' (actual) vs '{item2[key]}' (file)")
        else:
            logger.info(f"Order {name} in actual is missing from file. We will add it to the file")
            missing.append(item1)
    add_knives_to_csv(missing, file_path)

def check_for_missing_orders(actual: List[Dict], file: List[Dict], file_path: str) -> None:
    actual_orders = {item['item_name'] for item in actual}
    file_orders = {item['item_name'] for item in file}
    missing_in_actual = file_orders - actual_orders
    for element in missing_in_actual:
        logger.info(f"INFO: Order {element} is missing, you either already bought it or manually canceled it. We will delete it from csv.")
    delete_knives_from_csv(missing_in_actual, file_path)

def handle_user_choice() -> str:
    print("If you want to cancel the order press C or c, if you want to put a new max price press N or n, if you want to ignore this press I or i")
    
    user_input = input().strip().lower()

    if user_input == 'c':
        print("You chose to cancel the order.")
        return 'cancel'
    elif user_input == 'n':
        print("You chose to put a new max price.")
        return 'new_max_price'
    elif user_input == 'i':
        print("You chose to ignore the order.")
        return 'ignore'
    else:
        print("Invalid input, please press C, N, or I.")
        return handle_user_choice()

def cancel_order(order: Dict) -> bool:
    try:
        order_id = order.get('order_id')
        if(order_id is None):
            logger.error(f"Failed to cancel the buy order on the knife {order.get('item_name')} because order has no id")
            return False
        response = steam_client.market.cancel_buy_order(int(order_id))
    except Exception as e:
        logger.error(f"Failed to cancel the buy order on the knife {order.get('item_name')} skipping...")
        logger.error(str(e))
        return False
    if response.get('success') != 1:
        return False
    else:
        logger.info(f"Successfully canceled the buy order on the knife {order.get('item_name')} at price {order.get('price')}")
        return True

def put_order(listing: Knife, price: int) -> Tuple[bool, int]:
    try:
        response = steam_client.market.create_buy_order(listing.knife_name, price, 1, GameOptions.CS, Currency.EURO)
        
    except Exception as e:
        logger.error(f"Failed to put the buy order on the knife {listing.knife_name}, skipping...")
        logger.error(str(e))
        return False, -1
    if response.get('success') != 1:
        return False, -1
    else:
        logger.info(f"Successfully put the buy order on the knife {listing.knife_name} at price {price / 100.0}")
        return True, int(response.get('buy_orderid'))

def cancel_orders(orders):
    successfully_canceled = list()
    for order in orders:
        if(cancel_order(order)):
            successfully_canceled.append(order)
    return successfully_canceled

def amend_orders(orders):
    unsuccessfully_amended = list()
    successfully_amended = list()
    for order in orders:
        if(not cancel_order(order.get('order_id'))):
            unsuccessfully_amended.append(order)
            continue
        if(not put_order(order, 1)):
            unsuccessfully_amended.append(order)
        successfully_amended.append(order)
    delete_knives_from_csv(successfully_amended, file_path)
    add_knives_to_csv(successfully_amended, file_path)
    return unsuccessfully_amended

def get_wallet_balance(steam_client: SteamClient) -> Tuple[bool, float]:
        try:
            wallet_balance = steam_client.get_wallet_balance()
            assert isinstance(wallet_balance, Decimal)
            wallet_balance = float(wallet_balance)
            return True, wallet_balance
        except Exception:
            logger.error("Failed to get wallet balance sleeping for 30 seconds then trying again")
            time.sleep(30)
            return False, -1

if __name__ == "__main__":
    load_dotenv()
    logger = CustomLogger(log_file="order_maxer.log", log_level="[INFO]")

    login_cookies = {'steamLoginSecure': os.environ['STEAM_COOKIE_STEAM_LOGIN_SECURE']}  # provide dict with cookies
    steam_client = SteamClient(os.environ['STEAM_API'], username=os.environ['STEAM_USERNAME'], login_cookies=login_cookies)
    #steam_client = SteamClient(os.environ['STEAM_API'], username=os.environ['STEAM_USERNAME'])
    assert steam_client.was_login_executed
    success, wallet_balance = get_wallet_balance(steam_client)
    while(not success):
        success, wallet_balance = get_wallet_balance(steam_client)

    listings = steam_client.market.get_my_market_listings()
    knife_orders_actual = filter_listings_to_knives(listings)

    knife_orders_file = None
    file_path = 'data.csv'
    if os.path.exists(file_path):
        knife_orders_file = load_orders_from_csv(file_path)
        #logging.info("Loaded last known orders and your max prices from file")
        logger.info("Loaded last known orders and your max prices from file")
    else:
        #logging.info("CSV file doesn't exist")
        logger.info("CSV file doesn't exist, creating a new one")
    if knife_orders_file is not None:
        check_for_missing_orders(knife_orders_actual, knife_orders_file, file_path)
        knife_orders_file = load_orders_from_csv(file_path) #XDDDDDDDDDDDDDDDDDDD
        compare_orders(knife_orders_actual, knife_orders_file, file_path)
        knife_orders_file = load_orders_from_csv(file_path) #XDDDDDDDDDDDDDDDDDDD
        '''
        amended_orders = list()
        canceled_orders = list()
        for order in knife_orders_file:
            if float(order.get('price')) > float(order.get('max_price')):
                print(f"Order for {order.get('item_name')} is {order.get('price')} which is bigger than your max order {order.get('max_price')}.")
                action = handle_user_choice()
                if action == 'cancel':
                    canceled_orders.append(order)
                elif action == 'new_max_price':
                    amended_order = order
                    amended_order['max_price'] = get_price_from_user(order)
                    amended_orders.append(order)
                elif action == 'ignore':
                    print("Ignoring")
        delete_knives_from_csv(cancel_orders(canceled_orders), file_path)
        amend_orders(amended_orders)
        '''
    else:
        knife_orders_file = set_all_prices(knife_orders_actual)

    save_orders_to_csv(knife_orders_file, file_path)
    
    for order in knife_orders_file:
        logger.info(f"Item: {order.get('item_name')}, your current buy order price: {order.get('price')}, maximum price: {order.get('max_price')}")
    connection, cursor = connect_to_db('localhost', 'knives', 3306, 'root', '')
    driver, user_data_dir = initialize_driver(True)
    while True:
        try:
            success, wallet_balance = get_wallet_balance(steam_client)
            if(not success):
                continue
            #TODO: The code below assumes that the wallet balance doesnt change and that no buy orders go through, 
            # and it doesnt check the consistency between csv file and the actual buy orders 
            # so if you change the buy orders manually through steam it gets fuckd

            for knife_order in knife_orders_file:
                knife_name = knife_order.get('item_name')
                if(knife_name is None):
                    continue
                knife_listing = safe_get_knife_info((knife_name, ), driver, cursor, connection, 6, logger)
                if knife_listing is None:
                    continue
                save_knives_to_db([knife_listing], cursor, connection)

                current_buy_order_price = knife_order.get('price')
                if(current_buy_order_price is None):
                    logger.error(f"{knife_name} order has no price, skipping...")
                    continue
                current_buy_order_price = float(current_buy_order_price)

                if(knife_listing.buy_order_price is None):
                    logger.error(f"Buy order price is None {knife_name}")
                    continue
                price_difference = knife_listing.buy_order_price - current_buy_order_price
                knife_max_price = knife_order.get('max_price')
                if(knife_max_price is None):
                    logger.error(f"{knife_name} order has no max price, skipping...")
                    continue
                knife_max_price = float(knife_max_price)
                #TODO: Handle case where me and other people have the same max buy order. Example: 283-4 people, 283.1-3 people 283.1 is max
                if (price_difference > 0) and (knife_listing.buy_order_price < knife_max_price):
                    if(cancel_order(knife_order)):
                        if knife_listing.buy_order_price < wallet_balance:
                            success, id = put_order(knife_listing, int(knife_listing.buy_order_price * 100 + 1))
                            if(success):
                                delete_knives_from_csv([knife_name], file_path)
                                new_order = knife_order
                                new_order['price'] = round_up_decimal(knife_listing.buy_order_price + 0.01)
                                new_order['order_id'] = id
                                add_knives_to_csv([new_order], file_path)
                        else:
                            success, id = put_order(knife_listing, int(wallet_balance*100))
                            if(success):
                                delete_knives_from_csv([knife_name], file_path)
                                new_order = knife_order
                                new_order['price'] = round_up_decimal(wallet_balance)
                                new_order['order_id'] = id
                                add_knives_to_csv([new_order], file_path)
                elif(current_buy_order_price >= float(knife_max_price)):
                    if(knife_order.get('action') == 'cancel' and cancel_order(knife_order)):
                        logger.info(f"Canceled order on knife {knife_name} because price is bigger than maximum set.")
                    
                            
            knife_orders_file = load_orders_from_csv(file_path)
            sleep_seconds = 300
            logger.info(f"Sleeping for {sleep_seconds} seconds")
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            logger.info("\n Canceling loop and shutting down...")
            break
    exit_gracefully(driver, connection, user_data_dir, 0)
