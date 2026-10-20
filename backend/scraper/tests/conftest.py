"""
Run with `pytest` from backend/scraper (pip install -r requirements-dev.txt).

The database tests use their own database, knives_test, on the Postgres from
docker-compose.yml (change it with the KNIVES_TEST_DB_* environment variables).
They drop its tables, so they refuse to run against the knives database, and
they're skipped when Postgres isn't reachable.
"""

import csv
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import pytest

SCRAPER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRAPER_DIR))

import psycopg2  # noqa: E402

import steam_market as sm  # noqa: E402
from CustomLogger import CustomLogger  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GROUP_PAGE_KEY = "G18F403204D3003"
SCHEMA = (SCRAPER_DIR / "DB" / "Postgres" / "baza_postgres.sql").read_text()

ORDER_BOOK = {
    "data": {
        "success": True,
        "data": {"amtMaxBuyOrder": 12221, "amtMinSellOrder": 13130, "eCurrency": 3},
    }
}

# (status, body) or (status, body, headers). Status "drop" closes the connection,
# "slow429" answers 429 after a short delay so several requests are in flight at once.
Response = tuple[Any, ...]


class FakeSteam:
    """Local HTTP server standing in for steamcommunity.com."""

    def __init__(self):
        self.calls: list[tuple[float, str, dict]] = []
        self.queue: list[Response] = []
        # Answers requests once the queue is empty: route(path) -> response.
        self.route: Callable[[str], Response] = lambda path: (
            200,
            json.dumps(ORDER_BOOK),
        )
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.calls.append((time.monotonic(), self.path, dict(self.headers)))
                response = fake.queue.pop(0) if fake.queue else fake.route(self.path)
                status, body = response[0], response[1]
                headers = response[2] if len(response) > 2 else {}
                if status == "drop":
                    self.connection.close()
                    return
                if status == "slow429":
                    time.sleep(0.3)
                    status = 429
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body.encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/market"

    def reply(self, *responses: Response) -> None:
        self.queue.extend(responses)

    def reply_json(self, *payloads: Any) -> None:
        self.queue.extend((200, json.dumps(payload)) for payload in payloads)


@pytest.fixture
def logger(tmp_path) -> CustomLogger:
    return CustomLogger(str(tmp_path / "test.log"))


@pytest.fixture
def group_page() -> str:
    return (FIXTURES / "group_page.html").read_text(encoding="utf-8")


@pytest.fixture
def fake_steam(monkeypatch):
    steam = FakeSteam()
    monkeypatch.setattr(sm, "MARKET_URL", steam.url)
    monkeypatch.setattr(sm, "COOLDOWNS", [1, 2])
    yield steam
    steam.server.shutdown()


@pytest.fixture
def fast_limits(monkeypatch):
    """Makes every client created from now on fast and quick to give up on blocked endpoints."""

    def configure(interval: float = 0.01, max_blocked: float = 4) -> None:
        original = sm.default_rate_limits

        def limits():
            result = original()
            for limit in result.values():
                limit.interval = limit.min_interval = interval
                limit.max_blocked = max_blocked
            return result

        monkeypatch.setattr(sm, "default_rate_limits", limits)

    return configure


# --- Database --------------------------------------------------------------------------

DB_SETTINGS = {
    "host": os.environ.get("KNIVES_TEST_DB_HOST", "localhost"),
    "port": int(os.environ.get("KNIVES_TEST_DB_PORT", "5432")),
    "user": os.environ.get("KNIVES_TEST_DB_USER", "scraper"),
    "password": os.environ.get("KNIVES_TEST_DB_PASSWORD", "scraper"),
}
TEST_DB_NAME = os.environ.get("KNIVES_TEST_DB_NAME", "knives_test")


@pytest.fixture(scope="session")
def test_database() -> str:
    """Creates the test database if needed. Never the real one, the tests drop its tables."""
    if TEST_DB_NAME == "knives":
        pytest.exit("Refusing to run the tests against the 'knives' database.")
    try:
        admin = psycopg2.connect(dbname="postgres", connect_timeout=3, **DB_SETTINGS)
    except psycopg2.OperationalError as e:
        pytest.skip(f"Postgres for the tests isn't reachable: {e}")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    admin.close()
    return TEST_DB_NAME


class Database:
    def __init__(self, connection):
        self.connection = connection

    def query(self, sql: str, *args: Any) -> Optional[list]:
        with self.connection.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description else None

    def reset(self) -> None:
        self.query("DROP TABLE IF EXISTS SellHistory, SellTimes, Knives")
        with self.connection.cursor() as cur:
            cur.execute(SCHEMA)

    def knife(self, name: str) -> Optional[dict]:
        rows = self.query(
            """SELECT knife_id, current_min_price_with_fee, buy_order_price, last_min_price_with_fee,
                      last_sold, knife_image, last_updated, amount_sold, amount_sold_last_year
               FROM knives WHERE knife_name = %s""",
            name,
        )
        if not rows:
            return None
        keys = [
            "knife_id",
            "ask",
            "bid",
            "last_price",
            "last_sold",
            "image",
            "last_updated",
            "amount_sold",
            "amount_sold_last_year",
        ]
        return dict(zip(keys, rows[0], strict=True))

    def names(self) -> list[str]:
        return sorted(row[0] for row in self.query("SELECT knife_name FROM knives"))

    def history(self, knife_id: int) -> list:
        rows = self.query(
            """SELECT st.sell_time, sh.price, sh.quantity FROM SellHistory sh
               JOIN SellTimes st USING (sell_time_id) WHERE knife_id = %s ORDER BY 1""",
            knife_id,
        )
        return [(sold_at, float(price), amount) for sold_at, price, amount in rows]


@pytest.fixture
def db(test_database) -> Database:
    connection = psycopg2.connect(dbname=test_database, **DB_SETTINGS)
    connection.autocommit = True
    database = Database(connection)
    database.reset()
    yield database
    connection.close()


@pytest.fixture
def run_scraper(db, logger, tmp_path, monkeypatch):
    """Runs update_all_knife_data against the test database, returns the failed knife names."""
    import scraper

    original_connect = scraper.connect_to_db
    monkeypatch.setattr(
        scraper,
        "connect_to_db",
        lambda host, database, port, user, password, logger: original_connect(
            DB_SETTINGS["host"],
            db.connection.info.dbname,
            DB_SETTINGS["port"],
            DB_SETTINGS["user"],
            DB_SETTINGS["password"],
            logger,
        ),
    )
    monkeypatch.setattr(scraper, "RETRY_WAIT", 0)
    failed_file = tmp_path / "failed_knives.csv"

    def run(client=None, discover: bool = True) -> list[str]:
        if client is not None:
            monkeypatch.setattr(scraper, "SteamMarketClient", lambda logger: client)
        if failed_file.exists():
            failed_file.unlink()
        scraper.update_all_knife_data(str(failed_file), logger, discover)
        if not failed_file.exists():
            return []
        with open(failed_file, encoding="utf-8", newline="") as file:
            return sorted(row[0] for row in csv.reader(file) if row)

    return run
