from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple

import mysql.connector
from mysql.connector import Error
from mysql.connector.connection import MySQLConnection
from mysql.connector.cursor import MySQLCursor
from tqdm import tqdm

from CustomLogger import CustomLogger
from Knife import Knife


def add_new_knives_to_db(
    names: List[str],
    cursor: MySQLCursor,
    connection: MySQLConnection,
    logger: CustomLogger,
) -> None:
    count = 0
    for name in tqdm(names):
        cursor.execute("SELECT knife_name FROM knives WHERE knife_name = (%s)", (name,))
        if cursor.fetchone() is None:
            cursor.execute("INSERT INTO knives (knife_name) VALUES (%s)", (name,))
            connection.commit()
            count += 1
    logger.info(f"Added {count} new knives to the database.")


# TODO: Optimize this b
def get_and_save_historical_pricing_helper(
    data: List[List],
    date_format: str,
    cursor: MySQLCursor,
    connection: MySQLConnection,
    knife_id: int,
) -> Tuple[Optional[float], Optional[datetime]]:
    price = None
    parsed_date = None
    # TODO: Batch insert
    for result in data:
        date_string = result[0][:-4]
        parsed_date = datetime.strptime(date_string, date_format)
        price = result[1]
        sold_count = result[2]
        cursor.execute(
            "SELECT sell_time_id FROM SellTimes WHERE sell_time = (%s)", (parsed_date,)
        )
        existing_date = cursor.fetchone()
        if not existing_date:
            cursor.execute(
                "INSERT IGNORE INTO SellTimes (sell_time) VALUES (%s)", (parsed_date,)
            )
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
        # TODO: IGNORE should in theory be unnecessary
        if date_id is not None:
            cursor.execute(
                "INSERT IGNORE INTO SellHistory (knife_id, sell_time_id, price, quantity) VALUES (%s, %s, %s, %s)",
                (knife_id, date_id, price, sold_count),
            )
            connection.commit()
    return price, parsed_date


def save_knives_to_db(
    knives: List[Knife], cursor: MySQLCursor, connection: MySQLConnection
) -> None:
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

    values = []
    for knife in knives:
        values.append(
            (
                knife.current_min_price_with_fee,
                knife.current_min_price_without_fee,
                knife.last_min_price_with_fee,
                knife.last_min_price_without_fee,
                knife.buy_order_price,
                knife.last_updated,
                knife.last_sold,
                knife.knife_image,
                knife.knife_name,
            )
        )

    cursor.executemany(base_query, values)
    connection.commit()


def get_knife_list_from_db(
    cursor: MySQLCursor, date: Optional[str] = None
) -> List[str]:
    if date is None:
        select_query = "SELECT knife_name FROM knives ORDER BY last_updated ASC"
    else:
        select_query = f"SELECT knife_name FROM knives WHERE last_updated < {date} ORDER BY last_updated ASC"
    cursor.execute(select_query)
    knife_list = [str(name[0]) for name in cursor.fetchall()]
    return knife_list


def get_knife_from_db(cursor: MySQLCursor, name: str) -> Optional[Knife]:
    cursor.execute("SELECT * FROM knives WHERE knife_name = %s", (name,))
    row = cursor.fetchone()
    if row:
        knife = Knife(
            knife_id=row[0],
            knife_name=str(row[1]),
            current_min_price_with_fee=float(row[2])
            if isinstance(row[2], (int, float, Decimal))
            else None,
            current_min_price_without_fee=float(row[3])
            if isinstance(row[3], (int, float, Decimal))
            else None,
            last_min_price_with_fee=float(row[4])
            if isinstance(row[4], (int, float, Decimal))
            else None,
            last_min_price_without_fee=float(row[5])
            if isinstance(row[5], (int, float, Decimal))
            else None,
            buy_order_price=float(row[6])
            if isinstance(row[6], (int, float, Decimal))
            else None,
            last_sold=row[9]
            if len(row) > 9 and isinstance(row[9], datetime)
            else None,  # Last sold is optional
        )
        return knife
    else:
        return None


def connect_to_db(
    host: str, database: str, port: int, user: str, password: str, logger: CustomLogger
):
    try:
        sql_connection = mysql.connector.connect(
            host=host, database=database, port=port, user=user, password=password
        )
        if sql_connection.is_connected():
            sql_cursor = sql_connection.cursor()
        else:
            logger.fatal("SQL connection error, likely invalid connection parameters")
            exit(1)

    except Error as e:
        logger.fatal("SQL connection error " + str(e))
        exit(1)
    return sql_connection, sql_cursor


def connect_to_db_threaded(
    host: str, database: str, port: int, user: str, password: str, logger: CustomLogger
):
    return connect_to_db(
        host=host,
        database=database,
        port=port,
        user=user,
        password=password,
        logger=logger,
    )


def update_all(cursor: MySQLCursor) -> None:
    update_amount_sold(cursor)
    update_selling_frequency(cursor)
    update_amount_sold_last_year(cursor)


def update_amount_sold(cursor: MySQLCursor) -> None:
    update_query = """
    UPDATE `knives`.`Knives` k
    JOIN (
        SELECT `knife_id`, COUNT(*) AS `total_sold`
        FROM `knives`.`SellHistory`
        GROUP BY `knife_id`
    ) sh ON k.`knife_id` = sh.`knife_id`
    SET k.`amount_sold` = sh.`total_sold`;
    """
    cursor.execute(update_query)


def update_selling_frequency(cursor: MySQLCursor) -> None:
    update_query = """
    UPDATE `knives`.`Knives` k
    JOIN (
        SELECT sh.`knife_id`, 
            COUNT(*) / TIMESTAMPDIFF(MONTH, MIN(st.`sell_time`), NOW()) AS `frequency`
        FROM `knives`.`SellHistory` sh
        JOIN `knives`.`SellTimes` st ON sh.`sell_time_id` = st.`sell_time_id`
        GROUP BY sh.`knife_id`
    ) sf ON k.`knife_id` = sf.`knife_id`
    SET k.`selling_frequency` = sf.`frequency`;
    """
    cursor.execute(update_query)


def update_amount_sold_last_year(cursor: MySQLCursor) -> None:
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
            st.sell_time >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR)
        GROUP BY 
            sh.knife_id
    )

    UPDATE Knives k
    LEFT JOIN LastYearSales lys ON k.knife_id = lys.knife_id
    SET k.amount_sold_last_year = COALESCE(lys.total_quantity_sold, 0);
    """
    cursor.execute(update_query)
