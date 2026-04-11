import sqlite3
import pandas as pd
import os
from typing import Any, Optional, List, Dict
from datetime import date, datetime, timedelta
from config.config import CONFIG
from utils.black_scholes import (
    calculate_delta_for_strike_band,
    get_current_delta
)
from utils.iv import ProductionIVCalculator
from utils.utility import standardize_column_names
import re
from core.indicators import compute_smas, compute_smas_with_high_low, compute_intraday_vwap, add_ao_color, nifty_skipping_low, nifty_skipping_high

DB_PATH = 'db/trading_bot.db'

# Config values
STRIKE_BAND = CONFIG["STRIKE_BAND"]
STRIKE_INTERVAL = CONFIG["STRIKE_INTERVAL"]
RISK_FREE_RATE = CONFIG["RISK_FREE_RATE"]
PRUNE_AFTER_MINUTES = CONFIG["PRUNE_AFTER_MINUTES"]
ENABLE_DELTA_LOGGING = CONFIG["ENABLE_DELTA_LOGGING"]
OPTION_CACHE_REFRESH_SECONDS = 60
OPTION_CACHE_STALE_GRACE_SECONDS = 10
OPTION_CACHE_TAIL_LIMIT = 300

EXPECTED_COLUMNS = ['open', 'high', 'low', 'close', 'ao_value', 'donchian_upper', 'donchian_lower', 'donchian_mid']


def _nearest_100(x: float) -> int:
    return int(((float(x) + 50.0) // 100.0) * 100)


def _ceil_100(x: float) -> int:
    value = float(x)
    return int(((value + 99.999999999) // 100.0) * 100)


def _select_ce_strike_near(spot_price: float, ce_strikes: List[int]) -> Optional[int]:
    if not ce_strikes:
        return None

    normalized = sorted({int(strike) for strike in ce_strikes})
    in_range = [strike for strike in normalized if abs(strike - float(spot_price)) <= 200.0]
    if in_range:
        return int(min(in_range, key=lambda strike: (abs(strike - float(spot_price)), strike)))

    lower = float(spot_price) - 200.0
    upper = float(spot_price) + 200.0

    def distance_to_band(strike: int) -> float:
        if strike < lower:
            return lower - strike
        if strike > upper:
            return strike - upper
        return 0.0

    return int(min(normalized, key=lambda strike: (distance_to_band(strike), abs(strike - float(spot_price)), strike)))


def build_nifty_strategy_required_contracts(
    spot_price: float,
    available_ce_strikes: Optional[List[int]] = None,
    position_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the exact strike/type contracts the NIFTY strategy expects to exist."""
    pe_strike = _nearest_100(float(spot_price))
    pe_plus_100 = pe_strike + 100
    pe_upper = _ceil_100(float(spot_price) + 200.0)
    ce_near = _select_ce_strike_near(float(spot_price), available_ce_strikes or [pe_strike])

    required: List[tuple[str, int]] = []
    normalized_position_type = (position_type or "").upper()
    if normalized_position_type == "A":
        if ce_near is not None:
            required.append(("CE", ce_near))
        required.extend([("PE", pe_upper), ("CE", pe_upper)])
    elif normalized_position_type == "B":
        required.extend([("PE", pe_strike), ("PE", pe_plus_100)])
    else:
        if ce_near is not None:
            required.append(("CE", ce_near))
        required.extend([
            ("PE", pe_strike),
            ("PE", pe_plus_100),
            ("PE", pe_upper),
            ("CE", pe_upper),
        ])

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for option_type, strike_price in required:
        key = (option_type, int(strike_price))
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"option_type": option_type, "strike_price": int(strike_price)})
    return deduped


def summarize_option_snapshot_coverage(
    option_rows: List[Dict[str, Any]],
    required_contracts: Optional[List[Dict[str, Any]]] = None,
    snapshot_timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Summarize whether a snapshot contains the exact contracts required by the strategy."""
    available_contracts = set()
    strikes: List[int] = []
    resolved_timestamp = snapshot_timestamp

    for row in option_rows or []:
        option_type = str(row.get("option_type") or "").upper()
        strike_price = row.get("strike_price")
        if not option_type or strike_price is None:
            continue
        try:
            strike_int = int(float(strike_price))
        except Exception:
            continue
        available_contracts.add((option_type, strike_int))
        strikes.append(strike_int)
        if resolved_timestamp is None and row.get("timestamp") is not None:
            resolved_timestamp = str(row.get("timestamp"))

    missing_required_contracts: List[Dict[str, Any]] = []
    for contract in required_contracts or []:
        option_type = str(contract.get("option_type") or "").upper()
        strike_price = contract.get("strike_price")
        if strike_price is None:
            continue
        key = (option_type, int(strike_price))
        if key not in available_contracts:
            missing_required_contracts.append({
                "option_type": option_type,
                "strike_price": int(strike_price),
            })

    return {
        "snapshot_timestamp": resolved_timestamp,
        "contract_count": len(option_rows or []),
        "available_contract_count": len(available_contracts),
        "min_available_strike": min(strikes) if strikes else None,
        "max_available_strike": max(strikes) if strikes else None,
        "required_contracts": list(required_contracts or []),
        "missing_required_contracts": missing_required_contracts,
        "complete": len(missing_required_contracts) == 0,
    }


def inspect_latest_option_snapshot_coverage(
    symbol: str = 'NIFTY50',
    required_contracts: Optional[List[Dict[str, Any]]] = None,
    db_path=DB_PATH,
) -> Dict[str, Any]:
    """Inspect the full latest stored option snapshot for required contract coverage."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MAX(timestamp)
        FROM option_data
        WHERE symbol = ?
    """, (symbol,))
    latest_timestamp = cursor.fetchone()[0]
    conn.close()

    latest_rows = fetch_latest_option_snapshot(symbol, db_path)
    return summarize_option_snapshot_coverage(
        latest_rows,
        required_contracts=required_contracts,
        snapshot_timestamp=latest_timestamp,
    )

def ensure_db_dir(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def fetch_last_non_null_skip_level(
    cursor: sqlite3.Cursor,
    table_name: str,
    symbol: str,
    column_name: str
) -> Optional[float]:
    cursor.execute(
        f"""
        SELECT {column_name}
        FROM {table_name}
        WHERE symbol = ? AND {column_name} IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (symbol,)
    )
    row = cursor.fetchone()
    return float(row[0]) if row and row[0] is not None else None

def ensure_market_data_unique_index(conn: sqlite3.Connection, interval: str) -> None:
    safe_interval = re.sub(r'[^0-9a-zA-Z]+', '_', str(interval))
    table_name = f"market_data_{safe_interval}"
    index_name = f"ux_market_data_{safe_interval}_symbol_timestamp"

    try:
        conn.execute(f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {index_name}
            ON {table_name} (symbol, timestamp);
        """)
    except sqlite3.IntegrityError as exc:
        raise sqlite3.IntegrityError(
            f"Cannot enforce unique candle storage on {table_name}. "
            "Remove existing duplicate rows before writing new live data."
        ) from exc


def store_market_data(df, symbol: str = 'NIFTY50', interval: str = '5m', vix_value: Optional[float] = None, db_path='db/trading_bot.db'):
#Stores OHLCV + AO + Donchian channel data into the market_data table.
    """
    Stores processed DataFrame into respective interval-based market_data table.
    Now includes VIX data alongside other market data.
    This version is robust to index duplicates, column-case differences, and
    merges last-N DB rows to compute rolling SL/SH correctly.
    """
    table_name = f"market_data_{interval}"
    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path, timeout=20)

    try:
        cursor = conn.cursor()

        # Normalize incoming column names to lowercase for consistency
        df = df.copy()
        df.columns = [col.lower() for col in df.columns]

        # --- Robust DB-aware merging + timezone normalization + SL/SH compute ---
        try:
            if symbol == "NIFTY50":
                initial_sl = fetch_last_non_null_skip_level(cursor, table_name, symbol, "SL")
                initial_sh = fetch_last_non_null_skip_level(cursor, table_name, symbol, "SH")

                # Fetch last 6 candles for SL/SH window context
                prev_df = fetch_market_data(symbol=symbol, limit=6, interval=interval, db_path=db_path)
                print(f"[DEBUG] prev_df fetched: rows={len(prev_df) if prev_df is not None and not prev_df.empty else 0}")

                if prev_df is None or prev_df.empty:
                    prev_df = pd.DataFrame()

                incoming = df.copy()
                incoming.columns = [c.lower() for c in incoming.columns]

                carry_cols = [col for col in ['sl', 'sh', 'SL', 'SH'] if col in incoming.columns]
                if carry_cols:
                    incoming = incoming.drop(columns=carry_cols)

                # STEP 1: Normalize timestamps as columns (not index)
                if not prev_df.empty:
                    prev_df = prev_df.reset_index()
                    if 'timestamp' not in prev_df.columns:
                        prev_df = prev_df.rename(columns={prev_df.columns[0]: 'timestamp'})
                    carry_cols = [col for col in ['sl', 'sh', 'SL', 'SH'] if col in prev_df.columns]
                    if carry_cols:
                        prev_df = prev_df.drop(columns=carry_cols)
                else:
                    prev_df = pd.DataFrame(columns=['timestamp'])

                incoming = incoming.reset_index()
                if 'timestamp' not in incoming.columns:
                    incoming = incoming.rename(columns={incoming.columns[0]: 'timestamp'})

                # Convert to datetime
                prev_df['timestamp'] = pd.to_datetime(prev_df['timestamp'], errors='coerce')
                incoming['timestamp'] = pd.to_datetime(incoming['timestamp'], errors='coerce')

                # STEP 2: Timezone normalization
                def normalize_timezone(df1, df2):
                    """Normalize timezones between two dataframes."""
                    if df1.empty or df2.empty:
                        return df1, df2

                    tz1 = df1['timestamp'].dt.tz if hasattr(df1['timestamp'].dt, 'tz') else None
                    tz2 = df2['timestamp'].dt.tz if hasattr(df2['timestamp'].dt, 'tz') else None

                    # Both naive - no action needed
                    if tz1 is None and tz2 is None:
                        return df1, df2

                    # One is aware, one is naive - localize the naive one
                    if tz1 is not None and tz2 is None:
                        df2['timestamp'] = df2['timestamp'].dt.tz_localize(tz1)
                    elif tz1 is None and tz2 is not None:
                        df1['timestamp'] = df1['timestamp'].dt.tz_localize(tz2)
                    # Both aware but different - convert to UTC
                    elif str(tz1) != str(tz2):
                        df1['timestamp'] = df1['timestamp'].dt.tz_convert('UTC')
                        df2['timestamp'] = df2['timestamp'].dt.tz_convert('UTC')

                    return df1, df2

                prev_df, incoming = normalize_timezone(prev_df, incoming)

                # STEP 3: Deduplicate using string representation
                prev_df['ts_str'] = prev_df['timestamp'].astype(str)
                incoming['ts_str'] = incoming['timestamp'].astype(str)

                prev_df = prev_df.drop_duplicates(subset=['ts_str'], keep='last').copy()
                incoming = incoming.drop_duplicates(subset=['ts_str'], keep='last').copy()
                # Remove duplicate columns (keep first occurrence)
                incoming = incoming.loc[:, ~incoming.columns.duplicated(keep='first')]
                prev_df = prev_df.loc[:, ~prev_df.columns.duplicated(keep='first')]

                combined = pd.concat([prev_df, incoming], axis=0, ignore_index=True)

                # Final deduplication on combined data
                combined = combined.drop_duplicates(subset=['ts_str'], keep='last').copy()
                combined = combined.drop(columns=['ts_str'])

                # Sort by timestamp
                combined = combined.sort_values('timestamp').reset_index(drop=True)

                print(f"[DEBUG] Combined rows after dedup: {len(combined)}")

                # STEP 5: Compute SL/SH using dedicated indicator functions
                combined_indexed = combined.set_index('timestamp')
                combined_indexed = nifty_skipping_low(
                    combined_indexed,
                    filter_pct=0.0000,
                    initial_sl=initial_sl
                )
                combined_indexed = nifty_skipping_high(
                    combined_indexed,
                    filter_pct=0.0000,
                    initial_sh=initial_sh
                )

                # Reset index back to column
                combined = combined_indexed.reset_index()

                # STEP 6: Extract only the incoming batch rows
                tail_len = len(incoming)
                result = combined.tail(tail_len).copy()
                result = result.reset_index(drop=True)

                print(f"[DEBUG] Final result rows: {len(result)}, SL/SH computed successfully")
                df = result.copy()

        except Exception as e:
            print(f"[ERROR] Error computing skipping low/high for {symbol}: {e}")
            import traceback
            traceback.print_exc()

        # Warn if any expected columns are missing
        missing_cols = [col for col in EXPECTED_COLUMNS if col not in df.columns]
        if missing_cols:
            print(f"[WARN] DataFrame is missing columns: {missing_cols}")

        # Case-insensitive safe_get_value
        def safe_get_value(row, column_name):
            try:
                candidates = []
                if column_name in row.index:
                    candidates.append(column_name)

                lower_name = str(column_name).lower()
                for col in row.index:
                    if col not in candidates and str(col).lower() == lower_name:
                        candidates.append(col)

                for col in candidates:
                    value = row[col]
                    if pd.notna(value):
                        return float(value)

                return None
            except Exception:
                return None

        rows = []
        for idx, row in df.iterrows():
            # Get timestamp from row (it's now a column, not index)
            timestamp_val = row.get('timestamp', idx)

            if hasattr(timestamp_val, 'strftime'):
                timestamp_str = timestamp_val.strftime("%Y-%m-%d %H:%M:%S")
            else:
                timestamp_str = str(timestamp_val)

            rows.append((
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
                safe_get_value(row, 'SL'),
                safe_get_value(row, 'SH')
            ))

        if not rows:
            print(f"No market data rows to store in {table_name}.")
            return 0

        ensure_market_data_unique_index(conn, interval)

        cursor.executemany(f"""
            INSERT INTO {table_name} (
                timestamp, symbol, open, high, low, close,
                ao_value, donchian_upper, donchian_lower, donchian_mid, SL, SH
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timestamp) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                ao_value = excluded.ao_value,
                donchian_upper = excluded.donchian_upper,
                donchian_lower = excluded.donchian_lower,
                donchian_mid = excluded.donchian_mid,
                SL = excluded.SL,
                SH = excluded.SH;
        """, rows)

        conn.commit()
        print(f"Market data stored successfully in {table_name}.")
        return len(rows)
    finally:
        conn.close()

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
def _parse_option_expiry_date(expiry_value: Any) -> Optional[date]:
    """Normalize option expiry values to a local date for storage filtering."""
    if isinstance(expiry_value, datetime):
        return expiry_value.date()
    if isinstance(expiry_value, date):
        return expiry_value
    if isinstance(expiry_value, str):
        expiry_text = expiry_value.strip()
        if not expiry_text:
            return None
        try:
            return date.fromisoformat(expiry_text[:10])
        except ValueError:
            return None
    return None


def store_high_accuracy_options_data(options_list: List[Dict], symbol: str, spot_price: float, db_path=DB_PATH):
    """
    Store high-accuracy options data for Black-Scholes delta calculations.
    Appends near-expiry option-chain snapshots for historical use.
    """
    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    max_expiry_date = date.today() + timedelta(days=14)
    skipped_invalid_expiry = 0
    skipped_far_expiry = 0

    for option in options_list:
        expiry_date = _parse_option_expiry_date(option.get('expiry_date'))
        if expiry_date is None:
            skipped_invalid_expiry += 1
            continue
        if expiry_date > max_expiry_date:
            skipped_far_expiry += 1
            continue

        try:
            cursor.execute("""
                INSERT INTO option_data (
                    timestamp, symbol, strike_price, option_type,
                    ltp, iv, expiry_date, spot_price, tradingsymbol, open_interest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp_str, symbol, option['strike_price'], option['option_type'],
                option['ltp'], option['iv'], expiry_date.isoformat(), spot_price,
                option['tradingsymbol'], option.get('open_interest', 0)
            ))
        except Exception as e:
            print(f"Error storing option {option.get('tradingsymbol', 'unknown')}: {e}")
            continue
    conn.commit()
    conn.close()
    if skipped_invalid_expiry:
        print(f"Skipped {skipped_invalid_expiry} option row(s) with invalid expiry_date.")
    if skipped_far_expiry:
        print(f"Skipped {skipped_far_expiry} option row(s) expiring after {max_expiry_date.isoformat()}.")
    print("Options data stored successfully.")

def _map_option_data_rows(rows) -> List[Dict]:
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
        LIMIT ?
    """, (symbol, OPTION_CACHE_TAIL_LIMIT))
    rows = cursor.fetchall()
    conn.close()
    return _map_option_data_rows(rows)

def fetch_latest_option_snapshot(symbol: str = 'NIFTY50', db_path=DB_PATH) -> List[Dict]:
    """
    Fetch the complete latest option_data snapshot for a symbol.

    Unlike fetch_cached_options_data(), this returns all rows for the most
    recent timestamp rather than a capped tail of records.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT MAX(timestamp)
        FROM option_data
        WHERE symbol = ?
    """, (symbol,))
    latest_timestamp = cursor.fetchone()[0]

    if not latest_timestamp:
        conn.close()
        return []

    cursor.execute("""
        SELECT *
        FROM option_data
        WHERE symbol = ? AND timestamp = ?
        ORDER BY strike_price, option_type, expiry_date
    """, (symbol, latest_timestamp))
    rows = cursor.fetchall()
    conn.close()
    return _map_option_data_rows(rows)


def _log_option_snapshot_coverage(context: str, coverage: Dict[str, Any]) -> None:
    missing_desc = ", ".join(
        f"{item['option_type']} {item['strike_price']}"
        for item in coverage.get("missing_required_contracts", [])
    ) or "none"
    print(
        f"[OPTION COVERAGE] {context}: "
        f"ts={coverage.get('snapshot_timestamp')} "
        f"contracts={coverage.get('contract_count')} "
        f"range={coverage.get('min_available_strike')}-{coverage.get('max_available_strike')} "
        f"missing={missing_desc}"
    )

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
        
        def build_required_contracts(option_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            ce_strikes = []
            for row in option_rows:
                if str(row.get("option_type") or "").upper() != "CE":
                    continue
                strike_price = row.get("strike_price")
                if strike_price is None:
                    continue
                try:
                    ce_strikes.append(int(float(strike_price)))
                except Exception:
                    continue
            return build_nifty_strategy_required_contracts(spot_price, ce_strikes)

        # Get cached options data
        cached_options = fetch_latest_option_snapshot(symbol, db_path)
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
                
        # Refresh cache if expired/empty OR too stale by age.
        # Keep the shared live path permissive so existing consumers continue to
        # see the same behavior as before; stricter completeness checks belong
        # in strategy/backtest-specific call paths.
        STALE_THRESHOLD_SECONDS = OPTION_CACHE_REFRESH_SECONDS + OPTION_CACHE_STALE_GRACE_SECONDS
        is_stale_by_age = newest_age is not None and newest_age > STALE_THRESHOLD_SECONDS
        if all_expired or not cached_options or is_stale_by_age:
            print("All cached options are expired or cache is empty. Fetching fresh option data...")
            from utils.vix_fetcher import fetch_live_option_chain
            fresh_options = fetch_live_option_chain(spot_price=spot_price)
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
            cached_options = fetch_latest_option_snapshot(symbol, db_path)
            
        if not cached_options:
            print("No options data available after refresh.")
            return None

        coverage = summarize_option_snapshot_coverage(
            cached_options,
            required_contracts=build_required_contracts(cached_options),
        )
            
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
            cursor.execute("SELECT MAX(timestamp) FROM delta_cache WHERE symbol = ?", (symbol,))
            latest_delta_timestamp = cursor.fetchone()[0]
            if latest_delta_timestamp and str(latest_delta_timestamp).startswith(timestamp_str[:16]):
                print(f"[DELTA CACHE] Snapshot already stored for {timestamp_str[:16]}; skipping duplicate write.")
            else:
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
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'coverage': coverage,
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
    Print the latest timestamp for 5m, 15m, and 1h data in the database.
    """
    import sqlite3
    for interval in ["5m", "15m", "1h"]:
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
        SELECT strike_price, option_type, delta, ltp, tradingsymbol, expiry_date
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
            'tradingsymbol': row[4],
            'expiry': row[5],
        })
    return options_data


def fetch_latest_option_price(
    symbol: str = 'NIFTY50',
    tradingsymbol: Optional[str] = None,
    strike_price: Optional[int] = None,
    option_type: Optional[str] = None,
    expiry: Optional[str] = None,
    db_path=DB_PATH,
) -> Optional[float]:
    """
    Fetch the latest known quote for a specific held option from delta_cache.

    The lookup prefers an exact tradingsymbol match and falls back to the
    option contract identifiers when needed.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        if tradingsymbol:
            cursor.execute("""
                SELECT ltp
                FROM delta_cache
                WHERE symbol = ? AND tradingsymbol = ? AND ltp IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 1
            """, (symbol, tradingsymbol))
            row = cursor.fetchone()
            if row and row[0] is not None:
                return float(row[0])

        if strike_price is None or not option_type:
            return None

        query = """
            SELECT ltp
            FROM delta_cache
            WHERE symbol = ? AND strike_price = ? AND option_type = ? AND ltp IS NOT NULL
        """
        params = [symbol, int(strike_price), str(option_type).upper()]

        if expiry:
            query += " AND expiry_date = ?"
            params.append(str(expiry))

        query += " ORDER BY timestamp DESC LIMIT 1"
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row and row[0] is not None:
            return float(row[0])

        return None
    finally:
        conn.close()

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
            sma_5_high REAL,
            sma_5_low REAL,
            sma_20 REAL,
            sma_50 REAL,
            sma_200 REAL,
            PRIMARY KEY (timestamp, symbol)
        );      
    """)

    # Backfill columns for older tables created before sma_5_high/sma_5_low existed.
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({tbl})")
    columns = [col[1] for col in cursor.fetchall()]

    required_columns = {
        'sma_20': 'REAL',
        'sma_50': 'REAL',
        'sma_200': 'REAL',
        'sma_5_high': 'REAL',
        'sma_5_low': 'REAL',
    }

    for column_name, column_type in required_columns.items():
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {column_name} {column_type}")

    conn.commit()
    
def store_sma_from_df(df: pd.DateOffset, symbol : str, interval: str, db_path= DB_PATH):
    """
    Compute SMA with high/low tracking on df and store into market_sma_<interval>.
    - df: DataFrame indexed by timestamp (index can be DatetimeIndex or strings). Must contain 'close', 'high', 'low'.
    - symbol: single symbol string (e.g., 'NIFTY50').
    - interval: '1m','5m','15m','1h'
    - db_path: path to sqlite DB file
    """
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]
    df_smas = compute_smas_with_high_low(df)

    #prepare rows for insertoin
    rows = []
    for ts, row in df_smas.iterrows():
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
        def safe(x): return float(x) if pd.notna(x) else None
        rows.append((
            ts_str, symbol,
            safe(row.get('sma_5')),
            safe(row.get('sma_5_high')),
            safe(row.get('sma_5_low')),
            safe(row.get('sma_20')),
            safe(row.get('sma_50')),
            safe(row.get('sma_200')),
        ))

    conn = sqlite3.connect(db_path, timeout=20)
    try:
        create_sma_table(conn, interval)
        tbl = sma_table_name(interval)
        cur = conn.cursor()
        #Insert or Replace so same code wont create duplicate
        cur.executemany(
            f"INSERT OR REPLACE INTO {tbl}(timestamp, symbol, sma_5, sma_5_high, sma_5_low, sma_20, sma_50, sma_200) Values (?, ?, ?, ?, ?, ?, ?, ?);",
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

def migrate_add_sma_high_low(db_path=DB_PATH):
    """
    ALTER existing market_sma_<interval> tables to add sma_5_high and sma_5_low columns.
    Safely handles cases where columns already exist.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    for interval in ['1m', '5m', '15m', '1h']:
        tbl = sma_table_name(interval)
        
        # Check if table exists first
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
        if not cursor.fetchone():
            print(f"[MIGRATION] Table {tbl} does not exist. Skipping.")
            continue
        
        # Check if columns already exist
        cursor.execute(f"PRAGMA table_info({tbl})")
        columns = [col[1] for col in cursor.fetchall()]
        
        try:
            required_columns = ['sma_20', 'sma_50', 'sma_200', 'sma_5_high', 'sma_5_low']

            for column_name in required_columns:
                if column_name not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN {column_name} REAL")
                    print(f"[MIGRATION] Added {column_name} to {tbl}")
                else:
                    print(f"[MIGRATION] {column_name} already exists in {tbl}")
        except sqlite3.OperationalError as e:
            print(f"[MIGRATION] Warning for {tbl}: {e}")
    
    conn.commit()
    conn.close()
    print("[MIGRATION] SMA high/low migration completed.")

def migrate_create_sma_tables(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        for interval in ["5m", "15m", "1h"]:
            create_sma_table(conn, interval)
        print("SMA tables (5m, 15m, 1h) verified or created.")
    finally:
        conn.close()

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
    migrate_create_sma_tables()
    print("Equity tables ensured.")
