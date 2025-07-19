"""
Live VIX Data Fetcher using Kite API
Fetches live option chain data from Kite API, calculates VIX, and stores in database
"""

import pandas as pd
import math
from datetime import datetime, timedelta
from utils.db_func import store_vix_data_bulk, fetch_vix_data, store_high_accuracy_options_data
from broker.zerodha_client import kite_from_saved_token
import time
import sqlite3
from utils.iv import ProductionIVCalculator

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

def fetch_live_option_chain():
    """
    Fetch live option chain data from Kite API for NIFTY 50 using batch quotes
    Now calculates IV using ProductionIVCalculator instead of using API IV.
    """
    try:
        kite = kite_from_saved_token()
        if not kite:
            print("Failed to connect to Kite API")
            return None
        
        # Get all NFO instruments (options and futures)
        instruments = kite.instruments("NFO")
        print(f"Total NFO instruments: {len(instruments)}")
        
        # Filter for NIFTY 50 options
        nifty_option_symbols = []
        current_date = datetime.now().date()
        
        for instrument in instruments:
            # Check if it's a NIFTY option
            if (instrument['name'] == 'NIFTY' and 
                instrument['instrument_type'] in ['CE', 'PE'] and
                instrument['expiry'] >= current_date):
                
                quote_key = f"NFO:{instrument['tradingsymbol']}"
                nifty_option_symbols.append({
                    'quote_key': quote_key,
                    'instrument': instrument
                })
        
        print(f"Found {len(nifty_option_symbols)} NIFTY options to fetch quotes for")
        
        # Batch fetch quotes in groups of 50 (Kite API limit)
        batch_size = 50
        nifty_options = []
        iv_calc = ProductionIVCalculator()
        spot_price = get_nifty50_spot_price()
        if spot_price is None:
            print("Could not get NIFTY 50 spot price for IV calculation")
            return None
        
        for i in range(0, len(nifty_option_symbols), batch_size):
            batch = nifty_option_symbols[i:i + batch_size]
            quote_keys = [item['quote_key'] for item in batch]
            
            try:
                print(f"Fetching batch {i//batch_size + 1}/{(len(nifty_option_symbols) + batch_size - 1)//batch_size} ({len(quote_keys)} instruments)")
                quotes = kite.quote(quote_keys)
                
                for item in batch:
                    quote_key = item['quote_key']
                    instrument = item['instrument']
                    
                    if quote_key in quotes:
                        option_data = quotes[quote_key]
                        if isinstance(option_data, dict):
                            # Remove API IV fetching, calculate IV using ProductionIVCalculator
                            ltp = option_data.get('last_price', 0)
                            strike = instrument['strike']
                            option_type = instrument['instrument_type']
                            expiry = instrument['expiry']
                            expiry_str = str(expiry)
                            # Calculate IV using your calculator
                            iv_result = iv_calc.calculate_iv(
                                spot=spot_price,
                                strike=strike,
                                ltp=ltp,
                                expiry_date_str=expiry_str,
                                option_type=option_type
                            )
                            calculated_iv = iv_result['iv'] if iv_result and 'iv' in iv_result else None
                            option_info = {
                                'strikePrice': strike,
                                'IV': calculated_iv,  # Use calculated IV only
                                'openInterest': option_data.get('oi', 0),
                                'lastPrice': ltp,
                                'optionType': option_type,
                                'expiry': expiry,
                                'tradingsymbol': instrument['tradingsymbol']
                            }
                            nifty_options.append(option_info)
                
                # Add delay between batches to avoid rate limiting
                if i + batch_size < len(nifty_option_symbols):
                    time.sleep(0.5)  # 500ms delay between batches
                    
            except Exception as e:
                print(f"Error fetching batch {i//batch_size + 1}: {e}")
                continue
        
        print(f"Successfully fetched quotes for {len(nifty_options)} NIFTY options")
        return nifty_options
        
    except Exception as e:
        print(f"Error fetching live option chain: {e}")
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
        from strategies.indicators import calculate_vix
        vix_value = calculate_vix(option_data, spot_price, strike_window)
        
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
            print(f"Live VIX calculated and stored: {vix_value:.2f} (with indicators)")
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
if __name__ == "__main__":
    #print("[DEBUG] Running IV debug script...")
    #debug_print_iv_values()
    get_nifty50_spot_price()
    """
    # Example usage
    symbol = "NIFTY50"
    
    # Calculate and store live VIX once
    if symbol is not None:
        vix_value = calculate_and_store_vix(symbol)
    else:
        print("[WARN] symbol is None, skipping calculate_and_store_vix.")
    
    # Export recent VIX data
    if symbol is not None:
        export_vix_to_csv(symbol, days=7)
    else:
        print("[WARN] symbol is None, skipping export_vix_to_csv.")
    
    # continuous_vix_monitoring(symbol, interval_minutes=5, duration_hours=2) 
    """
