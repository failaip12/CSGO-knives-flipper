import os
import mysql.connector

from decimal import Decimal
from dotenv import load_dotenv
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from mysql.connector import Error


from steampy.client import SteamClient
from steampy.models import GameOptions, Currency

from main import get_knife_info, interceptor, save_knife_to_db

if __name__ == "__main__":
    load_dotenv()
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

    login_cookies = {'steamLoginSecure': os.environ['STEAM_COOKIE_STEAM_LOGIN_SECURE']} # provide dict with cookies
    steam_client = SteamClient(os.environ['STEAM_API'],username=os.environ['STEAM_USERNAME'],login_cookies=login_cookies)
    assert steam_client.was_login_executed
    wallet_balance = steam_client.get_wallet_balance()
    assert isinstance(wallet_balance, Decimal)
    wallet_balance = float(wallet_balance)
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument("user-data-dir=C:/Filip_projekti/CSGO-knives-flipper/backend/chrome-cache")
    driver = webdriver.Chrome(options=options)
    driver.request_interceptor = interceptor

    listings = steam_client.market.get_my_market_listings()
    knife_orders = list()
    lost_knife_names = list()
    for listing in listings['buy_orders']:
        l = listings['buy_orders'][listing]
        game_name = l['game_name']
        item_name = l['item_name'].lower()

        if game_name == "Counter-Strike 2" and any(keyword in item_name for keyword in ["knife", "bayonet", "karambit", "shadow daggers"]):
            knife_orders.append(l)
    
    for knife_order in knife_orders:
        knife_name = knife_order['item_name']
        knife_listing = get_knife_info(knife_name, driver)
        save_knife_to_db(knife_listing, cursor, connection)
        current_buy_order_price = float(knife_order['price'].replace(",", ".").replace("-", "0").replace("€", "").replace(" ", "").strip())
        price_difference = knife_listing["buy_order_price"] - current_buy_order_price
        if (price_difference > 0) and (current_buy_order_price != wallet_balance):
            buy_order_id = int(knife_order["order_id"])
            response = steam_client.market.cancel_buy_order(buy_order_id)
            if(response['success'] != 1):
                print("ERROR: Failed to cancel the buy order on the knife " + knife_name + ", skipping...")
                continue
            else:
                print("INFO: Succsessfully canceled the buy order on the knife ", knife_name)
            if(knife_listing["buy_order_price"] > wallet_balance):
                print("INFO: Current max buy order price is higher than the amount of money you have in wallet, setting the price to your wallet money as fallback", knife_name)
                response = steam_client.market.create_buy_order(knife_name, wallet_balance * 100, 1, GameOptions.CS, Currency.EURO)
            else:
                response = steam_client.market.create_buy_order(knife_name, knife_listing["buy_order_price"] * 100 + 5, 1, GameOptions.CS, Currency.EURO)
            if(response['success'] != 1):
                print("ERROR: Failed to create the buy order on the knife ", knife_name)
                lost_knife_names.append(knife_name)
            else:
                print("INFO: Succsessfully created the buy order on the knife ", knife_name, ", for the price of ", knife_listing["buy_order_price"] + 0.05)
    print(lost_knife_names)
    connection.close()
