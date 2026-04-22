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


def _create_open_interest_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS option_open_interest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            tradingsymbol TEXT NOT NULL,
            instrument_token INTEGER,
            expiry_date TEXT NOT NULL,
            strike_price REAL NOT NULL,
            option_type TEXT NOT NULL,
            spot_price REAL,
            last_price REAL,
            open_interest INTEGER,
            oi_day_high INTEGER,
            oi_day_low INTEGER,
            vwap REAL,
            last_trade_time TEXT,
            volume INTEGER
        );
    """)


def create_open_interest_5m_table(conn: sqlite3.Connection) -> None:
    """Create or backfill the derived 5-minute option OI/VWAP table."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS option_open_interest_5m (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            tradingsymbol TEXT NOT NULL,
            instrument_token INTEGER,
            expiry_date TEXT NOT NULL,
            strike_price REAL NOT NULL,
            option_type TEXT NOT NULL,
            spot_price REAL,
            last_price REAL,
            open_interest INTEGER,
            oi_day_high INTEGER,
            oi_day_low INTEGER,
            vwap REAL,
            bucket_volume INTEGER,
            is_carry_forward INTEGER NOT NULL DEFAULT 0,
            source_start_timestamp TEXT,
            source_end_timestamp TEXT,
            source_snapshot_count INTEGER,
            last_trade_time TEXT
        );
    """)

    required_columns = {
        "timestamp": "TEXT",
        "symbol": "TEXT",
        "exchange": "TEXT NOT NULL DEFAULT 'NFO'",
        "tradingsymbol": "TEXT",
        "instrument_token": "INTEGER",
        "expiry_date": "TEXT NOT NULL DEFAULT ''",
        "strike_price": "REAL NOT NULL DEFAULT 0",
        "option_type": "TEXT NOT NULL DEFAULT ''",
        "spot_price": "REAL",
        "last_price": "REAL",
        "open_interest": "INTEGER",
        "oi_day_high": "INTEGER",
        "oi_day_low": "INTEGER",
        "vwap": "REAL",
        "bucket_volume": "INTEGER",
        "is_carry_forward": "INTEGER NOT NULL DEFAULT 0",
        "source_start_timestamp": "TEXT",
        "source_end_timestamp": "TEXT",
        "source_snapshot_count": "INTEGER",
        "last_trade_time": "TEXT",
    }

    cursor.execute("PRAGMA table_info(option_open_interest_5m)")
    existing_columns = {column[1] for column in cursor.fetchall()}
    for column_name, column_type in required_columns.items():
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE option_open_interest_5m ADD COLUMN {column_name} {column_type}")

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_option_oi_5m_timestamp_symbol_tradingsymbol
        ON option_open_interest_5m (timestamp, symbol, tradingsymbol);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_option_oi_5m_symbol_timestamp
        ON option_open_interest_5m (symbol, timestamp);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_option_oi_5m_contract_replay
        ON option_open_interest_5m (symbol, expiry_date, strike_price, option_type, timestamp);
    """)
    conn.commit()


def _rebuild_open_interest_without_quote_timestamp(
    cursor: sqlite3.Cursor,
    existing_columns: set[str],
) -> None:
    """Drop the removed quote_timestamp column while preserving existing rows."""
    cursor.execute("DROP INDEX IF EXISTS ux_option_oi_timestamp_symbol_tradingsymbol")
    cursor.execute("DROP INDEX IF EXISTS idx_option_oi_symbol_timestamp")
    cursor.execute("DROP INDEX IF EXISTS idx_option_oi_contract_replay")
    cursor.execute("ALTER TABLE option_open_interest RENAME TO option_open_interest_old")
    _create_open_interest_table(cursor)

    insert_columns = [
        "id",
        "timestamp",
        "symbol",
        "exchange",
        "tradingsymbol",
        "instrument_token",
        "expiry_date",
        "strike_price",
        "option_type",
        "spot_price",
        "last_price",
        "open_interest",
        "oi_day_high",
        "oi_day_low",
        "vwap",
        "last_trade_time",
        "volume",
    ]
    defaults = {
        "exchange": "'NFO'",
        "tradingsymbol": "''",
        "expiry_date": "''",
        "strike_price": "0",
        "option_type": "''",
    }
    select_columns = [
        column_name if column_name in existing_columns else defaults.get(column_name, "NULL")
        for column_name in insert_columns
    ]

    cursor.execute(f"""
        INSERT INTO option_open_interest ({", ".join(insert_columns)})
        SELECT {", ".join(select_columns)}
        FROM option_open_interest_old;
    """)
    cursor.execute("DROP TABLE option_open_interest_old")


def create_open_interest_table(conn: sqlite3.Connection) -> None:
    """Create or backfill the append-only NIFTY option OI snapshot table."""
    cursor = conn.cursor()
    _create_open_interest_table(cursor)

    required_columns = {
        "timestamp": "TEXT",
        "symbol": "TEXT",
        "exchange": "TEXT NOT NULL DEFAULT 'NFO'",
        "tradingsymbol": "TEXT",
        "instrument_token": "INTEGER",
        "expiry_date": "TEXT NOT NULL DEFAULT ''",
        "strike_price": "REAL NOT NULL DEFAULT 0",
        "option_type": "TEXT NOT NULL DEFAULT ''",
        "spot_price": "REAL",
        "last_price": "REAL",
        "open_interest": "INTEGER",
        "oi_day_high": "INTEGER",
        "oi_day_low": "INTEGER",
        "vwap": "REAL",
        "last_trade_time": "TEXT",
        "volume": "INTEGER",
    }

    cursor.execute("PRAGMA table_info(option_open_interest)")
    existing_columns = {column[1] for column in cursor.fetchall()}

    if "quote_timestamp" in existing_columns:
        _rebuild_open_interest_without_quote_timestamp(cursor, existing_columns)
        cursor.execute("PRAGMA table_info(option_open_interest)")
        existing_columns = {column[1] for column in cursor.fetchall()}

    for column_name, column_type in required_columns.items():
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE option_open_interest ADD COLUMN {column_name} {column_type}")

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_option_oi_timestamp_symbol_tradingsymbol
        ON option_open_interest (timestamp, symbol, tradingsymbol);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_option_oi_symbol_timestamp
        ON option_open_interest (symbol, timestamp);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_option_oi_contract_replay
        ON option_open_interest (symbol, expiry_date, strike_price, option_type, timestamp);
    """)
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

    create_open_interest_table(conn)
    create_open_interest_5m_table(conn)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS ix_option_data_symbol_timestamp
        ON option_data (symbol, timestamp);
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

    #10. Append-only option open-interest snapshots
    create_open_interest_table(conn)

    #11. Equity Data Tables for 1m, 5m, 15m
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
        
        #12. Equity SMA Tables for 1m, 5m, 15m
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
