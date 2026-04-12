"""
Live VIX Data Fetcher using Kite API
Fetches live option chain data from Kite API, calculates VIX, and stores in database
"""

import pandas as pd
import math
from datetime import date, datetime, timedelta
from config.config import CONFIG
from utils.db_func import (
    store_vix_data_bulk,
    fetch_vix_data,
    store_high_accuracy_options_data,
    build_nifty_strategy_required_contracts,
    summarize_option_snapshot_coverage,
)
from broker.zerodha_client import kite_from_saved_token
import time
import sqlite3
from utils.iv import ProductionIVCalculator


SNAPSHOT_STRIKE_BAND = int(CONFIG.get("STRIKE_BAND", 500))
SNAPSHOT_STRIKE_INTERVAL = int(CONFIG.get("STRIKE_INTERVAL", 50))
SNAPSHOT_EXTRA_STRIKES = 2


def get_nearest_nifty_futures_price(kite=None):
    """
    Returns last_price of nearest NIFTY FUT series from the instruments list via Kite.
    """
    try:
        if kite is None:
            kite = kite_from_saved_token()
            if not kite:
                return None
        instruments = kite.instruments("NFO")
        # Filter FUT instruments for NIFTY
        futs = [inst for inst in instruments if inst.get('name') == 'NIFTY' and inst.get('instrument_type') == 'FUT']
        if not futs:
            return None
        # choose earliest expiry
        futs_sorted = sorted(futs, key=lambda x: x.get('expiry') or '')
        nearest = futs_sorted[0]
        symbol = f"NFO:{nearest['tradingsymbol']}"
        q = kite.quote(symbol)
        if q and symbol in q:
            return q[symbol].get('last_price')
        return None
    except Exception as e:
        print("Error fetching futures price:", e)
        return None


def safe_expiry_to_string(expiry):
    """Safely convert expiry to string format for IV calculation"""
    try:
        if isinstance(expiry, str):
            return expiry
        elif hasattr(expiry, 'strftime'):
            return expiry.strftime('%Y-%m-%d')
        elif hasattr(expiry, 'date'):
            return expiry.date().strftime('%Y-%m-%d')
        else:
            return str(expiry)
    except:
        return str(expiry)


def _normalize_expiry_date(expiry):
    if isinstance(expiry, str):
        try:
            return datetime.strptime(expiry, "%Y-%m-%d").date()
        except Exception:
            return None
    if isinstance(expiry, datetime):
        return expiry.date()
    if isinstance(expiry, date):
        return expiry
    if hasattr(expiry, "date"):
        try:
            return expiry.date()
        except Exception:
            return None
    return None


def _normalize_strike_value(strike):
    try:
        return int(float(strike))
    except Exception:
        return None


def _select_snapshot_instruments(option_instruments, spot_price, required_contracts=None):
    if spot_price is None:
        return option_instruments, required_contracts

    dated_instruments = []
    for inst in option_instruments:
        expiry_date = _normalize_expiry_date(inst.get("expiry"))
        if expiry_date is None:
            continue
        dated_instruments.append((inst, expiry_date))

    if not dated_instruments:
        return option_instruments, required_contracts

    nearest_expiry = min(expiry_date for _, expiry_date in dated_instruments)
    nearest_expiry_instruments = [
        inst for inst, expiry_date in dated_instruments if expiry_date == nearest_expiry
    ]
    if not nearest_expiry_instruments:
        return option_instruments, required_contracts

    ce_strikes = sorted({
        strike
        for inst in nearest_expiry_instruments
        if inst.get("instrument_type") == "CE"
        for strike in [_normalize_strike_value(inst.get("strike"))]
        if strike is not None
    })

    resolved_required_contracts = required_contracts
    if resolved_required_contracts is None:
        resolved_required_contracts = build_nifty_strategy_required_contracts(spot_price, ce_strikes)

    required_strikes = [
        strike
        for contract in resolved_required_contracts or []
        for strike in [_normalize_strike_value(contract.get("strike_price"))]
        if strike is not None
    ]

    strike_buffer = SNAPSHOT_STRIKE_BAND + (SNAPSHOT_STRIKE_INTERVAL * SNAPSHOT_EXTRA_STRIKES)
    if required_strikes:
        lower_bound = min(required_strikes) - strike_buffer
        upper_bound = max(required_strikes) + strike_buffer
    else:
        center_strike = _normalize_strike_value(spot_price)
        if center_strike is None:
            return nearest_expiry_instruments, resolved_required_contracts
        lower_bound = center_strike - strike_buffer
        upper_bound = center_strike + strike_buffer

    filtered_instruments = []
    for inst in nearest_expiry_instruments:
        strike = _normalize_strike_value(inst.get("strike"))
        if strike is None:
            filtered_instruments.append(inst)
            continue
        if lower_bound <= strike <= upper_bound:
            filtered_instruments.append(inst)

    if filtered_instruments:
        print(
            f"[OPTION SNAPSHOT] Using nearest expiry {nearest_expiry} "
            f"with strike window {lower_bound}-{upper_bound} "
            f"across {len(filtered_instruments)} instruments"
        )
        return filtered_instruments, resolved_required_contracts

    return nearest_expiry_instruments, resolved_required_contracts

def get_nifty50_spot_price():
    """
    Get current NIFTY 50 spot price from Kite API
    """
    try:
        kite = kite_from_saved_token()
        if not kite:
            print("Failed to connect to Kite API")
            return None
        
        # Get NIFTY 50 quote
        quote = kite.quote("NSE:NIFTY 50")
        if quote and "NSE:NIFTY 50" in quote:
            quote_data = quote["NSE:NIFTY 50"]
            if isinstance(quote_data, dict) and "last_price" in quote_data:
                spot_price = quote_data["last_price"]
                print(f"Current NIFTY 50 spot price: {spot_price}")
                return spot_price
        print("Could not get NIFTY 50 spot price")
        return None
            
    except Exception as e:
        print(f"Error getting NIFTY 50 spot price: {e}")
        return None


def fetch_live_option_chain(spot_price=None, required_contracts=None):
    """
    Fetch NIFTY option chain quotes and return a list of enriched dicts:
    Each dict has keys: strikePrice, optionType (CE/PE), expiry, tradingsymbol,
    bestBid, bestAsk, midPrice, lastPrice, openInterest
    """
    try:
        kite = kite_from_saved_token()
        if not kite:
            print("Failed to connect to Kite API")
            return None

        instruments = kite.instruments("NFO")
        current_date = datetime.now().date()

        nifty_option_symbols = []
        for inst in instruments:
            # instrument structure might differ; filter by name and type
            if inst.get('name') == 'NIFTY' and inst.get('instrument_type') in ['CE', 'PE']:
                # ensure expiry not in past
                exp = inst.get('expiry')
                if isinstance(exp, str):
                    exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                elif hasattr(exp, 'date'):
                    exp_date = exp.date() if hasattr(exp, 'date') else exp
                else:
                    exp_date = None
                if exp_date and exp_date < current_date:
                    continue
                nifty_option_symbols.append(inst)

        print(f"Found {len(nifty_option_symbols)} option instruments")

        filtered_option_symbols, required_contracts = _select_snapshot_instruments(
            nifty_option_symbols,
            spot_price,
            required_contracts=required_contracts,
        )

        def expiry_sort_value(inst):
            expiry = _normalize_expiry_date(inst.get('expiry'))
            return expiry or datetime.max.date()

        required_quote_keys = []
        if required_contracts:
            for contract in required_contracts:
                option_type = str(contract.get("option_type") or "").upper()
                strike_price = contract.get("strike_price")
                if strike_price is None:
                    continue
                candidates = [
                    inst for inst in filtered_option_symbols
                    if inst.get('instrument_type') == option_type and int(inst.get('strike') or 0) == int(strike_price)
                ]
                if not candidates:
                    continue
                chosen = min(candidates, key=lambda inst: (expiry_sort_value(inst), inst.get('tradingsymbol') or ""))
                required_quote_keys.append(f"NFO:{chosen['tradingsymbol']}")

        required_quote_keys = sorted(set(required_quote_keys))

        # batch fetch quotes
        batch_size = 50
        results = []
        # prepare map tradingsymbol -> instrument
        ts_map = {f"NFO:{inst['tradingsymbol']}": inst for inst in filtered_option_symbols}
        seen_symbols = set()

        def append_quotes(quote_keys, quotes):
            for sym in quote_keys:
                if sym in seen_symbols or sym not in quotes:
                    continue
                q = quotes[sym]
                inst = ts_map[sym]
                strike = inst.get('strike')
                opt_type = inst.get('instrument_type')
                expiry = inst.get('expiry')
                tradingsymbol = inst.get('tradingsymbol')
                oi = q.get('oi') or q.get('open_interest') or q.get('openInterest') or 0

                bestBid = 0.0
                bestAsk = 0.0
                depth = q.get('depth') or {}
                buys = depth.get('buy') or []
                sells = depth.get('sell') or []

                if buys and isinstance(buys, list) and len(buys) > 0:
                    bestBid = buys[0].get('price') or buys[0].get('price')
                if sells and isinstance(sells, list) and len(sells) > 0:
                    bestAsk = sells[0].get('price') or sells[0].get('price')

                try:
                    if bestBid and bestAsk:
                        ltp = (float(bestBid) + float(bestAsk)) / 2.0
                    elif bestBid:
                        ltp = float(bestBid)
                    elif bestAsk:
                        ltp = float(bestAsk)
                    else:
                        ltp = q.get('last_price') or q.get('lastPrice') or q.get('ltp') or 0
                except Exception as e:
                    print(f"Failed to calculate ltp due to : {e}")
                    ltp = q.get('last_price') or q.get('lastPrice') or q.get('ltp') or 0

                results.append({
                    "strikePrice": strike,
                    "optionType": opt_type,
                    "expiry": expiry,
                    "tradingsymbol": tradingsymbol,
                    "bestBid": bestBid,
                    "bestAsk": bestAsk,
                    "lastPrice": ltp,
                    "openInterest": oi
                })
                seen_symbols.add(sym)

        keys = list(ts_map.keys())
        for i in range(0, len(keys), batch_size):
            chunk = keys[i:i+batch_size]
            try:
                quotes = kite.quote(chunk)
                append_quotes(chunk, quotes)
                # small pause
                time.sleep(0.2)
            except Exception as e:
                print("Error fetching chunk quotes:", e)
                time.sleep(0.5)
                continue

        missing_required_keys = [key for key in required_quote_keys if key not in seen_symbols]
        if missing_required_keys:
            print(f"[OPTION SNAPSHOT] Retrying {len(missing_required_keys)} required contract quote(s)")
            for i in range(0, len(missing_required_keys), 10):
                chunk = missing_required_keys[i:i+10]
                try:
                    quotes = kite.quote(chunk)
                    append_quotes(chunk, quotes)
                    time.sleep(0.2)
                except Exception as e:
                    print("Error retrying required quote chunk:", e)
                    time.sleep(0.5)
                    continue

        if required_contracts:
            coverage = summarize_option_snapshot_coverage(
                [
                    {
                        "option_type": row["optionType"],
                        "strike_price": row["strikePrice"],
                    }
                    for row in results
                ],
                required_contracts=required_contracts,
            )
            missing_desc = ", ".join(
                f"{item['option_type']} {item['strike_price']}"
                for item in coverage["missing_required_contracts"]
            ) or "none"
            print(
                f"[OPTION SNAPSHOT] contracts={coverage['contract_count']} "
                f"range={coverage['min_available_strike']}-{coverage['max_available_strike']} "
                f"missing={missing_desc}"
            )

        print(f"Fetched {len(results)} option quotes")
        return results

    except Exception as e:
        print("Error in fetch_live_option_chain:", e)
        return None


def calculate_live_vix(spot_price=None, strike_window=300):
    """
    Calculate live VIX using Kite API data
    Also computes AO and Donchian indicators on the VIX time series and stores them.
    """
    try:
        # Get spot price if not provided
        if spot_price is None:
            spot_price = get_nifty50_spot_price()
            if spot_price is None:
                print("Could not get NIFTY 50 spot price")
                return None
        # Ensure spot_price is a float
        if not isinstance(spot_price, float):
            try:
                spot_price = float(spot_price)
            except Exception:
                print(f"Invalid spot_price: {spot_price}")
                return None
        
        # Fetch live option chain data
        option_data = fetch_live_option_chain()
        if not option_data:
            print("No live option data available")
            return None
        
        print(f"Calculating VIX with {len(option_data)} options around spot price {spot_price}")

        # Calculate VIX using the existing function
        from core.indicators import calculate_enhanced_vix,calculate_vix2
        
        current_day = datetime.now().weekday()
        if current_day == 1:
            vix_value = calculate_enhanced_vix(option_data, spot_price, strike_window)
        
        else:
            vix_value = calculate_vix2(option_data, spot_price, strike_window)
        kite = kite_from_saved_token()

        futures_price = get_nearest_nifty_futures_price(kite=kite)
        if futures_price: 
            if isinstance(futures_price, str):
                try:
                    futures_price = float(futures_price)
                except:
                    futures_price = None

        print(f"Using futures price (forward) = {futures_price} (spot {spot_price})")
        #vix_value = calculate_enhanced_vix(option_data, spot_price, futures_price=futures_price, verbose=True)
        #vix_value = calculate_enhanced_vix_30d(option_data, spot_price, futures_price=futures_price, verbose=True)

        # fallback if enhanced fails
        if vix_value is None:
            print("[WARN] Enhanced VIX failed, falling back to calculate_vix2")
            vix_value = calculate_vix2(option_data, spot_price, strike_window)

        
        if vix_value is not None:
            # Fetch recent VIX data for indicator calculation
            from utils.db_func import fetch_vix_data
            import pandas as pd
            try:
                vix_df = fetch_vix_data(symbol="NIFTY50")  # Get all, then slice
            except Exception as e:
                print(f"Warning: Could not fetch existing VIX data: {e}")
                # Create empty DataFrame with correct columns
                vix_df = pd.DataFrame(columns=['vix_value', 'symbol'])
            
            # Append the new value
            now = pd.Timestamp.now()
            new_row = pd.DataFrame({
                'vix_value': [vix_value],
                'symbol': ["NIFTY50"]
            }, index=pd.DatetimeIndex([now]))
            vix_df = pd.concat([vix_df, new_row])
            # Only keep last 40 rows
            vix_df = vix_df.tail(40)
            # Compute AO and Donchian on vix_value
            ao_fast, ao_slow, donchian_period = 5, 34, 20
            vix_df = vix_df.sort_index()
            median_price = vix_df['vix_value']
            vix_df['vix_ao_value'] = median_price.rolling(window=ao_fast).mean() - median_price.rolling(window=ao_slow).mean()
            vix_df['vix_donchian_upper'] = median_price.rolling(window=donchian_period).max()
            vix_df['vix_donchian_lower'] = median_price.rolling(window=donchian_period).min()
            vix_df['vix_donchian_mid'] = (vix_df['vix_donchian_upper'] + vix_df['vix_donchian_lower']) / 2
            # Get the latest row (the one we want to store)
            latest = vix_df.iloc[-1]
            # Store VIX data with indicators
            store_vix_data(
                timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
                symbol="NIFTY50",
                vix_value=float(vix_value),
                vix_ao_value=float(latest['vix_ao_value']) if pd.notna(latest['vix_ao_value']) else 0.0,
                vix_donchian_upper=float(latest['vix_donchian_upper']) if pd.notna(latest['vix_donchian_upper']) else 0.0,
                vix_donchian_lower=float(latest['vix_donchian_lower']) if pd.notna(latest['vix_donchian_lower']) else 0.0,
                vix_donchian_mid=float(latest['vix_donchian_mid']) if pd.notna(latest['vix_donchian_mid']) else 0.0
            )
            #print(f"Live VIX calculated and stored: {vix_value:.2f} (with indicators)")
            return vix_value
        else:
            print("Live VIX calculation failed")
            return None
            
    except Exception as e:
        print(f"Error in live VIX calculation: {e}")
        return None

def calculate_and_store_vix(symbol: str = "NIFTY50", spot_price: float = None, strike_window: int = 300):
    """
    Calculates VIX and stores it in the database using live Kite API data.
    
    Parameters:
        symbol: Symbol name (default: NIFTY50)
        spot_price: Current spot price (will fetch if not provided)
        strike_window: Window around ATM for VIX calculation
    
    Returns:
        VIX value or None if calculation fails
    """
    # Ensure symbol is a string
    if not symbol or not isinstance(symbol, str):
        symbol = "NIFTY50"
    if symbol == "NIFTY50" or symbol == "^NIFTY50":
        vix_check = calculate_live_vix(spot_price, strike_window)
        #print(f"Calculated vix is : {vix_check}")
        return calculate_live_vix(spot_price, strike_window)
    else:
        print(f"Live VIX calculation not supported for symbol: {symbol}")
        return None

def fetch_historical_vix_data(symbol: str = 'NIFTY50', days: int = 30):
    """
    Fetches historical VIX data from the database.
    
    Parameters:
        symbol: Symbol name
        days: Number of days to fetch
    
    Returns:
        DataFrame with historical VIX data
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    return fetch_vix_data(
        symbol=symbol,
        start=start_date.strftime("%Y-%m-%d"),
        end=end_date.strftime("%Y-%m-%d")
    )

def export_vix_to_csv(symbol: str = "NIFTY50", output_path: str = None, days: int = 30):
    """
    Exports VIX data to CSV file.
    
    Parameters:
        symbol: Symbol name
        output_path: Output file path (optional)
        days: Number of days to export
    """
    # Fetch VIX data
    vix_df = fetch_historical_vix_data(symbol, days)
    
    if vix_df.empty:
        print(f"No VIX data found for {symbol}")
        return None
    
    # Generate output path if not provided
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"exports/vix_data_{symbol}_{timestamp}.csv"
    
    # Export to CSV
    vix_df.to_csv(output_path)
    print(f"VIX data exported to: {output_path}")
    
    return output_path

def continuous_vix_monitoring(symbol: str = "NIFTY50", interval_minutes: int = 5, duration_hours: int = 8):
    """
    Continuously monitors and stores VIX data using live Kite API data.
    
    Parameters:
        symbol: Symbol to monitor (default: NIFTY50)
        interval_minutes: Interval between VIX calculations (minutes)
        duration_hours: Total duration to monitor (hours)
    """
    print(f"Starting live VIX monitoring for {symbol}")
    print(f"Interval: {interval_minutes} minutes, Duration: {duration_hours} hours")
    
    start_time = datetime.now()
    end_time = start_time + timedelta(hours=duration_hours)
    
    while datetime.now() < end_time:
        try:
            # Calculate and store live VIX
            vix_value = calculate_and_store_vix(symbol)
            
            if vix_value is not None:
                print(f"{datetime.now().strftime('%H:%M:%S')} - Live VIX: {vix_value:.2f}")
            else:
                print(f"{datetime.now().strftime('%H:%M:%S')} - Live VIX calculation failed")
            
            # Wait for next interval
            time.sleep(interval_minutes * 60)
            
        except KeyboardInterrupt:
            print("\nLive VIX monitoring stopped by user")
            break
        except Exception as e:
            print(f"Error in live VIX monitoring: {e}")
            time.sleep(60)  # Wait 1 minute before retrying
    
    print("Live VIX monitoring completed")

def debug_print_iv_values():
    print("\n=== IV Debug Script: Raw IV Values from API (NIFTY50, IV != 0) ===")
    options = fetch_live_option_chain()
    if not options:
        print("No option data fetched.")
        return
    print(f"Fetched {len(options)} options.")
    print(f"{'Symbol':<20} {'Strike':>8} {'Type':>3} {'LTP':>10} {'IV (raw)':>10}")
    print("-"*60)
    count = 0
    for opt in options:
        symbol = str(opt.get('tradingsymbol', '-'))
        strike = str(opt.get('strikePrice', '-'))
        opt_type = str(opt.get('optionType', '-'))
        ltp = str(opt.get('lastPrice', '-'))
        iv = opt.get('IV', None)
        # Only print NIFTY options with valid IV
        if 'NIFTY' not in symbol:
            continue
        if iv is None or iv == 0 or iv == 0.0:
            continue
        print(f"{symbol:<20} {strike:>8} {opt_type:>3} {ltp:>10} {iv:>10}")
        count += 1
    print(f"\n[INFO] Found {count} NIFTY options with nonzero IV.")
    print("[INFO] If you see IV values > 100, the API is returning them as percent or there is a data issue.")

# Update store_vix_data to accept new columns
def store_vix_data(timestamp, symbol, vix_value, vix_ao_value=None, vix_donchian_upper=None, vix_donchian_lower=None, vix_donchian_mid=None, db_path='db/trading_bot.db'):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO vix_data (timestamp, symbol, vix_value, vix_ao_value, vix_donchian_upper, vix_donchian_lower, vix_donchian_mid)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, symbol, vix_value, vix_ao_value, vix_donchian_upper, vix_donchian_lower, vix_donchian_mid))
    conn.commit()
    conn.close() 

if __name__ =="__main__":
    fetch_live_option_chain()
