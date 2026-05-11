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
OI_STRIKE_BAND = CONFIG.get("OI_STRIKE_BAND", 300)
OI_STRIKE_INTERVAL = CONFIG.get("OI_STRIKE_INTERVAL", 50)
OI_EXPIRY_MODE = CONFIG.get("OI_EXPIRY_MODE", "nearest_only")
OI_FETCH_INTERVAL_SECONDS = CONFIG.get("OI_FETCH_INTERVAL_SECONDS", 60)

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


def _canonical_option_order_by(include_timestamp: bool = True) -> str:
    order_fields = []
    if include_timestamp:
        order_fields.append("timestamp ASC")
    order_fields.extend([
        "expiry_date ASC",
        "strike_price ASC",
        (
            "CASE "
            "WHEN UPPER(option_type) = 'CE' THEN 0 "
            "WHEN UPPER(option_type) = 'PE' THEN 1 "
            "ELSE 2 END ASC"
        ),
        "tradingsymbol ASC",
    ])
    return ", ".join(order_fields)


def _sort_option_like_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy sorted by the canonical timestamp/expiry/strike/type order."""
    if df is None or df.empty:
        return df

    working = df.copy()
    temp_columns: List[str] = []
    sort_columns: List[str] = []

    if "timestamp" in working.columns:
        sort_columns.append("timestamp")
    if "expiry_date" in working.columns:
        sort_columns.append("expiry_date")
    if "strike_price" in working.columns:
        strike_sort_column = "__strike_price_sort"
        working[strike_sort_column] = pd.to_numeric(working["strike_price"], errors="coerce")
        sort_columns.append(strike_sort_column)
        temp_columns.append(strike_sort_column)
    if "option_type" in working.columns:
        option_type_sort_column = "__option_type_sort"
        working[option_type_sort_column] = (
            working["option_type"]
            .astype(str)
            .str.upper()
            .map({"CE": 0, "PE": 1})
            .fillna(2)
        )
        sort_columns.append(option_type_sort_column)
        temp_columns.append(option_type_sort_column)
    if "tradingsymbol" in working.columns:
        sort_columns.append("tradingsymbol")

    if sort_columns:
        working = working.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)
    if temp_columns:
        working = working.drop(columns=temp_columns)
    return working


def _normalize_required_contracts(required_contracts: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen = set()

    for contract in required_contracts or []:
        normalized_contract: Dict[str, Any] = {}

        tradingsymbol = contract.get("tradingsymbol")
        if tradingsymbol:
            normalized_contract["tradingsymbol"] = str(tradingsymbol).upper()

        option_type = contract.get("option_type")
        if option_type:
            normalized_contract["option_type"] = str(option_type).upper()

        strike_price = contract.get("strike_price")
        if strike_price is not None:
            try:
                normalized_contract["strike_price"] = int(float(strike_price))
            except Exception:
                pass

        expiry = contract.get("expiry") or contract.get("expiry_date")
        if expiry:
            normalized_contract["expiry_date"] = str(expiry)[:10]

        if not normalized_contract:
            continue

        key = (
            normalized_contract.get("tradingsymbol"),
            normalized_contract.get("option_type"),
            normalized_contract.get("strike_price"),
            normalized_contract.get("expiry_date"),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_contract)

    return normalized


def _merge_required_contracts(*contract_sets: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()

    for contract_set in contract_sets:
        for contract in _normalize_required_contracts(contract_set):
            key = (
                contract.get("tradingsymbol"),
                contract.get("option_type"),
                contract.get("strike_price"),
                contract.get("expiry_date"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(contract)

    return merged


def _row_matches_required_contract(row: Dict[str, Any], contract: Dict[str, Any]) -> bool:
    row_tradingsymbol = str(row.get("tradingsymbol") or "").upper()
    contract_tradingsymbol = contract.get("tradingsymbol")
    if contract_tradingsymbol:
        return row_tradingsymbol == contract_tradingsymbol

    row_option_type = str(row.get("option_type") or "").upper()
    row_expiry = str(row.get("expiry_date") or row.get("expiry") or "")[:10]
    row_strike = row.get("strike_price")
    try:
        row_strike_int = int(float(row_strike)) if row_strike is not None else None
    except Exception:
        row_strike_int = None

    if contract.get("option_type") and row_option_type != contract.get("option_type"):
        return False
    if contract.get("strike_price") is not None and row_strike_int != contract.get("strike_price"):
        return False
    if contract.get("expiry_date") and row_expiry != contract.get("expiry_date"):
        return False
    return True


_CONTRACT_LOOKUP_FIELD_GROUPS = (
    ("option_type",),
    ("strike_price",),
    ("expiry_date",),
    ("option_type", "strike_price"),
    ("option_type", "expiry_date"),
    ("strike_price", "expiry_date"),
    ("option_type", "strike_price", "expiry_date"),
)


def _normalize_option_row_contract_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    strike_price = row.get("strike_price")
    try:
        normalized_strike = int(float(strike_price)) if strike_price is not None else None
    except Exception:
        normalized_strike = None

    return {
        "tradingsymbol": str(row.get("tradingsymbol") or "").upper(),
        "option_type": str(row.get("option_type") or "").upper(),
        "strike_price": normalized_strike,
        "expiry_date": str(row.get("expiry_date") or row.get("expiry") or "")[:10],
    }


def _build_required_contract_lookup(option_rows: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    tradingsymbol_rows: Dict[str, Dict[str, Any]] = {}
    field_group_rows: Dict[tuple[str, ...], Dict[tuple[Any, ...], Dict[str, Any]]] = {
        field_group: {}
        for field_group in _CONTRACT_LOOKUP_FIELD_GROUPS
    }

    for row in option_rows or []:
        normalized_row = _normalize_option_row_contract_fields(row)
        tradingsymbol = normalized_row["tradingsymbol"]
        if tradingsymbol and tradingsymbol not in tradingsymbol_rows:
            tradingsymbol_rows[tradingsymbol] = row

        for field_group, grouped_rows in field_group_rows.items():
            key = tuple(normalized_row[field_name] for field_name in field_group)
            if key not in grouped_rows:
                grouped_rows[key] = row

    return {
        "tradingsymbol_rows": tradingsymbol_rows,
        "field_group_rows": field_group_rows,
    }


def _get_required_contract_lookup_key(contract: Dict[str, Any]) -> tuple[tuple[str, ...], Optional[tuple[Any, ...]]]:
    tradingsymbol = contract.get("tradingsymbol")
    if tradingsymbol:
        return ("tradingsymbol",), (str(tradingsymbol).upper(),)

    field_names = []
    field_values = []

    option_type = contract.get("option_type")
    if option_type:
        field_names.append("option_type")
        field_values.append(option_type)

    strike_price = contract.get("strike_price")
    if strike_price is not None:
        field_names.append("strike_price")
        field_values.append(strike_price)

    expiry_date = contract.get("expiry_date")
    if expiry_date:
        field_names.append("expiry_date")
        field_values.append(expiry_date)

    if not field_names:
        return tuple(), None

    return tuple(field_names), tuple(field_values)


def _find_matching_required_contract_row(
    lookup: Dict[str, Any],
    contract: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    field_names, key = _get_required_contract_lookup_key(contract)
    if not key:
        return None
    if field_names == ("tradingsymbol",):
        return lookup["tradingsymbol_rows"].get(key[0])
    return lookup["field_group_rows"].get(field_names, {}).get(key)


def _build_required_contract_match_sets(required_contracts: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    normalized_required = _normalize_required_contracts(required_contracts)
    match_sets: Dict[str, Any] = {
        "tradingsymbols": set(),
        "field_groups": {
            field_group: set()
            for field_group in _CONTRACT_LOOKUP_FIELD_GROUPS
        },
    }

    for contract in normalized_required:
        tradingsymbol = contract.get("tradingsymbol")
        if tradingsymbol:
            match_sets["tradingsymbols"].add(tradingsymbol)
            continue

        field_names, key = _get_required_contract_lookup_key(contract)
        if field_names and key:
            match_sets["field_groups"][field_names].add(key)

    return match_sets


def _row_is_explicitly_required(row: Dict[str, Any], required_match_sets: Dict[str, Any]) -> bool:
    normalized_row = _normalize_option_row_contract_fields(row)
    tradingsymbol = normalized_row["tradingsymbol"]
    if tradingsymbol and tradingsymbol in required_match_sets["tradingsymbols"]:
        return True

    for field_group, values in required_match_sets["field_groups"].items():
        if tuple(normalized_row[field_name] for field_name in field_group) in values:
            return True
    return False


def find_missing_required_contracts(
    option_rows: List[Dict[str, Any]],
    required_contracts: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    missing: List[Dict[str, Any]] = []
    normalized_required = _normalize_required_contracts(required_contracts)
    if not normalized_required:
        return missing

    lookup = _build_required_contract_lookup(option_rows)
    for contract in normalized_required:
        if _find_matching_required_contract_row(lookup, contract) is not None:
            continue
        missing.append(contract)

    return missing


def _build_exact_contract_delta_rows(
    option_rows: List[Dict[str, Any]],
    required_contracts: Optional[List[Dict[str, Any]]],
    spot_price: float,
) -> List[Dict[str, Any]]:
    exact_rows: List[Dict[str, Any]] = []
    iv_calc = ProductionIVCalculator()
    normalized_required = _normalize_required_contracts(required_contracts)
    if not normalized_required:
        return exact_rows
    lookup = _build_required_contract_lookup(option_rows)
    expiry_time_cache: Dict[str, Optional[float]] = {}

    for contract in normalized_required:
        matched_row = _find_matching_required_contract_row(lookup, contract)
        if not matched_row:
            continue

        strike_price = matched_row.get("strike_price")
        option_type = str(matched_row.get("option_type") or "").upper()
        expiry_date = matched_row.get("expiry_date")
        iv_value = matched_row.get("iv")
        tradingsymbol = matched_row.get("tradingsymbol")
        ltp_value = matched_row.get("ltp")

        if strike_price is None or not option_type or not expiry_date or iv_value is None or tradingsymbol is None:
            continue

        try:
            strike_int = int(float(strike_price))
            iv_float = float(iv_value)
            expiry_key = str(expiry_date)
            if expiry_key not in expiry_time_cache:
                expiry_time_cache[expiry_key] = iv_calc.calculate_time_to_expiry(expiry_key)
            T = expiry_time_cache[expiry_key]
            if T is None or T <= 0:
                continue
            delta_value = iv_calc.calculate_delta(
                spot=float(spot_price),
                strike=strike_int,
                T=T,
                iv=iv_float,
                option_type=option_type,
            )
        except Exception:
            continue

        exact_rows.append({
            "strike_price": strike_int,
            "option_type": option_type,
            "delta": delta_value,
            "expiry_date": str(expiry_date),
            "spot_price": float(spot_price),
            "ltp": ltp_value,
            "tradingsymbol": str(tradingsymbol),
        })

    return exact_rows

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


def _safe_db_timestamp(value) -> Optional[str]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _safe_db_date(value) -> Optional[str]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return int(float(value))
    except Exception:
        return None


def _first_non_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _ensure_open_interest_schema(conn: sqlite3.Connection) -> None:
    from utils.db_setup import create_open_interest_table

    create_open_interest_table(conn)


def _ensure_open_interest_5m_schema(conn: sqlite3.Connection) -> None:
    from utils.db_setup import create_open_interest_5m_table

    create_open_interest_5m_table(conn)


def _floor_to_5m_bucket(timestamp_value: Any) -> Optional[pd.Timestamp]:
    timestamp = pd.Timestamp(timestamp_value) if timestamp_value is not None else None
    if timestamp is None or pd.isna(timestamp):
        return None
    return timestamp.floor("5min")


def rebuild_open_interest_5m_for_day(
    symbol: str = "NIFTY50",
    trading_day: Any = None,
    db_path=DB_PATH,
) -> int:
    """Rebuild derived 5-minute OI/VWAP rows for one symbol and trading day."""
    if trading_day is None:
        trading_day = datetime.now().date()
    if isinstance(trading_day, datetime):
        trading_day = trading_day.date()
    elif isinstance(trading_day, str):
        trading_day = pd.Timestamp(trading_day).date()

    day_start = datetime.combine(trading_day, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    conn = sqlite3.connect(db_path, timeout=20)
    try:
        _ensure_open_interest_schema(conn)
        _ensure_open_interest_5m_schema(conn)
        raw_df = pd.read_sql_query(
            """
            SELECT *
            FROM option_open_interest
            WHERE symbol = ? AND timestamp >= ? AND timestamp < ?
            ORDER BY timestamp, expiry_date, strike_price, option_type
            """,
            conn,
            params=(
                symbol,
                _safe_db_timestamp(day_start),
                _safe_db_timestamp(day_end),
            ),
        )

        conn.execute(
            """
            DELETE FROM option_open_interest_5m
            WHERE symbol = ? AND timestamp >= ? AND timestamp < ?
            """,
            (symbol, _safe_db_timestamp(day_start), _safe_db_timestamp(day_end)),
        )

        if raw_df.empty:
            conn.commit()
            return 0

        raw_df["timestamp"] = pd.to_datetime(raw_df["timestamp"], errors="coerce")
        if "last_trade_time" in raw_df.columns:
            raw_df["last_trade_time"] = pd.to_datetime(raw_df["last_trade_time"], errors="coerce")
        raw_df = raw_df.dropna(subset=["timestamp"]).copy()
        if raw_df.empty:
            conn.commit()
            return 0

        raw_df["bucket_timestamp"] = raw_df["timestamp"].dt.floor("5min")
        contract_columns = [
            "symbol",
            "exchange",
            "tradingsymbol",
            "instrument_token",
            "expiry_date",
            "strike_price",
            "option_type",
        ]
        derived_rows: List[tuple] = []

        grouped = raw_df.groupby(contract_columns, dropna=False, sort=False)
        for contract_key, contract_df in grouped:
            contract_df = contract_df.sort_values("timestamp").reset_index(drop=True)
            bucket_state: Dict[pd.Timestamp, Dict[str, Any]] = {}
            previous_volume = None
            previous_vwap = None

            for _, row in contract_df.iterrows():
                bucket_ts = row["bucket_timestamp"]
                current_volume = _safe_int(row.get("volume"))
                delta_volume = 0
                if current_volume is not None:
                    if previous_volume is not None and current_volume >= previous_volume:
                        delta_volume = current_volume - previous_volume
                    previous_volume = current_volume

                state = bucket_state.setdefault(bucket_ts, {
                    "row": row,
                    "bucket_volume": 0,
                    "traded_value": 0.0,
                    "source_start_timestamp": row["timestamp"],
                    "source_end_timestamp": row["timestamp"],
                    "source_snapshot_count": 0,
                })
                state["row"] = row
                state["bucket_volume"] += int(max(delta_volume, 0))
                if delta_volume > 0 and row.get("last_price") is not None and not pd.isna(row.get("last_price")):
                    state["traded_value"] += float(row["last_price"]) * float(delta_volume)
                state["source_start_timestamp"] = min(state["source_start_timestamp"], row["timestamp"])
                state["source_end_timestamp"] = max(state["source_end_timestamp"], row["timestamp"])
                state["source_snapshot_count"] += 1

            bucket_timestamps = pd.date_range(
                start=contract_df["bucket_timestamp"].min(),
                end=contract_df["bucket_timestamp"].max(),
                freq="5min",
            )

            for bucket_ts in bucket_timestamps:
                state = bucket_state.get(bucket_ts)
                if state is None:
                    template_row = contract_df[contract_df["bucket_timestamp"] < bucket_ts].iloc[-1]
                    bucket_volume = 0
                    vwap = previous_vwap
                    is_carry_forward = 1 if previous_vwap is not None else 0
                    source_start_timestamp = None
                    source_end_timestamp = None
                    source_snapshot_count = 0
                else:
                    template_row = state["row"]
                    bucket_volume = int(state["bucket_volume"])
                    if bucket_volume > 0:
                        vwap = float(state["traded_value"]) / float(bucket_volume)
                        previous_vwap = vwap
                        is_carry_forward = 0
                    else:
                        vwap = previous_vwap
                        is_carry_forward = 1 if previous_vwap is not None else 0
                    source_start_timestamp = state["source_start_timestamp"]
                    source_end_timestamp = state["source_end_timestamp"]
                    source_snapshot_count = state["source_snapshot_count"]

                derived_rows.append((
                    _safe_db_timestamp(bucket_ts),
                    str(template_row.get("symbol") or symbol),
                    str(template_row.get("exchange") or "NFO"),
                    str(template_row.get("tradingsymbol") or ""),
                    _safe_int(template_row.get("instrument_token")),
                    _safe_db_date(template_row.get("expiry_date")),
                    _safe_float(template_row.get("strike_price")),
                    str(template_row.get("option_type") or "").upper(),
                    _safe_float(template_row.get("spot_price")),
                    _safe_float(template_row.get("last_price")),
                    _safe_int(template_row.get("open_interest")),
                    _safe_int(template_row.get("oi_day_high")),
                    _safe_int(template_row.get("oi_day_low")),
                    _safe_float(vwap),
                    bucket_volume,
                    int(is_carry_forward),
                    _safe_db_timestamp(source_start_timestamp),
                    _safe_db_timestamp(source_end_timestamp),
                    _safe_int(source_snapshot_count),
                    _safe_db_timestamp(template_row.get("last_trade_time")),
                ))

        if not derived_rows:
            conn.commit()
            return 0

        conn.executemany(
            """
            INSERT INTO option_open_interest_5m (
                timestamp, symbol, exchange, tradingsymbol, instrument_token,
                expiry_date, strike_price, option_type, spot_price, last_price,
                open_interest, oi_day_high, oi_day_low, vwap, bucket_volume,
                is_carry_forward, source_start_timestamp, source_end_timestamp,
                source_snapshot_count, last_trade_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(timestamp, symbol, tradingsymbol) DO UPDATE SET
                exchange = excluded.exchange,
                instrument_token = excluded.instrument_token,
                expiry_date = excluded.expiry_date,
                strike_price = excluded.strike_price,
                option_type = excluded.option_type,
                spot_price = excluded.spot_price,
                last_price = excluded.last_price,
                open_interest = excluded.open_interest,
                oi_day_high = excluded.oi_day_high,
                oi_day_low = excluded.oi_day_low,
                vwap = excluded.vwap,
                bucket_volume = excluded.bucket_volume,
                is_carry_forward = excluded.is_carry_forward,
                source_start_timestamp = excluded.source_start_timestamp,
                source_end_timestamp = excluded.source_end_timestamp,
                source_snapshot_count = excluded.source_snapshot_count,
                last_trade_time = excluded.last_trade_time;
            """,
            derived_rows,
        )
        conn.commit()
        return len(derived_rows)
    finally:
        conn.close()


def rebuild_open_interest_5m_for_rows(
    rows: List[Dict[str, Any]],
    db_path=DB_PATH,
) -> int:
    """Rebuild derived 5-minute OI/VWAP rows for trading days present in raw rows."""
    rebuild_targets = []
    for row in rows or []:
        timestamp_value = row.get("timestamp")
        if timestamp_value is None:
            continue
        timestamp = pd.Timestamp(timestamp_value)
        if pd.isna(timestamp):
            continue
        rebuild_targets.append((str(row.get("symbol") or "NIFTY50"), timestamp.date()))

    total_rows = 0
    for symbol, trading_day in sorted(set(rebuild_targets)):
        total_rows += rebuild_open_interest_5m_for_day(symbol=symbol, trading_day=trading_day, db_path=db_path)
    return total_rows


def store_open_interest_snapshot(rows: List[Dict[str, Any]], db_path=DB_PATH) -> int:
    """Store append-only NIFTY option open-interest snapshot rows."""
    if not rows:
        return 0

    ensure_db_dir(db_path)
    conn = sqlite3.connect(db_path, timeout=20)
    try:
        _ensure_open_interest_schema(conn)
        cursor = conn.cursor()
        prepared_rows = []

        for row in rows:
            timestamp_str = _safe_db_timestamp(row.get("timestamp") or datetime.now())
            tradingsymbol = row.get("tradingsymbol")
            expiry_date = _safe_db_date(row.get("expiry_date") or row.get("expiry"))
            option_type = str(row.get("option_type") or row.get("instrument_type") or "").upper()
            strike_price = _safe_float(row.get("strike_price") or row.get("strike"))

            if not timestamp_str or not tradingsymbol or not expiry_date or not option_type or strike_price is None:
                continue

            prepared_rows.append((
                timestamp_str,
                str(row.get("symbol") or "NIFTY50"),
                str(row.get("exchange") or "NFO"),
                str(tradingsymbol),
                _safe_int(row.get("instrument_token")),
                expiry_date,
                strike_price,
                option_type,
                _safe_float(row.get("spot_price")),
                _safe_float(_first_non_none(row.get("last_price"), row.get("ltp"))),
                _safe_int(_first_non_none(row.get("open_interest"), row.get("oi"))),
                _safe_int(row.get("oi_day_high")),
                _safe_int(row.get("oi_day_low")),
                _safe_float(_first_non_none(row.get("vwap"), row.get("average_price"), row.get("averagePrice"))),
                _safe_db_timestamp(row.get("last_trade_time")),
                _safe_int(row.get("volume")),
            ))

        if not prepared_rows:
            return 0

        cursor.executemany("""
            INSERT INTO option_open_interest (
                timestamp, symbol, exchange, tradingsymbol, instrument_token,
                expiry_date, strike_price, option_type, spot_price, last_price,
                open_interest, oi_day_high, oi_day_low, vwap,
                last_trade_time, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(timestamp, symbol, tradingsymbol) DO UPDATE SET
                exchange = excluded.exchange,
                instrument_token = excluded.instrument_token,
                expiry_date = excluded.expiry_date,
                strike_price = excluded.strike_price,
                option_type = excluded.option_type,
                spot_price = excluded.spot_price,
                last_price = excluded.last_price,
                open_interest = excluded.open_interest,
                oi_day_high = excluded.oi_day_high,
                oi_day_low = excluded.oi_day_low,
                vwap = excluded.vwap,
                last_trade_time = excluded.last_trade_time,
                volume = excluded.volume;
        """, prepared_rows)
        conn.commit()
        return len(prepared_rows)
    finally:
        conn.close()


def _open_interest_rows_to_dicts(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def fetch_latest_open_interest_snapshot(
    symbol: str = "NIFTY50",
    current_time=None,
    db_path=DB_PATH,
) -> List[Dict[str, Any]]:
    """Return all OI rows for the latest snapshot at or before current_time."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_open_interest_schema(conn)
        cursor = conn.cursor()
        try:
            if current_time is None:
                cursor.execute(
                    "SELECT MAX(timestamp) FROM option_open_interest WHERE symbol = ?",
                    (symbol,),
                )
            else:
                cursor.execute(
                    "SELECT MAX(timestamp) FROM option_open_interest WHERE symbol = ? AND timestamp <= ?",
                    (symbol, _safe_db_timestamp(current_time)),
                )
            latest_timestamp = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            return []

        if not latest_timestamp:
            return []

        cursor.execute("""
            SELECT *
            FROM option_open_interest
            WHERE symbol = ? AND timestamp = ?
            ORDER BY """ + _canonical_option_order_by(include_timestamp=True) + """
        """, (symbol, latest_timestamp))
        return _open_interest_rows_to_dicts(cursor.fetchall())
    finally:
        conn.close()

def fetch_open_interest_data(
    symbol: str = "NIFTY50",
    start=None,
    end=None,
    db_path=DB_PATH,
) -> pd.DataFrame:
    """Fetch persisted option open-interest rows as a timestamp-indexed DataFrame."""
    conn = sqlite3.connect(db_path)
    try:
        _ensure_open_interest_schema(conn)
        query = "SELECT * FROM option_open_interest WHERE symbol = ?"
        params = [symbol]

        if start:
            query += " AND timestamp >= ?"
            params.append(_safe_db_timestamp(start))
        if end:
            query += " AND timestamp <= ?"
            params.append(_safe_db_timestamp(end))

        query += f" ORDER BY {_canonical_option_order_by(include_timestamp=True)}"
        try:
            df = pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp", "last_trade_time"])
        except (sqlite3.OperationalError, ValueError):
            return pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        return df
    df = _sort_option_like_dataframe(df)
    df.set_index("timestamp", inplace=True)
    return df


def fetch_latest_open_interest_5m_snapshot(
    symbol: str = "NIFTY50",
    current_time=None,
    db_path=DB_PATH,
) -> List[Dict[str, Any]]:
    """Return all derived 5-minute OI rows for the latest bucket at or before current_time."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_open_interest_5m_schema(conn)
        cursor = conn.cursor()
        if current_time is None:
            cursor.execute(
                "SELECT MAX(timestamp) FROM option_open_interest_5m WHERE symbol = ?",
                (symbol,),
            )
        else:
            current_bucket = _floor_to_5m_bucket(current_time)
            cursor.execute(
                "SELECT MAX(timestamp) FROM option_open_interest_5m WHERE symbol = ? AND timestamp <= ?",
                (symbol, _safe_db_timestamp(current_bucket)),
            )
        latest_timestamp = cursor.fetchone()[0]
        if not latest_timestamp:
            return []

        cursor.execute(
            """
            SELECT *
            FROM option_open_interest_5m
            WHERE symbol = ? AND timestamp = ?
            ORDER BY """ + _canonical_option_order_by(include_timestamp=True) + """
            """,
            (symbol, latest_timestamp),
        )
        return _open_interest_rows_to_dicts(cursor.fetchall())
    finally:
        conn.close()


def fetch_open_interest_5m_data(
    symbol: str = "NIFTY50",
    start=None,
    end=None,
    db_path=DB_PATH,
) -> pd.DataFrame:
    """Fetch derived 5-minute OI/VWAP rows as a timestamp-indexed DataFrame."""
    conn = sqlite3.connect(db_path)
    try:
        _ensure_open_interest_5m_schema(conn)
        query = "SELECT * FROM option_open_interest_5m WHERE symbol = ?"
        params = [symbol]

        if start:
            query += " AND timestamp >= ?"
            params.append(_safe_db_timestamp(_floor_to_5m_bucket(start) or start))
        if end:
            query += " AND timestamp <= ?"
            params.append(_safe_db_timestamp(_floor_to_5m_bucket(end) or end))

        query += f" ORDER BY {_canonical_option_order_by(include_timestamp=True)}"
        try:
            df = pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp", "last_trade_time", "source_start_timestamp", "source_end_timestamp"])
        except (sqlite3.OperationalError, ValueError):
            return pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        return df
    df = _sort_option_like_dataframe(df)
    df.set_index("timestamp", inplace=True)
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


def store_high_accuracy_options_data(
    options_list: List[Dict],
    symbol: str,
    spot_price: float,
    db_path=DB_PATH,
    required_contracts: Optional[List[Dict[str, Any]]] = None,
):
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
    required_match_sets = _build_required_contract_match_sets(required_contracts)
    prepared_rows: List[tuple[str, tuple[Any, ...]]] = []

    for option in options_list:
        expiry_date = _parse_option_expiry_date(option.get('expiry_date'))
        if expiry_date is None:
            skipped_invalid_expiry += 1
            continue
        option_identity = {
            "tradingsymbol": option.get("tradingsymbol"),
            "option_type": option.get("option_type"),
            "strike_price": option.get("strike_price"),
            "expiry_date": expiry_date.isoformat(),
        }
        is_explicitly_required = _row_is_explicitly_required(option_identity, required_match_sets)
        if expiry_date > max_expiry_date and not is_explicitly_required:
            skipped_far_expiry += 1
            continue

        try:
            prepared_rows.append((
                str(option.get('tradingsymbol', 'unknown')),
                (
                    timestamp_str, symbol, option['strike_price'], option['option_type'],
                    option['ltp'], option['iv'], expiry_date.isoformat(), spot_price,
                    option['tradingsymbol'], option.get('open_interest', 0)
                ),
            ))
        except Exception as e:
            print(f"Error storing option {option.get('tradingsymbol', 'unknown')}: {e}")
            continue

    insert_sql = """
        INSERT INTO option_data (
            timestamp, symbol, strike_price, option_type,
            ltp, iv, expiry_date, spot_price, tradingsymbol, open_interest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    try:
        if prepared_rows:
            try:
                conn.execute("BEGIN")
                cursor.executemany(insert_sql, [row_values for _, row_values in prepared_rows])
                conn.commit()
            except Exception:
                conn.rollback()
                for tradingsymbol, row_values in prepared_rows:
                    try:
                        cursor.execute(insert_sql, row_values)
                    except Exception as e:
                        print(f"Error storing option {tradingsymbol}: {e}")
                        continue
                conn.commit()
    finally:
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
        LIMIT 100
    """, (symbol,))
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
        ORDER BY """ + _canonical_option_order_by(include_timestamp=True) + """
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
    db_path=DB_PATH,
    required_contracts: Optional[List[Dict[str, Any]]] = None,
    force_refresh: bool = False,
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
                
        strategy_required_contracts = build_required_contracts(cached_options) if cached_options else []
        requested_required_contracts = _normalize_required_contracts(required_contracts)
        combined_required_contracts = _merge_required_contracts(
            strategy_required_contracts,
            requested_required_contracts,
        )
        missing_requested_contracts = find_missing_required_contracts(
            cached_options,
            requested_required_contracts,
        )

        # Refresh cache if expired/empty OR too stale by age.
        # Keep the shared live path permissive so existing consumers continue to
        # see the same behavior as before; stricter completeness checks belong
        # in strategy/backtest-specific call paths.
        #STALE_THRESHOLD_SECONDS = OPTION_CACHE_REFRESH_SECONDS + OPTION_CACHE_STALE_GRACE_SECONDS
        STALE_THRESHOLD_SECONDS = 60  # 1 minute freshness threshold for option data
        is_stale_by_age = newest_age is not None and newest_age > STALE_THRESHOLD_SECONDS
        if all_expired or not cached_options or is_stale_by_age or force_refresh or missing_requested_contracts:
            print("All cached options are expired or cache is empty. Fetching fresh option data...")
            from utils.vix_fetcher import fetch_live_option_chain
            fresh_options = fetch_live_option_chain(
                spot_price=spot_price,
                required_contracts=combined_required_contracts or None,
            )
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
            store_high_accuracy_options_data(
                formatted_options,
                symbol,
                spot_price,
                db_path,
                required_contracts=combined_required_contracts or None,
            )
            cached_options = fetch_latest_option_snapshot(symbol, db_path)
            
        if not cached_options:
            print("No options data available after refresh.")
            return None

        strategy_required_contracts = build_required_contracts(cached_options)
        combined_required_contracts = _merge_required_contracts(
            strategy_required_contracts,
            requested_required_contracts,
        )

        coverage = summarize_option_snapshot_coverage(
            cached_options,
            required_contracts=strategy_required_contracts,
        )
        missing_requested_contracts = find_missing_required_contracts(
            cached_options,
            requested_required_contracts,
        )
            
        if strike_band is None:
            strikes = [opt['strike_price'] for opt in cached_options]
            atm_strike = min(strikes, key=lambda x: abs(x - spot_price))
            band = STRIKE_BAND
            interval = STRIKE_INTERVAL
            strike_band = [atm_strike + i*interval for i in range(-band//interval, band//interval+1)]
        else:
            strike_band = list(strike_band)

        required_strikes = set(int(float(strike)) for strike in strike_band)
        for contract in combined_required_contracts:
            strike_price = contract.get("strike_price")
            if strike_price is not None:
                required_strikes.add(int(strike_price))
                continue
            tradingsymbol = contract.get("tradingsymbol")
            if not tradingsymbol:
                continue
            matched_row = next(
                (row for row in cached_options if str(row.get("tradingsymbol") or "").upper() == tradingsymbol),
                None,
            )
            if matched_row and matched_row.get("strike_price") is not None:
                required_strikes.add(int(float(matched_row["strike_price"])))
        strike_band = sorted(required_strikes)
            
        # Calculate deltas for the strike band
        delta_results = calculate_delta_for_strike_band(
            spot_price=spot_price,
            strike_band=strike_band,
            option_data=cached_options
        )
        exact_contract_rows = _build_exact_contract_delta_rows(
            cached_options,
            requested_required_contracts,
            spot_price,
        )
        
        # Store delta results in delta_cache table if logging enabled
        if ENABLE_DELTA_LOGGING:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("SELECT MAX(timestamp) FROM delta_cache WHERE symbol = ?", (symbol,))
            latest_delta_timestamp = cursor.fetchone()[0]
            if latest_delta_timestamp and str(latest_delta_timestamp).startswith(timestamp_str[:16]) and not force_refresh:
                print(f"[DELTA CACHE] Snapshot already stored for {timestamp_str[:16]}; skipping duplicate write.")
            else:
                stored_exact_keys = set()
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
                            stored_exact_keys.add((
                                str(selected_tradingsymbol or "").upper(),
                                str(selected_expiry or ""),
                                int(strike),
                                str(option_type).upper(),
                            ))
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
                for exact_row in exact_contract_rows:
                    exact_key = (
                        str(exact_row["tradingsymbol"]).upper(),
                        str(exact_row["expiry_date"]),
                        int(exact_row["strike_price"]),
                        str(exact_row["option_type"]).upper(),
                    )
                    if exact_key in stored_exact_keys:
                        continue
                    cursor.execute("""
                        INSERT INTO delta_cache (
                            timestamp, strike_price, option_type, delta,
                            expiry_date, spot_price, symbol, ltp, tradingsymbol
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        timestamp_str,
                        exact_row["strike_price"],
                        exact_row["option_type"],
                        float(exact_row["delta"]) * 100,
                        exact_row["expiry_date"],
                        exact_row["spot_price"],
                        symbol,
                        exact_row["ltp"],
                        exact_row["tradingsymbol"],
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
            'missing_requested_contracts': missing_requested_contracts,
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

def fetch_latest_delta_snapshot(
    symbol: str = 'NIFTY50',
    db_path=DB_PATH,
    required_contracts: Optional[List[Dict[str, Any]]] = None,
    refresh_if_missing: bool = False,
) -> Dict[str, Any]:
    """Fetch the latest delta snapshot with timestamp and required-contract coverage."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Find the most recent timestamp in the delta_cache
    cursor.execute("SELECT MAX(timestamp) FROM delta_cache WHERE symbol = ?", (symbol,))
    latest_timestamp = cursor.fetchone()[0]

    if not latest_timestamp:
        conn.close()
        missing_required_contracts = _normalize_required_contracts(required_contracts)
        if refresh_if_missing:
            refresh_result = calculate_and_store_high_accuracy_delta(
                symbol=symbol,
                db_path=db_path,
                required_contracts=required_contracts,
                force_refresh=True,
            )
            if refresh_result is not None:
                return fetch_latest_delta_snapshot(
                    symbol=symbol,
                    db_path=db_path,
                    required_contracts=required_contracts,
                    refresh_if_missing=False,
                )
        return {
            "timestamp": None,
            "options_data": [],
            "missing_required_contracts": missing_required_contracts,
        }

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
    missing_required_contracts = find_missing_required_contracts(options_data, required_contracts)

    if refresh_if_missing and missing_required_contracts:
        refresh_result = calculate_and_store_high_accuracy_delta(
            symbol=symbol,
            db_path=db_path,
            required_contracts=required_contracts,
            force_refresh=True,
        )
        if refresh_result is not None:
            return fetch_latest_delta_snapshot(
                symbol=symbol,
                db_path=db_path,
                required_contracts=required_contracts,
                refresh_if_missing=False,
            )

    return {
        "timestamp": latest_timestamp,
        "options_data": options_data,
        "missing_required_contracts": missing_required_contracts,
    }


def fetch_latest_delta_data(
    symbol: str = 'NIFTY50',
    db_path=DB_PATH,
    required_contracts: Optional[List[Dict[str, Any]]] = None,
    refresh_if_missing: bool = False,
) -> List[Dict]:
    """
    Fetches the most recent delta data for all strikes from the delta_cache table.
    """
    snapshot = fetch_latest_delta_snapshot(
        symbol=symbol,
        db_path=db_path,
        required_contracts=required_contracts,
        refresh_if_missing=refresh_if_missing,
    )
    return snapshot["options_data"]


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


def floor_to_100_strike(value: float) -> int:
    """Floor spot price to the nearest 100-point strike."""
    return int(float(value) // 100.0) * 100


def _resolve_nearest_expiry_value(
    rows: Optional[List[Dict[str, Any]]],
    current_date: Optional[date] = None,
) -> Optional[str]:
    current_date = current_date or date.today()
    valid_expiries: List[date] = []

    for row in rows or []:
        expiry_value = _parse_option_expiry_date(row.get("expiry_date") or row.get("expiry"))
        if expiry_value is None or expiry_value < current_date:
            continue
        valid_expiries.append(expiry_value)

    if not valid_expiries:
        return None
    return min(valid_expiries).isoformat()


def _match_exact_contract_from_rows(
    rows: Optional[List[Dict[str, Any]]],
    strike_price: int,
    option_type: str,
    expiry_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    option_type = str(option_type or "").upper()
    target_expiry = str(expiry_date)[:10] if expiry_date else None
    candidates: List[Dict[str, Any]] = []

    for row in rows or []:
        row_type = str(row.get("option_type") or "").upper()
        if row_type != option_type:
            continue

        row_strike = row.get("strike_price")
        try:
            row_strike_int = int(float(row_strike))
        except Exception:
            continue
        if row_strike_int != int(strike_price):
            continue

        row_expiry = str(row.get("expiry_date") or row.get("expiry") or "")[:10]
        if target_expiry and row_expiry != target_expiry:
            continue

        candidates.append(dict(row))

    if not candidates:
        return None

    def _candidate_sort_key(row: Dict[str, Any]) -> tuple:
        return (
            str(row.get("expiry_date") or row.get("expiry") or ""),
            str(row.get("tradingsymbol") or ""),
        )

    chosen = dict(sorted(candidates, key=_candidate_sort_key)[0])
    if chosen.get("strike_price") is not None:
        try:
            chosen["strike_price"] = int(float(chosen["strike_price"]))
        except Exception:
            pass
    return chosen


def find_nearest_expiry_option_contract(
    strike_price: int,
    option_type: str,
    symbol: str = "NIFTY50",
    option_rows: Optional[List[Dict[str, Any]]] = None,
    current_date: Optional[date] = None,
    db_path=DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Return the exact nearest-expiry option snapshot row for a strike/type."""
    rows = option_rows if option_rows is not None else fetch_latest_option_snapshot(symbol=symbol, db_path=db_path)
    nearest_expiry = _resolve_nearest_expiry_value(rows, current_date=current_date)
    if not nearest_expiry:
        return None
    return _match_exact_contract_from_rows(rows, strike_price=int(strike_price), option_type=option_type, expiry_date=nearest_expiry)


def find_nearest_expiry_open_interest_contract(
    strike_price: int,
    option_type: str,
    symbol: str = "NIFTY50",
    oi_rows: Optional[List[Dict[str, Any]]] = None,
    current_time=None,
    current_date: Optional[date] = None,
    db_path=DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Return the exact nearest-expiry OI snapshot row for a strike/type."""
    rows = oi_rows if oi_rows is not None else fetch_latest_open_interest_snapshot(
        symbol=symbol,
        current_time=current_time,
        db_path=db_path,
    )
    nearest_expiry = _resolve_nearest_expiry_value(rows, current_date=current_date)
    if not nearest_expiry:
        return None
    return _match_exact_contract_from_rows(rows, strike_price=int(strike_price), option_type=option_type, expiry_date=nearest_expiry)


def fetch_latest_oi_vwap_5m_for_contract(
    strike_price: int,
    option_type: str,
    symbol: str = "NIFTY50",
    oi_5m_rows: Optional[List[Dict[str, Any]]] = None,
    current_time=None,
    current_date: Optional[date] = None,
    db_path=DB_PATH,
) -> Optional[float]:
    """Return the latest completed 5m OI VWAP for the exact nearest-expiry contract."""
    rows = oi_5m_rows if oi_5m_rows is not None else fetch_latest_open_interest_5m_snapshot(
        symbol=symbol,
        current_time=current_time,
        db_path=db_path,
    )
    row = find_nearest_expiry_open_interest_contract(
        strike_price=strike_price,
        option_type=option_type,
        symbol=symbol,
        oi_rows=rows,
        current_time=current_time,
        current_date=current_date,
        db_path=db_path,
    )
    if not row:
        return None
    vwap = row.get("vwap")
    try:
        return float(vwap) if vwap is not None else None
    except Exception:
        return None


def scan_first_lower_pe_contract_below_ltp(
    buy_strike_price: int,
    max_ltp: float,
    symbol: str = "NIFTY50",
    option_rows: Optional[List[Dict[str, Any]]] = None,
    current_date: Optional[date] = None,
    db_path=DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Scan downward in 100-point PE strikes and return the first contract below max_ltp."""
    rows = option_rows if option_rows is not None else fetch_latest_option_snapshot(symbol=symbol, db_path=db_path)
    nearest_expiry = _resolve_nearest_expiry_value(rows, current_date=current_date)
    if not nearest_expiry:
        return None

    best_candidate: Optional[Dict[str, Any]] = None
    for row in rows or []:
        if str(row.get("option_type") or "").upper() != "PE":
            continue
        row_expiry = str(row.get("expiry_date") or row.get("expiry") or "")[:10]
        if row_expiry != nearest_expiry:
            continue
        try:
            strike_int = int(float(row.get("strike_price")))
            ltp_value = float(row.get("ltp"))
        except Exception:
            continue
        if strike_int >= int(buy_strike_price):
            continue
        if ltp_value >= float(max_ltp):
            continue
        if best_candidate is None or strike_int > int(best_candidate["strike_price"]):
            best_candidate = dict(row, strike_price=strike_int, ltp=ltp_value)

    return best_candidate


def fetch_current_exact_option_ltp(
    symbol: str = "NIFTY50",
    tradingsymbol: Optional[str] = None,
    strike_price: Optional[int] = None,
    option_type: Optional[str] = None,
    expiry: Optional[str] = None,
    option_rows: Optional[List[Dict[str, Any]]] = None,
    db_path=DB_PATH,
) -> Optional[float]:
    """Resolve the latest LTP for an exact option contract from the latest snapshot with DB fallback."""
    rows = option_rows if option_rows is not None else fetch_latest_option_snapshot(symbol=symbol, db_path=db_path)

    if tradingsymbol:
        for row in rows or []:
            if str(row.get("tradingsymbol") or "") != str(tradingsymbol):
                continue
            try:
                ltp_value = row.get("ltp")
                return float(ltp_value) if ltp_value is not None else None
            except Exception:
                break

    if strike_price is not None and option_type:
        contract = _match_exact_contract_from_rows(
            rows,
            strike_price=int(strike_price),
            option_type=str(option_type).upper(),
            expiry_date=str(expiry)[:10] if expiry else None,
        )
        if contract:
            try:
                ltp_value = contract.get("ltp")
                return float(ltp_value) if ltp_value is not None else None
            except Exception:
                pass

    return fetch_latest_option_price(
        symbol=symbol,
        tradingsymbol=tradingsymbol,
        strike_price=strike_price,
        option_type=option_type,
        expiry=expiry,
        db_path=db_path,
    )

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
