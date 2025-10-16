import sqlite3
import os

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

    #4. Market Data Table for 1m
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_data_1m (
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
            vix_value REAL
        );
    """)

    #5. Market Data Table for 5m
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_data_5m (
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
            vix_value REAL
        );
    """)

    #6. Market Data Table for 15m
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_data_15m (
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
            vix_value REAL
        );
    """)

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
