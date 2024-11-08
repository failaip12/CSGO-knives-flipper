import os
import csv
import shutil

from decimal import Decimal, ROUND_CEILING
import time
from dotenv import load_dotenv

from steampy.client import SteamClient
from steampy.models import GameOptions, Currency

from main import safe_get_knife_info, save_knife_to_db, connect_to_db, initialize_driver

def round_up_decimal(number):
    return float(Decimal(str(number)).quantize(Decimal('0.01'), rounding=ROUND_CEILING))

def get_price_from_user(actual_listing):
    while True:
        try:
            max_price = round_up_decimal(input(f"Set the maximum buy order at max for this item: {actual_listing.get('item_name')}, your current buy order price: {actual_listing.get('price')}\n"))

            if(max_price > float(actual_listing.get('price'))):
                return max_price
            else:
                print(f"Please provide a bigger price than {actual_listing.get('price')}")
        except ValueError:
            print("Invalid input. Please enter a valid number.")

def get_action_from_user():
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

def set_all_prices(listings):
    knife_orders = list()
    for listing in listings:
        new_listing = listing
        new_listing['max_price'] = get_price_from_user(listing)
        new_listing['action'] = get_action_from_user()
        knife_orders.append(new_listing)
    return knife_orders

def filter_listings_to_knives(listings):
    knife_orders = list()
    for listing in listings['buy_orders']:
        actual_listing = listings['buy_orders'][listing]
        game_name = actual_listing['game_name']
        item_name = actual_listing['item_name'].lower()

        if game_name == "Counter-Strike 2" and any(keyword in item_name for keyword in ["knife", "bayonet", "karambit", "shadow daggers"]):
            filtered_order = dict()
            filtered_order['price'] = actual_listing['price'].replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip()
            filtered_order['order_id'] = actual_listing['order_id']
            filtered_order['item_name'] = actual_listing['item_name']
            knife_orders.append(filtered_order)
    return knife_orders

def add_knives_to_csv(knives_to_add, file_path):
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

def delete_knives_from_csv(knives_to_delete, file_path):
    if(len(knives_to_delete) < 1):
        return
    column_to_check = "item_name"

    with open(file_path, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        rows = [row for row in reader if row[column_to_check] not in knives_to_delete]

    with open(file_path, 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def save_orders_to_csv(knife_orders, file_path):
    with open(file_path, 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=knife_orders[0].keys())
        writer.writeheader()
        writer.writerows(knife_orders)

def load_orders_from_csv(file_path):
    data = []
    with open(file_path, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            data.append(dict(row))
    return data
#TODO: Handle the differences properly
#Difference found in order ★ StatTrak™ Bowie Knife | Autotronic (Minimal Wear)
#  Column 'price': '224.60' (actual) vs '224.6' (file
def compare_orders(actual, file, file_path):
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
                    print(f"Difference found in order {name}")
                    print(f"  Column '{key}': '{item1[key]}' (actual) vs '{item2[key]}' (file)")
        else:
            print(f"Order {name} in actual is missing from file. We will add it to the file")
            missing.append(item1)
    add_knives_to_csv(missing, file_path)

def check_for_missing_orders(actual, file, file_path):
    actual_orders = {item['item_name'] for item in actual}
    file_orders = {item['item_name'] for item in file}
    missing_in_actual = file_orders - actual_orders
    for element in missing_in_actual:
        #logging.info(f"Order {element} is missing, you either already bought it or manually canceled it. We will delete it from csv.")
        print(f"INFO: Order {element} is missing, you either already bought it or manually canceled it. We will delete it from csv.")
    delete_knives_from_csv(missing_in_actual, file_path)

def handle_user_choice():
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

def cancel_order(order):
    try:
        response = steam_client.market.cancel_buy_order(int(order.get('order_id')))
    except Exception as e:
        print(e)
        print(f"ERROR: Failed to cancel the buy order on the knife {order.get('item_name')} skipping...")
        return False
    if response.get('success') != 1:
        return False
    else:
        print(f"INFO: Succsessfully canceled the buy order on the knife {order.get('item_name')} at price {order.get('price')}")
        return True

def put_order(listing, price):
    try:
        response = steam_client.market.create_buy_order(listing.get('knife_name'), price, 1, GameOptions.CS, Currency.EURO)
        
    except Exception as e:
        print(e)
        print("ERROR: Failed to put the buy order on the knife " + listing.get('knife_name') + ", skipping...")
        return False, -1
    if response.get('success') != 1:
        return False, -1
    else:
        print(f"INFO: Succsessfully put the buy order on the knife {listing.get('knife_name')} at price {price / 100.0}")
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

if __name__ == "__main__":
    load_dotenv()
    #logging.basicConfig(
    #    level=logging.INFO,  # Set the logging level
    #    format="%(asctime)s [%(levelname)s] %(message)s",
    #    handlers=[
    #        logging.FileHandler("order_maxer.log", encoding='utf-8'),  # Set UTF-8 encoding for the file
    #        logging.StreamHandler(sys.stdout)  # Log to the console
    #    ]
    #)
    connection, cursor = connect_to_db('localhost', 'knives', '3306', 'root', '')
    driver, user_data_dir = initialize_driver(True)
    login_cookies = {'steamLoginSecure': os.environ['STEAM_COOKIE_STEAM_LOGIN_SECURE']}  # provide dict with cookies
    steam_client = SteamClient(os.environ['STEAM_API'], username=os.environ['STEAM_USERNAME'], login_cookies=login_cookies)
    assert steam_client.was_login_executed
    wallet_balance = steam_client.get_wallet_balance()
    assert isinstance(wallet_balance, Decimal)
    wallet_balance = float(wallet_balance)

    listings = steam_client.market.get_my_market_listings()
    knife_orders_actual = filter_listings_to_knives(listings)

    knife_orders_file = None
    file_path = 'data.csv'
    if os.path.exists(file_path):
        knife_orders_file = load_orders_from_csv(file_path)
        #logging.info("Loaded last known orders and your max prices from file")
        print("INFO: Loaded last known orders and your max prices from file")
    else:
        #logging.info("CSV file doesn't exist")
        print("INFO: CSV file doesn't exist")
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
        print(f"Item: {order.get('item_name')}, your current buy order price: {order.get('price')}, maximum price: {order.get('max_price')}")
    
    while True:
        try:
            wallet_balance = steam_client.get_wallet_balance()
            assert isinstance(wallet_balance, Decimal)
            wallet_balance = float(wallet_balance)

            #TODO: The code below assumes that the wallet balance doesnt change and that no buy orders go through, 
            # and it doesnt check the consistency between csv file and the actual buy orders

            for knife_order in knife_orders_file:
                knife_name = knife_order['item_name']
                knife_listing = safe_get_knife_info((knife_name, ), driver, cursor, connection, 6)
                if knife_listing is None:
                    continue
                save_knife_to_db(knife_listing, cursor, connection)
                current_buy_order_price = float(knife_order['price'])
                price_difference = knife_listing["buy_order_price"] - current_buy_order_price
                if (price_difference > 0) and (current_buy_order_price < float(knife_order["max_price"])):
                    if(cancel_order(knife_order)):
                        if knife_listing["buy_order_price"] < wallet_balance:
                            succsess, id = put_order(knife_listing, knife_listing["buy_order_price"] * 100 + 1)
                            if(succsess):
                                delete_knives_from_csv([knife_name], file_path)
                                new_order = knife_order
                                new_order['price'] = round_up_decimal(knife_listing["buy_order_price"] + 0.01)
                                new_order['order_id'] = id
                                add_knives_to_csv([new_order], file_path)
                        else:
                            succsess, id = put_order(knife_listing, wallet_balance*100)
                            if(succsess):
                                delete_knives_from_csv([knife_name], file_path)
                                new_order = knife_order
                                new_order['price'] = round_up_decimal(wallet_balance)
                                new_order['order_id'] = id
                                add_knives_to_csv([new_order], file_path)
                elif(current_buy_order_price >= float(knife_order["max_price"])):
                    if(knife_order.get('action') == 'cancel' and cancel_order(knife_order)):
                        print(f"Canceled order on knife {knife_name} because price is bigger than maximum set.")
                    
                            
            knife_orders_file = load_orders_from_csv(file_path)
            sleep_seconds = 300
            print(f"INFO: Sleeping for {sleep_seconds} seconds")
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            print("\nINFO: Canceling loop and shutting down...")
            break
    shutil.rmtree(user_data_dir)
