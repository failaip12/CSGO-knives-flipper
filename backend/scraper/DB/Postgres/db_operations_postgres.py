from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.errors
from psycopg2.extensions import connection, cursor
from psycopg2.extras import execute_values

from CustomLogger import CustomLogger
from Knife import Knife


def add_knife_to_db(name: str, cur: cursor, conn: connection) -> int:
    """Inserts the knife if it's missing and returns its knife_id."""
    # Existing knife_ids were overwritten with Steam ids in the past, so the serial
    # sequence can hand out an id that is already taken. Retrying moves past it.
    for _ in range(1000):
        try:
            cur.execute(
                "INSERT INTO knives (knife_name) VALUES (%s) ON CONFLICT (knife_name) DO NOTHING RETURNING knife_id",
                (name,),
            )
            row = cur.fetchone()
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            continue
        if row:
            return row[0]
        cur.execute("SELECT knife_id FROM knives WHERE knife_name = %s", (name,))
        existing = cur.fetchone()
        if existing:
            return existing[0]
    raise RuntimeError(f"Could not find a free knife_id for {name}")


def save_price_history(
    history: list[tuple[datetime, float, int]],
    cur: cursor,
    conn: connection,
    knife_id: int,
) -> None:
    """Upserts (sell time, price, amount sold) entries into SellHistory."""
    rows_by_time = {sold_at: (price, amount) for sold_at, price, amount in history}
    if not rows_by_time:
        return
    times = list(rows_by_time)
    execute_values(
        cur,
        "INSERT INTO SellTimes (sell_time) VALUES %s ON CONFLICT (sell_time) DO NOTHING",
        [(sold_at,) for sold_at in times],
        page_size=1000,
    )
    cur.execute(
        "SELECT sell_time, sell_time_id FROM SellTimes WHERE sell_time = ANY(%s)",
        (times,),
    )
    time_ids = dict(cur.fetchall())
    execute_values(
        cur,
        """INSERT INTO SellHistory (knife_id, sell_time_id, price, quantity) VALUES %s
        ON CONFLICT (knife_id, sell_time_id) DO UPDATE
        SET price = EXCLUDED.price, quantity = EXCLUDED.quantity""",
        [
            (knife_id, time_ids[sold_at], price, amount)
            for sold_at, (price, amount) in rows_by_time.items()
        ],
        page_size=1000,
    )
    conn.commit()


def save_knives_to_db(knives: list[Knife], cur: cursor, conn: connection) -> None:
    if not knives:
        return

    base_query = """
        UPDATE knives SET
            current_min_price_with_fee = %s,
            current_min_price_without_fee = %s,
            last_min_price_with_fee = %s,
            last_min_price_without_fee = %s,
            buy_order_price = %s,
            last_updated = %s,
            last_sold = %s,
            knife_image = %s
        WHERE knife_name = %s
    """

    values = [
        (
            knife.current_min_price_with_fee,
            knife.current_min_price_without_fee,
            knife.last_min_price_with_fee,
            knife.last_min_price_without_fee,
            knife.buy_order_price,
            datetime.now(),
            knife.last_sold,
            knife.knife_image,
            knife.knife_name,
        )
        for knife in knives
    ]

    cur.executemany(base_query, values)
    conn.commit()


def get_knife_names_from_db(
    cur: cursor, limit: Optional[int] = None, date: Optional[str] = None
) -> list[str]:
    if date is None:
        select_query = (
            "SELECT knife_name FROM knives ORDER BY last_updated ASC NULLS FIRST"
        )
    else:
        select_query = "SELECT knife_name FROM knives WHERE last_updated < %s ORDER BY last_updated ASC NULLS FIRST"
    if limit is not None:
        select_query += " LIMIT %s"
    cur.execute(select_query, (date, limit) if date else (limit,))
    knife_list = [name[0] for name in cur.fetchall()]
    return knife_list


def get_knife_list_from_db(cur: cursor, date: Optional[str] = None) -> list[Knife]:
    if date is None:
        select_query = "SELECT * FROM knives ORDER BY last_updated ASC NULLS FIRST"
    else:
        select_query = "SELECT * FROM knives WHERE last_updated < %s ORDER BY last_updated ASC NULLS FIRST"

    cur.execute(select_query, (date,) if date else None)
    knife_list = []
    for row in cur.fetchall():
        knife = Knife(
            knife_id=row[0],
            knife_name=str(row[1]),
            current_min_price_with_fee=row[2],
            current_min_price_without_fee=row[3],
            last_min_price_with_fee=row[4],
            last_min_price_without_fee=row[5],
            buy_order_price=row[6],
            knife_image=row[13] if len(row) > 13 else None,
            last_updated=row[8] if len(row) > 8 else None,
            last_sold=row[9] if len(row) > 9 else None,
        )
        knife_list.append(knife)
    return knife_list


def get_knife_from_db(cur: cursor, name: str) -> Optional[Knife]:
    cur.execute("SELECT * FROM knives WHERE knife_name = %s", (name,))
    row = cur.fetchone()
    if row:
        knife = Knife(
            knife_id=row[0],
            knife_name=str(row[1]),
            current_min_price_with_fee=row[2],
            current_min_price_without_fee=row[3],
            last_min_price_with_fee=row[4],
            last_min_price_without_fee=row[5],
            buy_order_price=row[6],
            knife_image=row[13] if len(row) > 13 else None,
            last_updated=row[8] if len(row) > 8 else None,
            last_sold=row[9] if len(row) > 9 else None,
        )
        return knife
    else:
        return None


def connect_to_db(
    host: str, database: str, port: int, user: str, password: str, logger: CustomLogger
) -> tuple[connection, cursor]:
    try:
        conn = psycopg2.connect(
            host=host, dbname=database, port=port, user=user, password=password
        )
        cur = conn.cursor()
    except psycopg2.Error as e:
        logger.fatal("SQL connection error " + str(e))
        exit(1)
    return conn, cur


def connect_to_db_threaded(
    host: str, database: str, port: int, user: str, password: str, logger: CustomLogger
) -> tuple[connection, cursor]:
    return connect_to_db(
        host=host,
        database=database,
        port=port,
        user=user,
        password=password,
        logger=logger,
    )


def update_all(cur: cursor) -> None:
    update_amount_sold(cur)
    update_selling_frequency(cur)
    update_amount_sold_last_year(cur)


def update_amount_sold(cur: cursor) -> None:
    update_query = """
    UPDATE Knives k
    SET amount_sold = sh.total_sold
    FROM (
        SELECT knife_id, COUNT(*) AS total_sold
        FROM SellHistory
        GROUP BY knife_id
    ) sh
    WHERE k.knife_id = sh.knife_id;
    """
    cur.execute(update_query)
    cur.connection.commit()


def update_selling_frequency(cur: cursor) -> None:
    update_query = """
    UPDATE Knives k
    SET selling_frequency = sf.frequency
    FROM (
        SELECT 
            sh.knife_id, 
            COUNT(*) / (EXTRACT(EPOCH FROM (NOW() - MIN(st.sell_time))) / 2592000) AS frequency
        FROM SellHistory sh
        JOIN SellTimes st ON sh.sell_time_id = st.sell_time_id
        GROUP BY sh.knife_id
    ) sf
    WHERE k.knife_id = sf.knife_id;
    """
    cur.execute(update_query)
    cur.connection.commit()


def update_amount_sold_last_year(cur: cursor) -> None:
    update_query = """
    WITH LastYearSales AS (
        SELECT 
            sh.knife_id,
            SUM(sh.quantity) AS total_quantity_sold
        FROM 
            SellHistory sh
        JOIN 
            SellTimes st ON sh.sell_time_id = st.sell_time_id
        WHERE 
            st.sell_time >= NOW() - INTERVAL '1 year'
        GROUP BY 
            sh.knife_id
    )
    UPDATE Knives k
    SET amount_sold_last_year = COALESCE(lys.total_quantity_sold, 0)
    FROM LastYearSales lys
    WHERE k.knife_id = lys.knife_id;
    """
    cur.execute(update_query)
    cur.connection.commit()
