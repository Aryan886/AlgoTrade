import pandas as pd
from strategies.indicators import add_donchian_channel, add_awesome_oscillator
from utils.db_func import (
    fetch_market_data, 
    fetch_vix_data, 
    calculate_and_store_high_accuracy_delta, 
    fetch_latest_delta_data
)
from utils.utility import setup_paper_trading_logger

# Initialize loggers
paper_logger, trade_logger, position_logger = setup_paper_trading_logger()

CUTOFF_TIME = pd.to_datetime("13:00").time()

def emergency_column_fix(df, data_type="market"):
    """Emergency fix for column name case issues and indicator mapping"""
    if df.empty:
        return df
    
    # Create a copy to avoid modifying original
    df = df.copy()
    
    print(f"[DEBUG] {data_type} data columns before fix: {list(df.columns)}")
    
    # Handle VIX data with pre-calculated indicators
    if data_type.lower() == "vix":
        # VIX data has pre-calculated indicators, just map them properly
        if 'vix_value' in df.columns:
            df['close'] = df['vix_value']  # lowercase
            df['high'] = df['vix_value']   # lowercase
            df['low'] = df['vix_value']    # lowercase
            print(f"[VIX FIX] Mapped vix_value to OHLC columns")
        
        # Map existing VIX indicators to expected column names
        vix_indicator_mapping = {
            'vix_donchian_upper': 'donchian_upper_vix',
            'vix_donchian_lower': 'donchian_lower_vix', 
            'vix_donchian_mid': 'donchian_mid_vix',
            'vix_ao_value': 'ao_value'
        }
        
        for old_col, new_col in vix_indicator_mapping.items():
            if old_col in df.columns:
                df[new_col] = df[old_col]
                print(f"[VIX FIX] Mapped {old_col} -> {new_col}")
        
        print(f"[DEBUG] VIX data columns after fix: {list(df.columns)}")
        return df
    
    # For market data (5m, 15m), all columns should already be lowercase from standardize_column_names()
    # No OHLC mapping needed since standardize_column_names() already handles this
    
    # Map pre-calculated indicators to expected format based on timeframe
    if "5min" in data_type.lower() or "5m" in data_type.lower():
        suffix = "_5m"
    elif "15min" in data_type.lower() or "15m" in data_type.lower():
        suffix = "_15m"
    else:
        suffix = ""
    
    # Map available indicators individually to their suffixed columns
    mapped = False

    if 'donchian_upper' in df.columns:
        df[f'donchian_upper{suffix}'] = df['donchian_upper']
        print(f"[INDICATOR FIX] Mapped donchian_upper -> donchian_upper{suffix}")
        mapped = True
    else:
        print(f"[WARNING] Column 'donchian_upper' missing in {data_type} data")

    if 'donchian_lower' in df.columns:
        df[f'donchian_lower{suffix}'] = df['donchian_lower']
        print(f"[INDICATOR FIX] Mapped donchian_lower -> donchian_lower{suffix}")
        mapped = True
    else:
        print(f"[WARNING] Column 'donchian_lower' missing in {data_type} data")

    if 'donchian_mid' in df.columns:
        df[f'donchian_mid{suffix}'] = df['donchian_mid']
        print(f"[INDICATOR FIX] Mapped donchian_mid -> donchian_mid{suffix}")
        mapped = True
    else:
        print(f"[WARNING] Column 'donchian_mid' missing in {data_type} data")

    if not mapped:
        print(f"[ERROR] None of the Donchian columns found in {data_type} data")
    
    if 'ao_value' in df.columns:
        # AO doesn't need suffix, it's used as-is
        print(f"[INDICATOR FIX] AO value already correctly named")
    
   # print(f"[DEBUG] {data_type} data columns after fix: {list(df.columns)}")
    print(f"[DEBUG] Columns in {data_type} after fix: {list(df.columns)}")

    
    return df
def debug_vix_data(symbol="NIFTY50"):
    """Debug function to inspect VIX data structure"""
    print(f"[DEBUG] Inspecting VIX data for symbol: {symbol}")
    
    try:
        vix_data = fetch_vix_data(symbol=symbol)
        
        if vix_data.empty:
            print("[DEBUG] VIX data is empty!")
            return
            
        print(f"[DEBUG] VIX data shape: {vix_data.shape}")
        print(f"[DEBUG] VIX data columns: {list(vix_data.columns)}")
        print(f"[DEBUG] VIX data types:\n{vix_data.dtypes}")
        print(f"[DEBUG] VIX data sample:\n{vix_data.head()}")
        
        # Check for numeric columns
        numeric_cols = vix_data.select_dtypes(include=['number']).columns
        print(f"[DEBUG] Numeric columns: {list(numeric_cols)}")
        
        return vix_data
        
    except Exception as e:
        print(f"[DEBUG ERROR] Error fetching VIX data: {e}")
        return None

def donchian_ao_strategy(symbol="NIFTY50"):
    """Main strategy function with proper error handling"""
    
    print(f"[DEBUG] Running strategy for symbol: {symbol}")
    if not symbol:
        symbol = "NIFTY50"

    try:
        # 1. Fetch Data from database (REMOVED DUPLICATE CODE)
        print(f"[DEBUG] Fetching market data for symbol: {symbol}")
        df_5min = fetch_market_data(symbol=symbol, interval="5m")
        df_15min = fetch_market_data(symbol=symbol, interval="15m")
        vix_data = fetch_vix_data(symbol=symbol)

        #Calculate indicators for 15m only, because there seems to be some godly issue with that mf
        df_15min.columns = [col.lower() for col in df_15min.columns]
        df_15min = add_donchian_channel(df_15min, suffix="_15m")
        df_15min = add_awesome_oscillator(df_15min)

        # Debug VIX data structure if empty or problematic
        if vix_data.empty:
            print("[DEBUG] VIX data is empty, trying to debug...")
            debug_vix_data(symbol)

        # Check if any data is empty
        if df_5min.empty or df_15min.empty or vix_data.empty:
            paper_logger.warning("Data not available for one or more timeframes. Skipping strategy.")
            return None

        # EMERGENCY FIX: Apply column fixes with data type info
        try:
            df_5min = emergency_column_fix(df_5min, "5min_market")
            #df_15min = emergency_column_fix(df_15min, "15min_market")
            vix_data = emergency_column_fix(vix_data, "VIX")
            print("[EMERGENCY FIX] Column fixes applied successfully")
            print("[DEBUG] Checking existence of required 15m columns...")
            print("Available columns in df_15min:", list(df_15min.columns))

            if 'donchian_mid_15m' not in df_15min.columns:
                raise Exception("[FATAL] donchian_mid_15m missing from df_15min after emergency fix!")

        except Exception as e:
            print(f"[EMERGENCY FIX ERROR] {e}")
            return None

        # CRITICAL: Basic data validation (no indicator calculation needed)
        basic_cols = ['high', 'low', 'close']
        
        if df_5min.empty or not all(col in df_5min.columns for col in basic_cols):
            paper_logger.error("5min data validation failed")
            return None
            
        if df_15min.empty or not all(col in df_15min.columns for col in basic_cols):
            paper_logger.error("15min data validation failed")
            return None
            
        # VIX validation - just need vix_value
        if vix_data.empty or 'vix_value' not in vix_data.columns:
            paper_logger.error("VIX data validation failed - missing vix_value column")
            return None
        
        print(f"[DEBUG] Data validated - 5min: {len(df_5min)} rows, 15min: {len(df_15min)} rows, VIX: {len(vix_data)} rows")

        # All indicators are pre-calculated in database, just map them properly
        print("[DEBUG] Using pre-calculated indicators from database")
        
        # Get the latest data point
        latest_5min = df_5min.iloc[-1]
        latest_15min = df_15min.iloc[-1]
        latest_vix = vix_data.iloc[-1]

        # --- Conditions to Avoid Taking a Position ---
        
        # 1. VIX Condition
        if latest_vix['close'] > latest_vix['donchian_mid_vix']:
            paper_logger.info("VIX is too high. No positions will be taken.")
            return None

        # 2. Simultaneous Breakout Condition
        now = pd.Timestamp.now().time()
        if now < CUTOFF_TIME:
            if latest_5min['close'] > latest_5min['donchian_upper_5m'] and \
               latest_15min['close'] > latest_15min['donchian_upper_15m']:
                print("Simultaneous breakout on 5m and 15m charts. No positions until 1:00 PM.")
                return None 

        # --- Essentials for Trade Evaluation ---
        essentials = {
            '5m_donchian': 1 if latest_5min['close'] > latest_5min['donchian_mid_5m'] else -1,
            '5m_ao': 1 if latest_5min['ao_value'] > 0 else -1,
            '15m_donchian': 1 if latest_15min['close'] > latest_15min['donchian_mid_15m'] else -1,
            '15m_ao': 1 if latest_15min['ao_value'] > 0 else -1,
        }

        essentials['vix_condition'] = 1 if latest_vix['close'] <= latest_vix['donchian_mid_vix'] else -1
        
        positive_essentials = sum(1 for value in essentials.values() if value == 1)

        # --- Trade Decision Logic ---
        
        # Avoid trade if all essentials are in the same direction
        if positive_essentials == 5 or positive_essentials == 0:
            paper_logger.info("All essentials are in the same direction. No trade.")
            return None

        # Take a trade if 1 or 2 essentials are positive
        if positive_essentials == 1 or positive_essentials == 2:
            # Check for the specific condition to sell ATM Call and Put
            if positive_essentials == 1 and essentials['5m_donchian'] == 1:
                paper_logger.info("SELL signal: 5m close is above 5m mid Donchian, and all other essentials are negative.")
                
                # 1. Calculate and store latest deltas
                calculate_and_store_high_accuracy_delta(symbol=symbol)
                
                # 2. Fetch options data with deltas
                options_data = fetch_latest_delta_data(symbol=symbol)
                if not options_data:
                    paper_logger.info("No options data with delta available to place a trade.")
                    return None

                first_leg, second_leg = select_options_for_trade(options_data)

                if first_leg and second_leg:
                    paper_logger.info(f"Selected pair for trade: {first_leg['tradingsymbol']} (Delta: {first_leg['delta']}) and {second_leg['tradingsymbol']} (Delta: {second_leg['delta']})")
                    return first_leg, second_leg
                else:
                    paper_logger.warning("Could not select a suitable pair of options for the trade.")
                    return None

            elif positive_essentials == 2 and (essentials['5m_donchian'] == 1 and essentials['5m_ao'] == 1):
                paper_logger.info("SELL Signal: 5m close is above mid-donchian and 5m AO is positive")

                # 1. Calculate and store deltas 
                calculate_and_store_high_accuracy_delta(symbol=symbol)  
                # 2. Fetch options data with deltas
                options_data = fetch_latest_delta_data(symbol=symbol)

                # Hybrid filter of PE/CE 
                pe_candidates = sorted(
                    [opt for opt in options_data if opt['option_type'] == 'PE' and 30 <= abs(opt['delta']) <= 50], 
                    key=lambda x: (-x['ltp'], abs(abs(x['delta']) - 40))
                )
                
                ce_candidates = sorted(
                    [opt for opt in options_data if opt['option_type'] == 'CE' and 30 <= abs(opt['delta']) <= 50], 
                    key=lambda x: (x['ltp'], abs(abs(x['delta']) - 40))
                )

                for pe in pe_candidates:
                    for ce in ce_candidates:
                        if (pe['ltp'] - ce['ltp']) > 18:
                            paper_logger.info(f"Selected PE: {pe['tradingsymbol']} ({pe['ltp']} | {pe['delta']}), CE: {ce['tradingsymbol']} ({ce['ltp']} | {ce['delta']})")
                            return pe, ce
            
            else:
                paper_logger.info("Trade condition met, but not the specific one for selling ATM options.")
                return None

        return None

    except Exception as e:
        paper_logger.error(f"Error in donchian_ao_strategy: {e}")
        return None

def select_options_for_trade(options_data):
    """
    Selects one ATM call and one ATM put based on delta rules.
    """
    # Separate calls and puts
    calls = [opt for opt in options_data if opt['option_type'] == 'CE']
    puts = [opt for opt in options_data if opt['option_type'] == 'PE']

    if not calls or not puts:
        print("Not enough options data to select a pair.")
        return None, None

    # 1. Find the option with delta closest to 50
    all_options = calls + puts
    first_leg = min(all_options, key=lambda x: abs(abs(x['delta']) - 50))

    # Ensure the first leg is within the +/- 5 range
    if abs(abs(first_leg['delta']) - 50) > 5:
        print(f"No option found with delta within +/- 5 of 50. closest was {first_leg['delta']}.")
        return None, None

    # 2. Find the second leg
    second_leg = None
    first_leg_delta = abs(first_leg['delta'])
    
    if first_leg['option_type'] == 'CE':
        # Find a put with delta within +/- 4 of the first leg's delta
        candidates = [p for p in puts if abs(abs(p['delta']) - first_leg_delta) <= 4]
        if candidates:
            second_leg = min(candidates, key=lambda x: abs(abs(x['delta']) - first_leg_delta))
    else:  # First leg is a Put
        # Find a call with delta within +/- 4 of the first leg's delta
        candidates = [c for c in calls if abs(abs(c['delta']) - first_leg_delta) <= 4]
        if candidates:
            second_leg = min(candidates, key=lambda x: abs(abs(x['delta']) - first_leg_delta))

    if not second_leg:
        print(f"Could not find a second leg to match the first leg (delta: {first_leg_delta}).")
        return None, None
        
    return first_leg, second_leg

def get_current_entry_criteria(symbol):
    try:
        df_5min = fetch_market_data(symbol=symbol, interval="5m")
        df_15min = fetch_market_data(symbol=symbol, interval="15m")
        df_vix = fetch_vix_data(symbol=symbol)

        # Fix 5m and vix as usual
        df_5min = emergency_column_fix(df_5min, "5min_market")
        df_vix = emergency_column_fix(df_vix, "vix")

        # Recalculate indicators for 15m
        df_15min.columns = [col.lower() for col in df_15min.columns]
        df_15min = add_donchian_channel(df_15min)
        df_15min = add_awesome_oscillator(df_15min)
        df_15min = emergency_column_fix(df_15min, "15min_market")

        latest_5min = df_5min.iloc[-1]
        latest_15min = df_15min.iloc[-1]
        latest_vix = df_vix.iloc[-1]

        entry_criteria = {
            "5m_donchian": 1 if latest_5min["close"] > latest_5min["donchian_mid_5m"] else -1,
            "15m_donchian": 1 if latest_15min["close"] > latest_15min["donchian_mid_15m"] else -1,
            "5m_ao": 1 if latest_5min["ao_value"] > 0 else -1,
            "15m_ao": 1 if latest_15min["ao_value"] > 0 else -1,
            "vix_donchian": 1 if latest_vix["donchian_mid_vix"] > latest_vix["close"] else -1,
        }

        return entry_criteria

    except Exception as e:
        paper_logger.error(f"Error in get_current_entry_criteria: {e}")
        return None


if __name__ == "__main__":
    donchian_ao_strategy()