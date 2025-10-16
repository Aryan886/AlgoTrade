import sqlite3
import pandas as pd
import os
from typing import Optional, List, Dict
from datetime import datetime
from datetime import date
from config.config import CONFIG
from utils.black_scholes import (
    calculate_delta_for_strike_band,
    get_current_delta
)
from utils.iv import ProductionIVCalculator
from utils.utility import standardize_column_names
import re
from core.indicators import compute_smas, compute_intraday_vwap, add_ao_color

DB_PATH = 'db/trading_bot.db'

# Config values
STRIKE_BAND = CONFIG["STRIKE_BAND"]
STRIKE_INTERVAL = CONFIG["STRIKE_INTERVAL"]
RISK_FREE_RATE = CONFIG["RISK_FREE_RATE"]
PRUNE_AFTER_MINUTES = CONFIG["PRUNE_AFTER_MINUTES"]
ENABLE_DELTA_LOGGING = CONFIG["ENABLE_DELTA_LOGGING"]

EXPECTED_COLUMNS = ['open', 'high', 'low', 'close', 'ao_value', 'donchian_upper', 'donchian_lower', 'donchian_mid']

def ensure_db_dir(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)

#Stores OHLCV + AO + Donchian channel data into the market_data table.
def store_market_data(df, symbol: str = 'NIFTY50', interval: str = '5m', vix_value: Optional[float] = None, db_path='db/trading_bot.db'):
    """
    Stores processed DataFrame into respective interval-based market_data table.
    Now includes VIX data alongside other market data.
    """
    table_name = f"market_data_{interval}"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Standardize columns to lowercase for Kite API compatibility
    df.columns = [col.lower() for col in df.columns]

    # Warn if any expected columns are missing
    missing_cols = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing_cols:
        print(f"[WARN] DataFrame is missing columns: {missing_cols}")

    for ts, row in df.iterrows():
        # Convert timestamp to string and handle NaN values
        timestamp_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, 'strftime') else str(ts)
        
        # Safe value extraction function
        def safe_get_value(row, column_name):
            try:
                if column_name in df.columns:
                    value = row[column_name]
                    return float(value) if pd.notna(value) else None
                return None
            except Exception:
                return None
        
        cursor.execute(f"""
            INSERT INTO {table_name} (
                timestamp, symbol, open, high, low, close,
                ao_value, donchian_upper, donchian_lower, donchian_mid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            timestamp_str,
            symbol,
            safe_get_value(row, 'open'),
            safe_get_value(row, 'high'),
            safe_get_value(row, 'low'),
            safe_get_value(row, 'close'),
            safe_get_value(row, 'ao_value'),
            safe_get_value(row, 'donchian_upper'),
            safe_get_value(row, 'donchian_lower'),
            safe_get_value(row, 'donchian_mid')
        ))

    conn.commit()
    conn.close()
    print(f"Market data stored successfully in {table_name}.")

#Fetches historical market data for a given symbol and date range.
def fetch_market_data(symbol: str = 'NIFTY50', start=None, end=None, interval=None, limit=200, db_path=DB_PATH):
    """
    Fetches historical market data from the database.
    Default limit of 200 prevents memory issues with large datasets.
    Set limit=None to fetch all data (use with caution).
    """
    conn = sqlite3.connect(db_path)
    print(f"[DEBUG] fetch_market_data called with symbol={symbol}, limit={limit}")
    
    # Determine which table to query based on interval
    if interval:
        table_name = f"market_data_{interval}"
        query = f"SELECT * FROM {table_name} WHERE 1=1"
    else:
        # Default to 5m if no interval specified
        query = "SELECT * FROM market_data_5m WHERE 1=1"
    
    params = []

    if symbol is not None and symbol != "":
        query += " AND symbol = ?"
        params.append(symbol)
    if start:
        query += " AND timestamp >= ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)

    query += " ORDER BY timestamp DESC"  # Recent data first
    
    # Add LIMIT clause unless explicitly set to None
    if limit is not None:
        query += f" LIMIT {int(limit)}"

    print(f"[DEBUG] Final query : {query}")
    print(f"[DEBUG] Params : {params}")

    df = pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp"])
    df.set_index("timestamp", inplace=True)
    df = standardize_column_names(df)
    
    # If we used LIMIT with DESC, reverse to get chronological order
    if limit is not None:
        df = df.iloc[::-1]
    
    conn.close()
    return df

#Stores VIX data into the vix_data table.
def store_vix_data(timestamp, symbol, vix_value, db_path=DB_PATH):
    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO vix_data (timestamp, symbol, vix_value)
        VALUES (?, ?, ?)
    """, (timestamp, symbol, vix_value))

    conn.commit()
    conn.close()

def store_vix_data_bulk(df, symbol: str, db_path=DB_PATH):
    """
    Stores VIX data from a DataFrame into the vix_data table.
    
    Parameters:
        df: DataFrame with columns ['timestamp', 'vix_value']
        symbol: Symbol name (e.g., 'NIFTY50')
        db_path: Database path
    """
    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for ts, row in df.iterrows():
        # Convert timestamp to string
        timestamp_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, 'strftime') else str(ts)
        
        # Get VIX value
        vix_value = row.get('vix_value', row.get('vix', None))
        
        if vix_value is not None and pd.notna(vix_value):
            cursor.execute("""
                INSERT INTO vix_data (timestamp, symbol, vix_value)
                VALUES (?, ?, ?)
            """, (timestamp_str, symbol, float(vix_value)))

    conn.commit()
    conn.close()
    print(f"Stored {len(df)} VIX data points for {symbol} successfully!!!")

def fetch_vix_data(symbol: str = 'NIFTY50', start=None, end=None, db_path=DB_PATH):
    """
    Fetches VIX data from the database.
    
    Parameters:
        symbol: Symbol to filter by (optional)
        start: Start date (optional)
        end: End date (optional)
        db_path: Database path
    
    Returns:
        DataFrame with VIX data
    """
    conn = sqlite3.connect(db_path)
    query = "SELECT * FROM vix_data WHERE 1=1"
    params = []

    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    if start:
        query += " AND timestamp >= ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)

    query += " ORDER BY timestamp"

    df = pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp"])
    df.set_index("timestamp", inplace=True)
    #df = standardize_column_names(df)
    conn.close()
    return df

#Logs trading signals with reasons and confidence scores.
def store_signal(timestamp, symbol, signal, reason=None, confidence_score=None, db_path=DB_PATH):
    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Convert timestamp to string if it's a datetime or pandas Timestamp
    if hasattr(timestamp, 'strftime'):
        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    else:
        timestamp_str = str(timestamp)

    cursor.execute("""
        INSERT INTO signals (timestamp, symbol, signal, reason, confidence_score)
        VALUES (?, ?, ?, ?, ?)
    """, (timestamp_str, symbol, signal, reason, confidence_score))

    conn.commit()
    conn.close()

#Logs actual trade execution info.
def store_trade(timestamp, symbol, action, price, qty, status='pending', db_path=DB_PATH):  
    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO trades (timestamp, symbol, action, price, qty, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (timestamp, symbol, action, price, qty, status))

    conn.commit()
    conn.close()

# High-accuracy options data storage
def store_high_accuracy_options_data(options_list: List[Dict], symbol: str, spot_price: float, db_path=DB_PATH):
    """
    Store high-accuracy options data for Black-Scholes delta calculations.
    Prunes old data based on config.
    """
    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Prune old data
    cursor.execute(f"""
        DELETE FROM option_data 
        WHERE symbol = ? AND timestamp < datetime('now', '-{PRUNE_AFTER_MINUTES} minutes')
    """, (symbol,))
    for option in options_list:
        try:
            cursor.execute("""
                INSERT INTO option_data (
                    timestamp, symbol, strike_price, option_type,
                    ltp, iv, expiry_date, spot_price, tradingsymbol, open_interest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp_str, symbol, option['strike_price'], option['option_type'],
                option['ltp'], option['iv'], option['expiry_date'], spot_price,
                option['tradingsymbol'], option.get('open_interest', 0)
            ))
        except Exception as e:
            print(f"Error storing option {option.get('tradingsymbol', 'unknown')}: {e}")
            continue
    conn.commit()
    conn.close()
    print("Options data stored successfully.")

def fetch_cached_options_data(symbol: str = 'NIFTY50', db_path=DB_PATH) -> List[Dict]:
    """
    Fetch cached options data for delta calculations.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM option_data 
        WHERE symbol = ? 
        ORDER BY timestamp DESC 
        LIMIT 100
    """, (symbol,))
    rows = cursor.fetchall()
    conn.close()
    options_data = []
    for row in rows:
        options_data.append({
            'strike_price': row[3],
            'option_type': row[4],
            'ltp': row[5],
            'iv': row[6],
            'expiry_date': row[7],
            'spot_price': row[8],
            'tradingsymbol': row[9],
            'open_interest': row[10]
        })
    return options_data

def calculate_and_store_high_accuracy_delta(
    symbol: str = 'NIFTY50',
    strike_band: Optional[List[int]] = None,
    db_path=DB_PATH
) -> Optional[Dict]:
    """
    Calculate and store high-accuracy delta using Black-Scholes formula.
    Uses config for band, interval, logging, and risk-free rate.
    If all cached options are expired, triggers a fresh fetch and retries.
    """
    from datetime import date, datetime as dt
    try:
        # Get current spot price
        from broker.zerodha_client import kite_from_saved_token
        kite = kite_from_saved_token()
        if not kite:
            print("Failed to connect to Kite API")
            return None
        quote = kite.quote("NSE:NIFTY 50")
        if quote and "NSE:NIFTY 50" in quote:
            quote_data = quote["NSE:NIFTY 50"]
            if isinstance(quote_data, dict) and "last_price" in quote_data:
                spot_price = quote_data["last_price"]
            else:
                print("Could not get spot price")
                return None
        else:
            print("Could not get spot price")
            return None
        
        # Get cached options data
        cached_options = fetch_cached_options_data(symbol, db_path)
        #print(f"[DEBUG] Total cached_options: {len(cached_options)}")
        #print("[DEBUG] First 5 cached options:")
        """
        
        for i, opt in enumerate(cached_options[:5]):
            print(f"  {i}: strike={opt['strike_price']}, type={opt['option_type']}, symbol={opt['tradingsymbol']}")
        
        """
        
        # Log the age of cached data

        newest_age = None
        if cached_options:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp FROM option_data WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1", (symbol,))
            newest = cursor.fetchone()
            cursor.execute("SELECT timestamp FROM option_data WHERE symbol = ? ORDER BY timestamp ASC LIMIT 1", (symbol,))
            oldest = cursor.fetchone()
            conn.close()
            now = dt.now()
            if newest and oldest:
                newest_age = (now - dt.strptime(newest[0], "%Y-%m-%d %H:%M:%S")).total_seconds()
                oldest_age = (now - dt.strptime(oldest[0], "%Y-%m-%d %H:%M:%S")).total_seconds()
                print(f"[CACHE] Option data age: newest = {newest_age:.1f}s, oldest = {oldest_age:.1f}s")

        today = date.today()
        print(f"DEBUG: today = {today}, type = {type(today)}")  # Add this debug line
        
        # Check if all cached options are expired
        all_expired = True
        today_str = str(today) if today else None

        if not today_str:
            print("ERROR: Could not get today's date..")
            return None

        for opt in cached_options:
            expiry = opt['expiry_date']
            if not isinstance(expiry, str):
                expiry = str(expiry)
            if expiry >= str(today):
                all_expired = False
                break
                
        # Refresh cache if expired/empty OR too stale by age
        STALE_THRESHOLD_SECONDS = 15
        is_stale_by_age = newest_age is not None and newest_age > STALE_THRESHOLD_SECONDS
        if all_expired or not cached_options or is_stale_by_age:
            print("All cached options are expired or cache is empty. Fetching fresh option data...")
            from utils.vix_fetcher import fetch_live_option_chain, store_high_accuracy_options_data
            fresh_options = fetch_live_option_chain()
            if not fresh_options:
                print("Failed to fetch fresh option data.")
                return None
                
            # Map fresh options to expected format
            formatted_options = []
            iv_calc = ProductionIVCalculator()
            for opt in fresh_options:
                expiry_date = str(opt.get('expiry'))
                spot = spot_price
                strike = opt.get('strikePrice')
                ltp = opt.get('lastPrice')
                option_type = opt.get('optionType')
                # Calculate IV using the new ProductionIVCalculator
                iv_val = None
                if spot and strike and ltp and option_type:
                    try:
                        iv_result = iv_calc.calculate_iv(spot, strike, ltp, expiry_date, option_type)
                        iv_val = iv_result['iv'] if iv_result and 'iv' in iv_result else None
                    except Exception:
                        iv_val = None
                formatted_options.append({
                    'strike_price': strike,
                    'option_type': option_type,
                    'ltp': ltp,
                    'iv': iv_val,  # <-- store as decimal (manual IV)
                    'expiry_date': expiry_date,
                    'spot_price': spot,
                    'tradingsymbol': opt.get('tradingsymbol'),
                    'open_interest': opt.get('openInterest', 0)
                })
            store_high_accuracy_options_data(formatted_options, symbol, spot_price, db_path)
            cached_options = fetch_cached_options_data(symbol, db_path)
            
        if not cached_options:
            print("No options data available after refresh.")
            return None
            
        if strike_band is None:
            strikes = [opt['strike_price'] for opt in cached_options]
            atm_strike = min(strikes, key=lambda x: abs(x - spot_price))
            band = STRIKE_BAND
            interval = STRIKE_INTERVAL
            strike_band = [atm_strike + i*interval for i in range(-band//interval, band//interval+1)]
            
        # Calculate deltas for the strike band
        delta_results = calculate_delta_for_strike_band(
            spot_price=spot_price,
            strike_band=strike_band,
            option_data=cached_options
        )
        
        # Store delta results in delta_cache table if logging enabled
        if ENABLE_DELTA_LOGGING:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for strike, strike_data in delta_results.items():
                for option_type, option_data in strike_data.items():
                    #print(f"[DEBUG] Looking for: strike={strike}, type={option_type}")
                    """
                    # Find the correct tradingsymbol for this specific strike and option_type
                    correct_tradingsymbol = 'N/A'
                    for opt in cached_options:
                        if opt['strike_price'] == strike and opt['option_type'] == option_type:
                            #print(f"[DEBUG] FOUND MATCH: {opt['tradingsymbol']}")
                            correct_tradingsymbol = opt.get('tradingsymbol', 'N/A')
                            break
                    
                    if correct_tradingsymbol == 'N/A':
                        print(f"[DEBUG] NO MATCH FOUND for strike={strike}, type={option_type}")
                   """
                    # Robust delta value extraction and scaling
                    if isinstance(option_data, dict) and 'delta' in option_data and isinstance(option_data['delta'], (float, int)):
                        delta_value = option_data['delta'] * 100
                        ltp_value = option_data.get('ltp', None)
                        selected_tradingsymbol = option_data.get('tradingsymbol', 'N/A')
                        selected_expiry = option_data.get('expiry_date')
                    elif isinstance(option_data, (float, int)):
                        delta_value = option_data * 100
                        ltp_value = None
                        selected_tradingsymbol = 'N/A'
                        selected_expiry = None
                    else:
                        delta_value = None  # Could not extract delta value
                        ltp_value = None
                        selected_tradingsymbol = 'N/A'
                        selected_expiry = None
                    if delta_value is not None:
                        """
                        correct_tradingsymbol = 'N/A'
                        for opt in cached_options:
                            if opt['strike_price'] == strike and opt['option_type'] == option_type:
                                correct_tradingsymbol = opt.get('tradingsymbol', 'N/A')
                                break
                        """
                        cursor.execute("""
                            INSERT INTO delta_cache (
                                timestamp, strike_price, option_type, delta,
                                expiry_date, spot_price, symbol, ltp, tradingsymbol
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            timestamp_str, strike, option_type, delta_value,
                            selected_expiry, spot_price, symbol, ltp_value,
                            selected_tradingsymbol
                        ))
            conn.commit()
            conn.close()
        print("Delta calculations completed and stored successfully.")
        return {
            'spot_price': spot_price,
            'strike_band': strike_band,
            'delta_results': delta_results,
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        print(f"Error in delta calculation: {e}")
        return None

def get_current_delta_from_cache(
    option_type: str,
    strike: int,
    symbol: str = 'NIFTY50',
    db_path=DB_PATH
) -> Optional[float]:
    """
    Get current delta for a specific option from cache.
    """
    try:
        # Get current spot price
        from broker.zerodha_client import kite_from_saved_token
        kite = kite_from_saved_token()
        if not kite:
            return None
        quote = kite.quote("NSE:NIFTY 50")
        if quote and "NSE:NIFTY 50" in quote:
            quote_data = quote["NSE:NIFTY 50"]
            if isinstance(quote_data, dict) and "last_price" in quote_data:
                spot_price = quote_data["last_price"]
            else:
                return None
        else:
            return None
        # Get cached options data
        cached_options = fetch_cached_options_data(symbol, db_path)
        if not cached_options:
            return None
        # Calculate delta using Black-Scholes
        delta = get_current_delta(
            option_type=option_type,
            strike=strike,
            spot_price=spot_price,
            cached_options=cached_options
        )
        return delta
    except Exception as e:
        print(f"Error getting current delta: {e}")
        return None

def print_latest_market_data_timestamps(db_path=DB_PATH):
    """
    Print the latest timestamp for 5m and 15m data in the database.
    """
    import sqlite3
    for interval in ["5m", "15m"]:
        table_name = f"market_data_{interval}"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT MAX(timestamp) FROM {table_name}")
            result = cursor.fetchone()
            print(f"Latest {interval} data timestamp: {result[0]}")
        except Exception as e:
            print(f"Error querying {table_name}: {e}")
        finally:
            conn.close()

def fetch_latest_delta_data(symbol: str = 'NIFTY50', db_path=DB_PATH) -> List[Dict]:
    """
    Fetches the most recent delta data for all strikes from the delta_cache table.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Find the most recent timestamp in the delta_cache
    cursor.execute("SELECT MAX(timestamp) FROM delta_cache WHERE symbol = ?", (symbol,))
    latest_timestamp = cursor.fetchone()[0]

    if not latest_timestamp:
        conn.close()
        return []

    # Fetch all records with that timestamp
    cursor.execute("""
        SELECT strike_price, option_type, delta, ltp, tradingsymbol
        FROM delta_cache
        WHERE timestamp = ? AND symbol = ?
    """, (latest_timestamp, symbol))

    rows = cursor.fetchall()
    conn.close()

    options_data = []
    for row in rows:
        options_data.append({
            'strike_price': row[0],
            'option_type': row[1],
            'delta': row[2],
            'ltp': row[3],
            'tradingsymbol': row[4]
        })
    return options_data

def sma_table_name(interval : str) -> str:
    # Replace any characters not letters/numbers with underscore
    safe_interval = re.sub(r'[^0-9a-zA-Z]+', '_', str(interval))
    return f"market_sma_{safe_interval}"

def create_sma_table(conn: sqlite3.Connection, interval : str):
    safe_interval = re.sub(r'[^0-9a-zA-Z]+', '_', str(interval))
    tbl = f"market_sma_{safe_interval}"

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {tbl}(
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            sma_5 REAL,
            sma_20 REAL,
            PRIMARY KEY (timestamp, symbol)
        );      
    """)
    conn.commit()
    
def store_sma_from_df(df: pd.DateOffset, symbol : str, interval: str, db_path= DB_PATH):
    """
    Compute SMA on df and store into market_sma_<interval>.
    - df: DataFrame indexed by timestamp (index can be DatetimeIndex or strings). Must contain 'close'.
    - symbol: single symbol string (e.g., 'NIFTY50').
    - interval: '1m','5m','15m'
    - db_path: path to sqlite DB file
    """
    df_smas = compute_smas(df)

    #prepare rows for insertoin
    rows = []
    for ts, row in df_smas.iterrows():
        #keep timestamp format consistent 
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
        sma5 = float(row['sma_5'] if pd.notna(row['sma_5']) else None)
        sma20 = float(row['sma_20'] if pd.notna(row['sma_20']) else None)
        rows.append((ts_str, symbol, sma5, sma20))

    conn = sqlite3.connect(db_path, timeout=20)
    try:
        create_sma_table(conn, interval)
        tbl = sma_table_name(interval)
        cur = conn.cursor()
        #Insert or Replace so same code wont create duplicate
        cur.executemany(
            f"INSERT OR REPLACE INTO {tbl}(timestamp,symbol, sma_5, sma_20) Values (?, ?, ?, ?);",
            rows
        )

        conn.commit()
    finally:
        conn.close()

def create_equity_table(conn: sqlite3.Connection, interval: str) -> None:
    """Create an equity_data_<interval> table if it doesn't exist."""
    tbl = f"equity_data_{interval}"
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {tbl} (
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            vwap REAL,
            ao_value REAL,
            ao_color INTEGER,
            donchian_upper REAL,
            donchian_lower REAL,
            donchian_mid REAL,
            PRIMARY KEY (timestamp, symbol)
        );
""")
    conn.commit()

def create_equity_sma_table(conn: sqlite3.Connection, interval: str) -> None:
    """Create an equity_sma_<interval> table if it doesn't exist."""
    tbl = f"equity_sma_{interval}"
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {tbl} (
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

def store_equity_data(
    df: pd.DataFrame,
    symbol: str,
    interval: str = '5m',
    db_path: str = DB_PATH
) -> int:
    """
    Store OHLCV + VWAP for equities into equity_data_<interval>.

    - df: DataFrame indexed by timestamp (DatetimeIndex). Columns expected: open, high, low, close, volume (case-insensitive).
    - symbol: equity symbol (e.g., 'INFY').
    - interval: '1m','5m','15m' etc. used to name the table.
    - Returns: number of rows inserted/updated.
    """
    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path, timeout=20)
    cur = conn.cursor()

    try:
        # Normalize column names to lowercase (consistent with store_market_data)
        df = df.copy()
        df.columns = [col.lower() for col in df.columns]

        # Compute VWAP if not present or contains NaNs
        if 'vwap' not in df.columns or df['vwap'].isna().any():
            # Only compute VWAP if volume exists; otherwise fill with None
            if 'volume' in df.columns:
                try:
                    df['vwap'] = compute_intraday_vwap(df)
                except KeyError as e:
                    # missing required column(s) for vwap; set vwap to NaN
                    print(f"[WARN] VWAP computation failed: {e}")
                    df['vwap'] = pd.NA
            else:
                print("[WARN] 'volume' column missing — VWAP will be empty")
                df['vwap'] = pd.NA

        # Compute Awesome Oscillator (AO) values and colors
        if 'ao_value' in df.columns:
            df = add_ao_color(df)
        # Ensure table exists
        create_equity_table(conn, interval)
        tbl = f"equity_data_{interval}"

        insert_sql = f"""
            INSERT OR REPLACE INTO {tbl}
            (timestamp, symbol, open, high, low, close, volume, vwap, ao_value,ao_color, donchian_upper, donchian_lower, donchian_mid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """

        rows = []
        for ts, row in df.iterrows():
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, 'strftime') else str(ts)

            def safe(x):
                return float(x) if pd.notna(x) else None

            rows.append((
                ts_str,
                symbol,
                safe(row.get('open')),
                safe(row.get('high')),
                safe(row.get('low')),
                safe(row.get('close')),
                safe(row.get('volume')),
                safe(row.get('vwap')), 
                safe(row.get('ao_value')),
                safe(row.get('ao_color')),
                safe(row.get('donchian_upper')),
                safe(row.get('donchian_lower')),
                safe(row.get('donchian_mid')),
            ))

        cur.executemany(insert_sql, rows)
        conn.commit()
        return len(rows)

    finally:
        conn.close()

def store_equity_sma_from_df(df: pd.DataFrame, symbol: str, interval: str, db_path: str = DB_PATH) -> int:
    """
    Compute SMA with high/low tracking on df and store into equity_sma_<interval>.
    
    Parameters:
        df: DataFrame indexed by timestamp (DatetimeIndex). Must contain 'close', 'high', 'low'.
        symbol: equity symbol (e.g., 'INFY').
        interval: '1m','5m','15m' etc. used to name the table.
        db_path: path to sqlite DB file
        
    Returns:
        Number of rows inserted/updated
    """
    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path, timeout=20)
    
    try:
        # Import the new SMA function
        from core.indicators import compute_smas_with_high_low
        
        # Compute SMAs with high/low tracking
        df_smas = compute_smas_with_high_low(df)
        
        # Prepare rows for insertion
        rows = []
        for ts, row in df_smas.iterrows():
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, 'strftime') else str(ts)
            
            def safe(x):
                return float(x) if pd.notna(x) else None
            
            rows.append((
                ts_str,
                symbol,
                safe(row.get('sma_5')),
                safe(row.get('sma_20')),
                safe(row.get('sma_5_high')),
                safe(row.get('sma_5_low')),
                safe(row.get('sma_20_high')),
                safe(row.get('sma_20_low'))
            ))
        
        # Ensure table exists and insert data
        create_equity_sma_table(conn, interval)
        tbl = f"equity_sma_{interval}"
        cur = conn.cursor()
        
        cur.executemany(
            f"INSERT OR REPLACE INTO {tbl}(timestamp, symbol, sma_5, sma_20, sma_5_high, sma_5_low, sma_20_high, sma_20_low) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            rows
        )
        
        conn.commit()
        return len(rows)
        
    finally:
        conn.close()

def fetch_equity_sma_data(
    symbol: str,
    start: str = None,
    end: str = None,
    interval: str = '5m',
    limit: int = 200,
    db_path: str = DB_PATH
) -> pd.DataFrame:
    """
    Fetch equity SMA data from equity_sma_<interval>.
    
    Parameters:
        symbol: equity symbol (e.g., 'INFY').
        start: start datetime string (optional).
        end: end datetime string (optional).
        interval: '1m','5m','15m' etc. used to name the table.
        limit: max rows to fetch (default 200, None for no limit).
        
    Returns:
        DataFrame indexed by timestamp with SMA and high/low data.
    """
    conn = sqlite3.connect(db_path)
    tbl = f"equity_sma_{interval}"
    query = f"SELECT * FROM {tbl} WHERE symbol = ?"
    params = [symbol]

    if start:
        query += " AND timestamp >= ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)

    query += " ORDER BY timestamp DESC"
    if limit is not None:
        query += f" LIMIT {int(limit)}"

    df = pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp"])
    df.set_index("timestamp", inplace=True)
    df = standardize_column_names(df)

    # Reverse if limited to get chronological order
    if limit is not None:
        df = df.iloc[::-1]

    conn.close()
    return df

def migrate_create_equity_tables(db_path=DB_PATH):
    """Ensure equity_data_1m/5m/15m and equity_sma_1m/5m/15m tables exist."""
    conn = sqlite3.connect(db_path)
    try:
        for interval in ["1m", "5m", "15m"]:
            create_equity_table(conn, interval)
            create_equity_sma_table(conn, interval)
        print("Equity tables and SMA tables created/verified successfully.")
    finally:
        conn.close()

def fetch_equity_data(
    symbol: str,
    start: str = None,
    end: str = None,
    interval: str = '5m',
    limit: int = 200,
    db_path: str = DB_PATH
) -> pd.DataFrame:
    """
    Fetch equity OHLCV + VWAP data from equity_data_<interval>.

    - symbol: equity symbol (e.g., 'INFY').
    - start: start datetime string (optional).
    - end: end datetime string (optional).
    - interval: '1m','5m','15m' etc. used to name the table.
    - limit: max rows to fetch (default 200, None for no limit).
    - Returns: DataFrame indexed by timestamp.
    """
    conn = sqlite3.connect(db_path)
    tbl = f"equity_data_{interval}"
    query = f"SELECT * FROM {tbl} WHERE symbol = ?"
    params = [symbol]

    if start:
        query += " AND timestamp >= ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)

    query += " ORDER BY timestamp DESC"
    if limit is not None:
        query += f" LIMIT {int(limit)}"

    df = pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp"])
    df.set_index("timestamp", inplace=True)
    df = standardize_column_names(df)

    # Reverse if limited to get chronological order
    if limit is not None:
        df = df.iloc[::-1]

    conn.close()
    return df

if __name__ == "__main__":
    migrate_create_equity_tables()
    print("Equity tables ensured.")