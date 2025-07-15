#Testing the scripts 

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

if __name__ == "__main__":
    # Ensure database tables exist and have VIX columns
    print("Setting up database...")
    create_tables()
    
    # Run migration to add VIX columns to existing tables
    print("Running database migration...")
    migrate_add_vix_column()
    
    symbol = "NSEI"
    
    # Calculate VIX once for all intervals
    print(f"\n--- Calculating VIX for {symbol} ---")
    vix_value = calculate_and_store_vix("^" + symbol)
    if vix_value:
        print(f"VIX calculated: {vix_value:.2f}")
    else:
        print("VIX calculation failed")
    
    for interval, period, ao_fast, ao_slow, donchian in [("1m", "1d", 3, 10, 10),
                                                         ("5m", "1d", 5, 34, 20),
                                                         ("15m", "1d", 5, 34, 20)]:
        print(f"\n--- Processing {interval} ---")
        
        # Fetch data and get the specific interval's dataframe
        results, df = fetch_and_save_data(symbol="^" + symbol, intervals=[interval], return_interval=interval, period=period)
        
        # Check if we got valid data
        if df is not None and not df.empty:
            df = compute_indicators(df, ao_fast=ao_fast, ao_slow=ao_slow, donchian_period=donchian)
            # Store market data with VIX included
            store_market_data(df, symbol=symbol, interval=interval, vix_value=vix_value)
            verify_db_table(interval)
        else:
            print(f"No valid data received for {interval}, skipping processing.")

