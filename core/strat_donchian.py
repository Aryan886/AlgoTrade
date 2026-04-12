import pandas as pd
from core.indicators import add_donchian_channel, add_awesome_oscillator
from utils.db_func import (
    fetch_market_data, 
    fetch_vix_data, 
    calculate_and_store_high_accuracy_delta, 
    fetch_latest_delta_data
)
from utils.utility import setup_paper_trading_logger
from datetime import datetime

# Initialize loggers
paper_logger, trade_logger, position_logger, sma_logger, equity_logger, _nifty_logger = setup_paper_trading_logger()

CUTOFF_TIME = pd.to_datetime("13:00").time()

# GLOBAL SIGNAL TRACKING
last_signal_time = None
last_signal_options = None
signal_cooldown = 300  # 5 minutes in seconds

def get_delta_limit_by_day():
    """Get delta limit based on current day of week"""
    current_day = datetime.now().weekday() #Monday = 0 Sunday = 6

    delta_config = {
        0: 50, #Monday
        1: 40, #Tuesday
        2: 40, #Wednesday
        3: 50, #Thursday (temp)
        4: 50, #Friday
        5: 40, #Saturday (just using)
        6: 40, #Sunday
    }

    return delta_config[current_day]

def emergency_column_fix(df, data_type="market"):
    """Emergency fix for column name case issues and indicator mapping"""
    if df.empty:
        return df
    
    # Create a copy to avoid modifying original
    df = df.copy()
    
    #print(f"[DEBUG] {data_type} data columns before fix: {list(df.columns)}")
    
    # Handle VIX data with pre-calculated indicators
    if data_type.lower() == "vix":
        # VIX data has pre-calculated indicators, just map them properly
        if 'vix_value' in df.columns:
            df['close'] = df['vix_value']  # lowercase
            df['high'] = df['vix_value']   # lowercase
            df['low'] = df['vix_value']    # lowercase
            #print(f"[VIX FIX] Mapped vix_value to OHLC columns")
        
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
                #print(f"[VIX FIX] Mapped {old_col} -> {new_col}")
        
        #print(f"[DEBUG] VIX data columns after fix: {list(df.columns)}")
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
        #print(f"[INDICATOR FIX] Mapped donchian_upper -> donchian_upper{suffix}")
        mapped = True
    else:
        print(f"[WARNING] Column 'donchian_upper' missing in {data_type} data")

    if 'donchian_lower' in df.columns:
        df[f'donchian_lower{suffix}'] = df['donchian_lower']
        #print(f"[INDICATOR FIX] Mapped donchian_lower -> donchian_lower{suffix}")
        mapped = True
    else:
        print(f"[WARNING] Column 'donchian_lower' missing in {data_type} data")

    if 'donchian_mid' in df.columns:
        df[f'donchian_mid{suffix}'] = df['donchian_mid']
        #print(f"[INDICATOR FIX] Mapped donchian_mid -> donchian_mid{suffix}")
        mapped = True
    else:
        print(f"[WARNING] Column 'donchian_mid' missing in {data_type} data")

    if not mapped:
        print(f"[ERROR] None of the Donchian columns found in {data_type} data")
    
    if 'ao_value' in df.columns:
        # AO doesn't need suffix, it's used as-is
       #print(f"[INDICATOR FIX] AO value already correctly named")
       pass
    
    #print(f"[DEBUG] Columns in {data_type} after fix: {list(df.columns)}")
    
    return df

def should_generate_signal():
    """Check if enough time has passed since last signal"""
    global last_signal_time, signal_cooldown
    
    current_time = datetime.now()
    
    if last_signal_time is None:
        return True
        
    time_diff = (current_time - last_signal_time).total_seconds()
    
    if time_diff < signal_cooldown:
        paper_logger.debug(f"Signal cooldown active: {signal_cooldown - time_diff:.0f}s remaining")
        return False
        
    return True

def is_duplicate_signal(first_leg, second_leg):
    """Check if this signal is identical to the last one"""
    global last_signal_options
    
    if last_signal_options is None:
        return False
    
    last_first, last_second = last_signal_options
    
    # Check if same options
    same_signal = (
        first_leg['tradingsymbol'] == last_first['tradingsymbol'] and
        second_leg['tradingsymbol'] == last_second['tradingsymbol'] 
    )
    
    return same_signal

def update_signal_tracking(first_leg, second_leg):
    """Update global signal tracking variables"""
    global last_signal_time, last_signal_options
    
    last_signal_time = datetime.now()
    last_signal_options = (first_leg, second_leg)
    
    paper_logger.info(f"Signal tracking updated: {first_leg['tradingsymbol']} + {second_leg['tradingsymbol']}")

def debug_vix_data(symbol="NIFTY50"):
    """Debug function to inspect VIX data structure"""
    print(f"[DEBUG] Inspecting VIX data for symbol: {symbol}")
    
    try:
        vix_data = fetch_vix_data(symbol=symbol)
        
        if vix_data.empty:
            print("[DEBUG] VIX data is empty!")
            return
            
        print(f"[DEBUG] VIX data shape: {vix_data.shape}")
       # print(f"[DEBUG] VIX data columns: {list(vix_data.columns)}")
        #print(f"[DEBUG] VIX data types:\n{vix_data.dtypes}")
        #print(f"[DEBUG] VIX data sample:\n{vix_data.head()}")
        
        # Check for numeric columns
        numeric_cols = vix_data.select_dtypes(include=['number']).columns
        print(f"[DEBUG] Numeric columns: {list(numeric_cols)}")
        
        return vix_data
        
    except Exception as e:
        print(f"[DEBUG ERROR] Error fetching VIX data: {e}")
        return None

def donchian_ao_strategy(symbol="NIFTY50"):
    """Main strategy function with proper error handling and signal tracking"""
    
    print(f"[DEBUG] Running strategy for symbol: {symbol}")
    if not symbol:
        symbol = "NIFTY50"

    # CHECK SIGNAL COOLDOWN FIRST (ADD THIS)
    if not should_generate_signal():
        paper_logger.debug("Strategy called but signal cooldown is active")
        return None

    try:
        # 1. Fetch Data from database
        print(f"[DEBUG] Fetching market data for symbol: {symbol}")
        df_5min = fetch_market_data(symbol=symbol, interval="5m", limit=None)
        df_15min = fetch_market_data(symbol=symbol, interval="15m", limit= None)
        vix_data = fetch_vix_data(symbol=symbol)

        # Calculate indicators for 15m only
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
            vix_data = emergency_column_fix(vix_data, "VIX")
           # print("[EMERGENCY FIX] Column fixes applied successfully")
           # print("[DEBUG] Checking existence of required 15m columns...")
            #print("Available columns in df_15min:", list(df_15min.columns))

            if 'donchian_mid_15m' not in df_15min.columns:
                raise Exception("[FATAL] donchian_mid_15m missing from df_15min after emergency fix!")

        except Exception as e:
            print(f"[EMERGENCY FIX ERROR] {e}")
            return None

        # CRITICAL: Basic data validation
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

        # Get the latest data point
        latest_5min = df_5min.iloc[-1]
        latest_15min = df_15min.iloc[-1]
        latest_vix = vix_data.iloc[-1]

        # --- Conditions to Avoid Taking a Position ---
        
        # 1. VIX Condition
        if latest_vix['close'] > latest_vix['donchian_mid_vix']:
            paper_logger.info("VIX is above mid-donchian. No positions will be taken.")
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
        
        positive_essentials = sum(1 for value in essentials.values() if value == 1)

        # --- Trade Decision Logic --- #
        
        # Checking for 3:1 ratio (3 positive + 1 negative OR 3 negative + 1 positive)
        if positive_essentials == 4 or positive_essentials == 0:
            paper_logger.info("All essentials in same direction. No Trade... ")
            return None
        
        delta_limit = get_delta_limit_by_day()
        paper_logger.info(f"Using delta limit : {delta_limit} for today..")

        # Fetching necessary deltas
        options_data = fetch_latest_delta_data(symbol=symbol)
        if not options_data:
            calculate_and_store_high_accuracy_delta(symbol=symbol)
            options_data = fetch_latest_delta_data(symbol=symbol)

        if not options_data:
            paper_logger.info("No options data with delta available..")
            return None
        
        # EXECUTE TRADE LOGIC
        trade_result = None
        
        if positive_essentials == 3:
            # Majority positive: Sell PE first, then CE
            paper_logger.info("3:1 Ratio - Majority positive: PE first CE next route opted... ")
            trade_result = execute_trade(options_data, delta_limit, "PE_FIRST")
        
        elif positive_essentials == 2:
            paper_logger.info("2:2 Ratio detected: Selling CE first then PE")
            trade_result = execute_trade(options_data, delta_limit, "CE_FIRST")

        else: # positive essentials = 1
            # Majority Negative : Sell CE first, then PE
            paper_logger.info("3:1 - Majority negative : Selling CE first,then PE ")
            trade_result = execute_trade(options_data, delta_limit, "CE_FIRST")
        
        # CHECK FOR VALID TRADE RESULT
        if trade_result and len(trade_result) == 2:
            first_leg, second_leg = trade_result
            
            if first_leg and second_leg:
                # Check for duplicate signal
                if is_duplicate_signal(first_leg, second_leg):
                    paper_logger.info(f"Duplicate signal detected, skipping: {first_leg['tradingsymbol']} + {second_leg['tradingsymbol']}")
                    return None
                
               # Format for execute_paper_trade (expects dict with ce_option/pe_option keys)
                if first_leg['option_type'] == 'CE':
                    return {"ce_option": first_leg, "pe_option": second_leg}
                else:
                    return {"ce_option": second_leg, "pe_option": first_leg}
            else:
                paper_logger.warning("Trade execution returned None for one or both legs")
                return None
        else:
            paper_logger.warning(f"Invalid trade result: {trade_result}")
            return None

    except Exception as e:
        paper_logger.error(f"Error in donchian_ao_strategy: {e}")
        return None

def execute_trade(options_data, delta_limit, trade_type):
    """
    Execute PE/CE trade based on 3:1 ratio logic
    trade_type : PE_FIRST or CE_FIRST
    """
    # Separate options by type
    pe_options = [opt for opt in options_data if opt["option_type"] == "PE" and abs(opt['delta']) < delta_limit]
    ce_options = [opt for opt in options_data if opt["option_type"] == "CE" and abs(opt['delta']) < delta_limit]
    
    """
    print(f"[DEBUG] Found {len(pe_options)} PE options, {len(ce_options)} CE options")
    print(f"[DEBUG] PE options: {[opt['tradingsymbol'] + ' (' + opt['option_type'] + ')' for opt in pe_options[:3]]}")
    print(f"[DEBUG] CE options: {[opt['tradingsymbol'] + ' (' + opt['option_type'] + ')' for opt in ce_options[:3]]}")
    """

    if not pe_options or not ce_options:
        print(f"Insufficient options data with delta < {delta_limit}")
        return None  # FIX: Changed from (None, None) to None
    
    if trade_type == "PE_FIRST":
        # Sell the most expensive one first
        pe_options.sort(key=lambda x : x['ltp'], reverse=True)
        first_leg = pe_options[0]

        # Find CE with LTP lower than selected PE option
        ce_candidates = [ce for ce in ce_options if ce['ltp'] < first_leg['ltp']]
        if not ce_candidates:
            print(f"No CE found with LTP lower than PE LTP : {first_leg['ltp']}")
            return None  # FIX: Changed from (None, None) to None
        
        # Sort CE by highest LTP among candidates (but still lower than PE)
        ce_candidates.sort(key=lambda x: x['ltp'], reverse=True)
        second_leg = ce_candidates[0]

    else: # CE_FIRST
        ce_options.sort(key=lambda x: x['ltp'], reverse=True)
        first_leg = ce_options[0]

        # Find PE with LTP lower than CE
        pe_candidates = [pe for pe in pe_options if pe['ltp'] < first_leg['ltp']]
        if not pe_candidates:
            print(f"No PE found with LTP lower than CE LTP : {first_leg['ltp']}")
            return None  # FIX: Changed from (None, None) to None

        pe_candidates.sort(key=lambda x:x['ltp'], reverse=True)
        second_leg = pe_candidates[0]

    paper_logger.info(f"Selected {trade_type}: First Leg : {first_leg['tradingsymbol']} (LTP: {first_leg['ltp']}, Delta: {first_leg['delta']})")
    paper_logger.info(f"Selected {trade_type}: Second leg: {second_leg['tradingsymbol']} (LTP: {second_leg['ltp']}, Delta: {second_leg['delta']})")

    # Standardize keys for paper trader compatibility
    first_leg['symbol'] = first_leg['tradingsymbol'] 
    second_leg['symbol'] = second_leg['tradingsymbol']
    first_leg['last_price'] = first_leg['ltp']
    second_leg['last_price'] = second_leg['ltp']
    
    print(f"[DEBUG] Selected first_leg: {first_leg['tradingsymbol']} ({first_leg['option_type']})")
    print(f"[DEBUG] Selected second_leg: {second_leg['tradingsymbol']} ({second_leg['option_type']})")

    return (first_leg, second_leg)  # FIX: Ensure tuple return

def select_options_for_trade(options_data):
    """
    Selects one ATM call and one ATM put based on delta rules.
    """
    # Separate calls and puts
    calls = [opt for opt in options_data if opt['option_type'] == 'CE']
    puts = [opt for opt in options_data if opt['option_type'] == 'PE']

    if not calls or not puts:
        print("Not enough options data to select a pair.")
        return None

    # 1. Find the option with delta closest to 50
    all_options = calls + puts
    first_leg = min(all_options, key=lambda x: abs(abs(x['delta']) - 50))

    # Ensure the first leg is within the +/- 5 range
    if abs(abs(first_leg['delta']) - 50) > 5:
        print(f"No option found with delta within +/- 5 of 50. closest was {first_leg['delta']}.")
        return None

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
        return None
        
    return (first_leg, second_leg)

def get_current_entry_criteria(symbol):
    """Get current entry criteria for position validation"""
    try:
        df_5min = fetch_market_data(symbol=symbol, interval="5m", limit=None)
        df_15min = fetch_market_data(symbol=symbol, interval="15m", limit=None)
        df_vix = fetch_vix_data(symbol=symbol)

        # Fix 5m and vix as usual
        df_5min = emergency_column_fix(df_5min, "5min_market")
        df_vix = emergency_column_fix(df_vix, "vix")

        # Recalculate indicators for 15m
        df_15min.columns = [col.lower() for col in df_15min.columns]
        df_15min = add_donchian_channel(df_15min, suffix="_15m")
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

#THIS FUNCTION FOR MANUAL SIGNAL RESET
def reset_signal_tracking():
    """Reset signal tracking - useful for testing or manual intervention"""
    global last_signal_time, last_signal_options
    last_signal_time = None
    last_signal_options = None
    paper_logger.info("Signal tracking reset manually")

if __name__ == "__main__":
    donchian_ao_strategy()