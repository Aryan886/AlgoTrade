#Making sure this thing works
from utils.data_fetcher import fetch_and_save_data
from strategies.indicators import compute_indicators
from utils.db_func import store_market_data, fetch_market_data
from utils.db_setup import create_tables
from utils.vix_fetcher import calculate_and_store_vix
from utils.db_migration import migrate_add_vix_column
import sqlite3

def verify_db_table(interval):
    table = f"market_data_{interval}"
    conn = sqlite3.connect("db/trading_bot.db")
    cursor = conn.cursor()

    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    print(f"[✓] {count} rows found in {table}")

    cursor.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 5")
    rows = cursor.fetchall()
    print(f"Last 5 rows from {table}:")
    for row in rows:
        print(row)

    conn.close()


# Get data with all indicators
df = fetch_market_data(symbol="NSEI", interval="5m")
# df contains: OHLCV + AO + Donchian + VIX