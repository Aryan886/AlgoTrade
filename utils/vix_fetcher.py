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
    Fixed version of fetch_live_option_chain with proper datetime handling
    Now uses bid-ask mid-price instead of last_price for IV calculation
    """
    try:
        kite = kite_from_saved_token()
        if not kite:
            print("Failed to connect to Kite API")
            return None
        
        instruments = kite.instruments("NFO")
        print(f"Total NFO instruments: {len(instruments)}")
        current_date = datetime.now().date()

        expiry_counts = {}
        for instrument in instruments:
            if instrument['name'] == 'NIFTY' and instrument['instrument_type'] in ['CE', 'PE']:
                exp_str = str(instrument['expiry'])
                expiry_counts[exp_str] = expiry_counts.get(exp_str, 0) + 1

        print("Available NIFTY option expiries:")
        for exp, count in sorted(expiry_counts.items()):
            days = (datetime.strptime(exp, '%Y-%m-%d').date() - current_date).days
            print(f"  {exp}: {count} options ({days} days)")
        
        nifty_option_symbols = []
        
        for instrument in instruments:
            if (instrument['name'] == 'NIFTY' and 
                instrument['instrument_type'] in ['CE', 'PE']):
                
                # Fix expiry comparison
                instrument_expiry = instrument['expiry']
                if isinstance(instrument_expiry, str):
                    instrument_expiry = datetime.strptime(instrument_expiry, '%Y-%m-%d').date()
                elif hasattr(instrument_expiry, 'date'):
                    instrument_expiry = instrument_expiry.date() if hasattr(instrument_expiry, 'date') else instrument_expiry
                
                if instrument_expiry >= current_date:
                    days_to_expiry = (instrument_expiry - current_date).days
                    # Only use options with 20-40 days to expiry for VIX calculation
                    if 20 <= days_to_expiry <= 40:
                        quote_key = f"NFO:{instrument['tradingsymbol']}"
                        nifty_option_symbols.append({
                            'quote_key': quote_key,
                            'instrument': instrument
                        })
        
        print(f"Found {len(nifty_option_symbols)} NIFTY options to fetch quotes for")

        
        # Batch fetch quotes
        batch_size = 50
        nifty_options = []
        iv_calc = ProductionIVCalculator()
        spot_price = get_nifty50_spot_price()
        
        if spot_price is None:
            print("Could not get NIFTY 50 spot price for IV calculation")
            return None
        
        debug_count = 0  # Counter for debug prints
        
        for i in range(0, len(nifty_option_symbols), batch_size):
            batch = nifty_option_symbols[i:i + batch_size]
            quote_keys = [item['quote_key'] for item in batch]
            
            try:
                print(f"Fetching batch {i//batch_size + 1}/{(len(nifty_option_symbols) + batch_size - 1)//batch_size}")
                quotes = kite.quote(quote_keys)
                
                for item in batch:
                    quote_key = item['quote_key']
                    instrument = item['instrument']
                    
                    if quote_key in quotes:
                        option_data = quotes[quote_key]
                        if isinstance(option_data, dict):
                            ltp = option_data.get('last_price', 0)
                            strike = instrument['strike']
                            option_type = instrument['instrument_type']
                            expiry = instrument['expiry']
                            
                            # Extract bid/ask prices from depth data
                            best_bid = None
                            best_ask = None
                            mid_price = None
                            price_for_iv = ltp  # Default fallback
                            
                            depth = option_data.get('depth', {})
                            if isinstance(depth, dict):
                                # Get best bid (highest buy price)
                                buy_orders = depth.get('buy', [])
                                if buy_orders and len(buy_orders) > 0 and isinstance(buy_orders[0], dict):
                                    best_bid = buy_orders[0].get('price', None)
                                
                                # Get best ask (lowest sell price)
                                sell_orders = depth.get('sell', [])
                                if sell_orders and len(sell_orders) > 0 and isinstance(sell_orders[0], dict):
                                    best_ask = sell_orders[0].get('price', None)
                                
                                # Calculate mid-price if both bid and ask are available
                                if best_bid is not None and best_ask is not None and best_bid > 0 and best_ask > 0:
                                    mid_price = (best_bid + best_ask) / 2
                                    price_for_iv = mid_price
                                else:
                                    # Fall back to last_price
                                    price_for_iv = ltp
                            
                            # Fix expiry string conversion
                            expiry_str = safe_expiry_to_string(expiry)
                            
                            # Calculate IV using the price_for_iv (mid_price or LTP)
                            iv_result = iv_calc.calculate_iv(
                                spot=spot_price,
                                strike=strike,
                                ltp=price_for_iv,  # Now using mid_price when available
                                expiry_date_str=expiry_str,
                                option_type=option_type
                            )
                            
                            calculated_iv = iv_result['iv'] if iv_result and 'iv' in iv_result else None

                            # Normalize if suspiciously high (optional safeguard)
                            if calculated_iv and calculated_iv > 1.0:
                                print(f"[WARN] IV {calculated_iv:.2f} seems high. Adjusting to decimal.")
                                calculated_iv = calculated_iv / 100.0
                            
                            # Debug: Print first 5 options with bid/ask/mid_price info
                            if debug_count < 5:
                                mid_str = f"{mid_price:.4f}" if mid_price is not None else "N/A"
                                used_price_str = f"{price_for_iv:.4f}" if price_for_iv is not None else "N/A"
                                iv_str = f"{calculated_iv:.6f}" if calculated_iv is not None else "N/A"
                                print(f"DEBUG {debug_count+1}: Strike={strike}, Type={option_type}, "
                                      f"Bid={best_bid}, Ask={best_ask}, Mid={mid_str}, "
                                      f"LTP={ltp}, Used_Price={used_price_str}, IV={iv_str}")
                                debug_count += 1
                            
                            option_info = {
                                'strikePrice': strike,
                                'IV': calculated_iv,
                                'openInterest': option_data.get('oi', 0),
                                'lastPrice': ltp,  # Keep original LTP
                                'bestBid': best_bid,  # Add best bid
                                'bestAsk': best_ask,  # Add best ask
                                'midPrice': mid_price,  # Add calculated mid price
                                'priceUsedForIV': price_for_iv,  # Add the price actually used for IV
                                'optionType': option_type,
                                'expiry': expiry,
                                'tradingsymbol': instrument['tradingsymbol']
                            }
                            nifty_options.append(option_info)
                
                if i + batch_size < len(nifty_option_symbols):
                    time.sleep(0.5)
                    
            except Exception as e:
                print(f"Error fetching batch {i//batch_size + 1}: {e}")
                continue
        
        print(f"Successfully fetched quotes for {len(nifty_options)} NIFTY options")
        
        # Debug: Check IV distribution
        valid_ivs = [opt['IV'] for opt in nifty_options if opt['IV'] and opt['IV'] > 0]
        if valid_ivs:
            import numpy as np
            print(f"IV Stats: Min={min(valid_ivs):.6f}, Max={max(valid_ivs):.6f}, Mean={np.mean(valid_ivs):.6f}")
            if max(valid_ivs) > 1.0:
                print("⚠️ WARNING: IVs > 1.0 detected - likely in percentage form")
        
        # Debug: Check bid/ask availability
        options_with_bid_ask = [opt for opt in nifty_options if opt['bestBid'] is not None and opt['bestAsk'] is not None]
        print(f"Options with bid/ask data: {len(options_with_bid_ask)}/{len(nifty_options)} ({len(options_with_bid_ask)/len(nifty_options)*100:.1f}%)")
        
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
        from strategies.indicators import calculate_enhanced_vix,calculate_vix,calculate_vix2
        #vix_value = calculate_vix2(option_data, spot_price, strike_window)
        #vix_value = calculate_enhanced_vix(option_data, spot_price, strike_window)
        #vix_value = calculate_vix2(option_data, spot_price, strike_window)
        
        futures_price = None  # <-- optionally fetch real futures price and set it here
        vix_value = calculate_enhanced_vix(option_data, spot_price, futures_price=futures_price, strike_window=strike_window)
        # If enhanced method fails, fallback to IV-based or simpler method:
        if vix_value is None:
            print("[WARN] enhanced VIX calculation failed; falling back to IV-based calculate_vix2")
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
        vix_check = calculate_live_vix(spot_price, strike_window)
        print(f"Calculated vix is : {vix_check}")
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
    calculate_live_vix()
    #fetch_live_option_chain()