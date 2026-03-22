import sqlite3
import os

MARKET_DATA_COLUMNS = {
    "open": "REAL",
    "high": "REAL",
    "low": "REAL",
    "close": "REAL",
    "ao_value": "REAL",
    "donchian_upper": "REAL",
    "donchian_lower": "REAL",
    "donchian_mid": "REAL",
    "vix_value": "REAL",
    "SL": "REAL",
    "SH": "REAL",
}


def create_market_data_table(conn: sqlite3.Connection, interval: str) -> None:
    """Create or backfill the schema for a market_data_<interval> table."""
    table_name = f"market_data_{interval}"
    cursor = conn.cursor()

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            ao_value REAL,
            donchian_upper REAL,
            donchian_lower REAL,
            donchian_mid REAL,
            vix_value REAL,
            SL REAL,
            SH REAL
        );
    """)
    cursor.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_market_data_{interval}_symbol_timestamp
        ON {table_name} (symbol, timestamp);
    """)

    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {column[1] for column in cursor.fetchall()}

    for column_name, column_type in MARKET_DATA_COLUMNS.items():
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    conn.commit()


def create_tables(db_path='db/trading_bot.db'):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. VIX Data Table (drop and recreate)
    cursor.execute("DROP TABLE IF EXISTS vix_data;")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vix_data (
            timestamp TEXT PRIMARY KEY,
            symbol TEXT,
            vix_value REAL,
            vix_ao_value REAL,
            vix_donchian_upper REAL,
            vix_donchian_lower REAL,
            vix_donchian_mid REAL
        );
    """)

    # 2. Signals Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal TEXT,
            reason TEXT,
            confidence_score REAL
        );
    """)

    # 3. Trades Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            action TEXT,
            price REAL,
            qty INTEGER,
            status TEXT
        );
    """)

    #4-7. Market Data Tables for 1m, 5m, 15m, 1h
    for interval in ["1m", "5m", "15m", "1h"]:
        create_market_data_table(conn, interval)

    #7. Options Data Table for PE/CE storage
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS options_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            option_type TEXT NOT NULL,  -- 'CE' or 'PE'
            strike_price REAL NOT NULL,
            expiry_date TEXT NOT NULL,
            tradingsymbol TEXT NOT NULL,
            last_price REAL,
            open_interest INTEGER,
            implied_volatility REAL,
            delta REAL,
            spot_price REAL,
            atm_strike REAL
        );
    """)

    #8. High-Accuracy Options Data Table (for Black-Scholes calculations)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS option_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strike_price INTEGER NOT NULL,
            option_type TEXT NOT NULL,  -- 'CE' or 'PE'
            ltp REAL,
            iv REAL,
            expiry_date TEXT NOT NULL,
            spot_price REAL,
            tradingsymbol TEXT NOT NULL,
            open_interest INTEGER
        );
    """)

    #9. Delta Cache Table (for historical delta tracking)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS delta_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strike_price INTEGER NOT NULL,
            option_type TEXT NOT NULL,  -- 'CE' or 'PE'
            delta REAL,
            expiry_date TEXT NOT NULL,
            spot_price REAL,
            symbol TEXT NOT NULL
        );
    """)

    #10. Equity Data Tables for 1m, 5m, 15m
    for interval in ['1m', '5m', '15m']:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS equity_data_{interval} (
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                vwap REAL,
                ao_value REAL,
                donchian_upper REAL,
                donchian_lower REAL,
                donchian_mid REAL,
                PRIMARY KEY (timestamp, symbol)
            );
        """)
        
        #11. Equity SMA Tables for 1m, 5m, 15m
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS equity_sma_{interval} (
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                sma_5 REAL,
                sma_20 REAL,
                sma_5_high REAL,
                sma_5_low REAL,
                sma_20_high REAL,
                sma_20_low REAL,
                PRIMARY KEY (timestamp, symbol)
            );
        """)

    conn.commit()
    conn.close()
    print("Database schema created successfully.")

if __name__ == "__main__":
    create_tables()
