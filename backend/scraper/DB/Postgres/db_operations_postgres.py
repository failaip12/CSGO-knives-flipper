from datetime import datetime
from typing import List, Optional, Set, Tuple

import psycopg2
from psycopg2.extensions import connection, cursor
from tqdm import tqdm

from CustomLogger import CustomLogger
from Knife import Knife


def add_new_knives_to_db(
    names: Set[str], cur: cursor, conn: connection, logger: CustomLogger
) -> None:
    count = 0
    for name in tqdm(names):
        cur.execute("SELECT knife_name FROM knives WHERE knife_name = (%s)", (name,))
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO knives (knife_name) VALUES (%s)",
                (name,),
            )
            conn.commit()
            count += 1
    logger.info(f"Added {count} new knives to the database.")


def get_and_save_historical_pricing_helper(
    data: List[List],
    date_format: str,
    cur: cursor,
    conn: connection,
    knife_id: int,
) -> Tuple[Optional[float], Optional[datetime]]:
    price = None
    parsed_date = None
    for result in data:
        date_string = result[0][:-4]
        parsed_date = datetime.strptime(date_string, date_format)
        price = result[1]
        sold_count = result[2]
        cur.execute(
            "INSERT INTO SellTimes (sell_time) VALUES (%s) ON CONFLICT (sell_time) DO NOTHING RETURNING sell_time_id",
            (parsed_date,),
        )

        existing_date = cur.fetchone()
        if existing_date:
            date_id = existing_date[0]
        else:
            cur.execute(
                "SELECT sell_time_id FROM SellTimes WHERE sell_time = (%s)",
                (parsed_date,),
            )
            fetched_row = cur.fetchone()
            date_id = fetched_row[0] if fetched_row is not None else None

        if date_id is not None:
            cur.execute(
                "INSERT INTO SellHistory (knife_id, sell_time_id, price, quantity) VALUES (%s, %s, %s, %s) ON CONFLICT (knife_id, sell_time_id) DO NOTHING",
                (knife_id, date_id, price, sold_count),
            )
            conn.commit()
    return price, parsed_date


def save_knives_to_db(knives: List[Knife], cur: cursor, conn: connection) -> None:
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


def get_knife_list_from_db(cur: cursor, date: Optional[str] = None) -> List[str]:
    if date is None:
        select_query = (
            "SELECT knife_name FROM knives ORDER BY last_updated ASC NULLS FIRST"
        )
    else:
        select_query = "SELECT knife_name FROM knives WHERE last_updated < %s ORDER BY last_updated ASC NULLS FIRST"

    cur.execute(select_query, (date,) if date else None)
    knife_list = [name[0] for name in cur.fetchall()]
    return knife_list


def check_if_knife_id_is_correct(cur: cursor, knife_id: int, knife_name: str) -> bool:
    cur.execute("SELECT knife_id FROM knives WHERE knife_name = %s", (knife_name,))
    row = cur.fetchone()
    if row:
        knife_id_db = row[0]
        return knife_id_db == knife_id
    else:
        return False


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
) -> Tuple[connection, cursor]:
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
) -> Tuple[connection, cursor]:
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
