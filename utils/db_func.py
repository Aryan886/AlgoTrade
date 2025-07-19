import sqlite3
import pandas as pd
import os
from typing import Optional, List, Dict
from datetime import datetime
from config.config import CONFIG
from utils.black_scholes import (
    calculate_delta_for_strike_band,
    get_current_delta
)
from utils.iv import ProductionIVCalculator

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
            except:
                return None
        
        # Handle VIX value
        vix_val = float(vix_value) if vix_value is not None else None
        
        cursor.execute(f"""
            INSERT INTO {table_name} (
                timestamp, symbol, open, high, low, close,
                ao_value, donchian_upper, donchian_lower, donchian_mid, vix_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            safe_get_value(row, 'donchian_mid'),
            vix_val
        ))

    conn.commit()
    conn.close()
    print(f"Stored {len(df)} rows into {table_name} successfully!!!")

#Fetches historical market data for a given symbol and date range.
def fetch_market_data(symbol: str = 'NIFTY50', start=None, end=None, interval=None, db_path=DB_PATH):
    """
    Fetches historical market data from the database.
    Now includes VIX data alongside other market data.
    """
    conn = sqlite3.connect(db_path)
    
    # Determine which table to query based on interval
    if interval:
        table_name = f"market_data_{interval}"
        query = f"SELECT * FROM {table_name} WHERE 1=1"
    else:
        # Default to 5m if no interval specified
        query = "SELECT * FROM market_data_5m WHERE 1=1"
    
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

    df = pd.read_sql_query(query, conn, parse_dates=["timestamp"])
    df.set_index("timestamp", inplace=True)
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
    print(f"Stored {len(options_list)} high-accuracy options data points for {symbol} successfully!!!")


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
        # Log the age of cached data
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
        # Check if all cached options are expired
        all_expired = True
        for opt in cached_options:
            expiry = opt['expiry_date']
            if not isinstance(expiry, str):
                expiry = str(expiry)
            if expiry >= str(today):
                all_expired = False
                break
        # If all expired, fetch fresh option data
        if all_expired or not cached_options:
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
        # Determine strike band if not provided
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
                    # Robust delta value extraction and scaling
                    if isinstance(option_data, dict) and 'delta' in option_data and isinstance(option_data['delta'], (float, int)):
                        delta_value = option_data['delta'] * 100
                        ltp_value = option_data.get('ltp', None)
                    elif isinstance(option_data, (float, int)):
                        delta_value = option_data * 100
                        ltp_value = None
                    else:
                        delta_value = None  # Could not extract delta value
                        ltp_value = None
                    if delta_value is not None:
                        cursor.execute("""
                            INSERT INTO delta_cache (
                                timestamp, strike_price, option_type, delta,
                                expiry_date, spot_price, symbol, ltp
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            timestamp_str, strike, option_type, delta_value,
                            cached_options[0]['expiry_date'], spot_price, symbol, ltp_value
                        ))
            conn.commit()
            conn.close()
        print(f"High-accuracy delta calculated{' and stored' if ENABLE_DELTA_LOGGING else ''} for {len(delta_results)} strikes")
        return {
            'spot_price': spot_price,
            'strike_band': strike_band,
            'delta_results': delta_results,
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        print(f"Error in high-accuracy delta calculation: {e}")
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


