# py -m fastapi run .\main.py
import os
import sys
from typing import Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pysteamsignin.steamsignin import SteamSignIn
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from steampy.client import SteamClient

from model import Knives

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scraper.DB.Postgres.config_postgres import DATABASE_URL  # noqa: E402

# from scraper.DB.MySQL.config_mysql import DATABASE_URL

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
engine = create_engine(DATABASE_URL)
steam_login = SteamSignIn()
STEAM_API_KEY = os.environ.get("STEAM_API_KEY")
if not STEAM_API_KEY:
    raise ValueError("STEAM_API_KEY environment variable is not set.")
STEAM_USERNAME = os.environ.get("STEAM_USERNAME")
login_cookies = {"steamLoginSecure": os.environ.get("STEAM_COOKIE_STEAM_LOGIN_SECURE")}

steam_client = SteamClient(
    STEAM_API_KEY,
    username=STEAM_USERNAME,
    login_cookies=login_cookies,
)


def filter_listings_to_knives(listings: Dict) -> Optional[List[str]]:
    knife_names = []
    orders = listings.get("buy_orders")
    if orders is None:
        return None
    assert orders is not None
    for order_id in orders:
        actual_listing = orders.get(order_id)
        game_name = actual_listing.get("game_name")
        item_name = actual_listing.get("item_name").lower()

        if game_name == "Counter-Strike 2" and any(
            keyword in item_name
            for keyword in ["knife", "bayonet", "karambit", "shadow daggers"]
        ):
            knife_names.append(actual_listing.get("item_name"))
    return knife_names


@app.get("/")
def read_root():
    return {"Message": "HELLO"}


@app.get("/knives")
def read_knives() -> List[Knives]:
    session = Session(engine)
    stmt = select(Knives)
    resp: List[Knives] = []
    for knife in session.scalars(stmt):
        resp.append(knife)
    return resp


@app.get("/buy_orders")
async def get_buy_orders():
    listings = steam_client.market.get_my_market_listings()
    knife_names_actual = filter_listings_to_knives(listings)
    return knife_names_actual
