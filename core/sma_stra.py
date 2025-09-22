import pandas as pd
from core.indicators import compute_smas, add_donchian_channel, add_awesome_oscillator
from utils.db_func import fetch_market_data, fetch_latest_delta_data
from utils.utility import setup_paper_trading_logger
from core.strat_donchian import emergency_column_fix
from datetime import datetime

# Initialize logger
paper_logger, trade_logger, position_logger, sma_logger = setup_paper_trading_logger()

START_TIME = pd.to_datetime("09:15:00").time()

# GLOBAL SIGNAL TRACKING
last_signal_time = None
last_signal_options = None
signal_cooldown = 300  # 5 minutes in seconds

def should_generate_signal():
    """Check if enough time has passed since last signal"""
    global last_signal_time, signal_cooldown
    
    current_time = datetime.now()
    
    if last_signal_time is None:
        return True
        
    time_diff = (current_time - last_signal_time).total_seconds()
    
    if time_diff < signal_cooldown:
        sma_logger.debug(f"Signal cooldown active: {signal_cooldown - time_diff:.0f}s remaining")
        return False
        
    return True

def is_duplicate_signal(buy_ce, sell_ce):
    """Check if this signal is identical to the last one"""
    global last_signal_options
    
    if last_signal_options is None:
        return False
    
    last_buy, last_sell = last_signal_options
    
    # Check if same options
    same_signal = (
        buy_ce['tradingsymbol'] == last_buy['tradingsymbol'] and
        sell_ce['tradingsymbol'] == last_sell['tradingsymbol'] 
    )
    
    return same_signal

def update_signal_tracking(buy_ce, sell_ce):
    """Update global signal tracking variables"""
    global last_signal_time, last_signal_options
    
    last_signal_time = datetime.now()
    last_signal_options = (buy_ce, sell_ce)
    
    sma_logger.info(f"Signal tracking updated: BUY {buy_ce['tradingsymbol']} + SELL {sell_ce['tradingsymbol']}")

def get_current_spot_price(df_1m):
    """Get current spot price from 1-minute data"""
    return df_1m.iloc[-1]['close']

def select_itm_atm_options(options_data, spot_price):
    """
    Select ITM CE (buy) and ATM CE (sell) based on strategy requirements
    ITM: spot - 140 ± 20 (range: -120 to -160 from spot)
    ATM: spot + 40 ± 20 (range: +20 to +60 from spot)
    """
    ce_options = [opt for opt in options_data if opt.get("option_type") == "CE" or opt.get("option_type") == "ce"]
    
    if not ce_options:
        sma_logger.error("No CE options available")
        return None, None
    
    # Calculate target strikes
    itm_target = spot_price - 140
    atm_target = spot_price + 40
    
    # Find ITM option (closest to spot-140, within ±20 range)
    itm_candidates = []
    for opt in ce_options:
        try:
            strike = float(opt.get('strike', 0))
            if abs(strike - itm_target) <= 20:  # Within ±20 range
                itm_candidates.append((opt, abs(strike - itm_target)))
        except (ValueError, TypeError):
            continue
    
    if not itm_candidates:
        sma_logger.error(f"No ITM CE found within range for target {itm_target}")
        return None, None
    
    # Select closest ITM option
    buy_ce = min(itm_candidates, key=lambda x: x[1])[0]
    
    # Find ATM option (closest to spot+40, within ±20 range)
    atm_candidates = []
    for opt in ce_options:
        try:
            strike = float(opt.get('strike', 0))
            if abs(strike - atm_target) <= 20:  # Within ±20 range
                atm_candidates.append((opt, abs(strike - atm_target)))
        except (ValueError, TypeError):
            continue
    
    if not atm_candidates:
        sma_logger.error(f"No ATM CE found within range for target {atm_target}")
        return None, None
    
    # Select closest ATM option
    sell_ce = min(atm_candidates, key=lambda x: x[1])[0]
    
    sma_logger.info(f"Selected ITM CE (BUY): {buy_ce['tradingsymbol']} @ {buy_ce.get('last_price', 0)}")
    sma_logger.info(f"Selected ATM CE (SELL): {sell_ce['tradingsymbol']} @ {sell_ce.get('last_price', 0)}")
    
    return buy_ce, sell_ce

def get_current_entry_criteria(symbol):
    """Get current SMA entry criteria for position validation"""
    try:
        # Fetch data
        df_15m = fetch_market_data(symbol, interval="15m")
        df_5m = fetch_market_data(symbol, interval="5m")
        df_1m = fetch_market_data(symbol, interval="1m")

        if df_15m is None or df_5m is None or df_1m is None:
            return None

        # Prepare data
        df_15m.columns = [col.lower() for col in df_15m.columns]
        df_5m.columns = [col.lower() for col in df_5m.columns]
        df_1m.columns = [col.lower() for col in df_1m.columns]

        # Compute indicators
        df_15m = compute_smas(df_15m)
        df_15m = add_donchian_channel(df_15m)
        df_15m = add_awesome_oscillator(df_15m)
        
        df_5m = compute_smas(df_5m)
        df_5m = add_donchian_channel(df_5m)
        df_5m = add_awesome_oscillator(df_5m)   
        
        df_1m = add_donchian_channel(df_1m)
        df_1m = add_awesome_oscillator(df_1m)
        df_1m = compute_smas(df_1m)

        # Get latest data
        latest_15m = df_15m.iloc[-1]
        latest_5m = df_5m.iloc[-1]
        latest_1m = df_1m.iloc[-1]

        # Entry criteria based on SMA strategy
        entry_criteria = {
            '5m_donchian': 1 if latest_5m['close'] > latest_5m['donchian_mid_5m'] else -1,
            '5m_ao': 1 if latest_5m['ao_value'] > 0 else -1,
            '15m_donchian': 1 if latest_15m['close'] > latest_15m['donchian_mid_15m'] else -1,
            '15m_ao': 1 if latest_15m['ao_value'] > 0 else -1,
            '1m_sma_5': 1 if latest_1m['close'] > latest_1m['sma_5'] else -1,
            '1m_sma_20': 1 if latest_1m['close'] > latest_1m['sma_20'] else -1,
        }

        return entry_criteria

    except Exception as e:
        sma_logger.error(f"Error in get_current_entry_criteria: {e}")
        return None

def sma_strategy(symbol="NIFTY50"):
    """
    Main Strategy Function for SMA strategy with proper signal generation
    """
    # CHECK SIGNAL COOLDOWN FIRST
    if not should_generate_signal():
        sma_logger.debug("Strategy called but signal cooldown is active")
        return None

    current_time = datetime.now().time()
    if current_time < START_TIME:
        sma_logger.info("Market is open but time is before strategy start time. Exiting.")
        return None
    
    else:
        sma_logger.info(f"Market is open. Starting strategy for {symbol}.")

        try:
            sma_logger.debug(f"Starting SMA strategy for {symbol}") 

            # Fetch historical market data
            df_15m = fetch_market_data(symbol, interval="15m")
            df_5m = fetch_market_data(symbol, interval="5m")
            df_1m = fetch_market_data(symbol, interval="1m")

            if df_15m is None or df_5m is None or df_1m is None:
                sma_logger.error(f"Failed to fetch market data for {symbol}. Exiting strategy.")
                return None
            
            sma_logger.debug(f"Fetched market data for {symbol}")
            
            # Compute indicators for 15m,5m and 1m dataframes
            df_15m.columns = [col.lower() for col in df_15m.columns]
            df_5m.columns = [col.lower() for col in df_5m.columns]
            df_1m.columns = [col.lower() for col in df_1m.columns]

            df_15m = compute_smas(df_15m)
            df_15m = add_donchian_channel(df_15m)
            df_15m = add_awesome_oscillator(df_15m)
            
            df_5m = compute_smas(df_5m)
            df_5m = add_donchian_channel(df_5m)
            df_5m = add_awesome_oscillator(df_5m)   
            
            df_1m = add_donchian_channel(df_1m)
            df_1m = add_awesome_oscillator(df_1m)
            df_1m = compute_smas(df_1m)

            # CRITICAL : Basic data validation
            basic_cols = ['open', 'high', 'low', 'close']
            for col in basic_cols:
                if col not in df_15m.columns or col not in df_5m.columns or col not in df_1m.columns:
                    sma_logger.error(f"Missing critical column '{col}' in one of the dataframes. Exiting strategy.")
                    return None
                
            sma_logger.debug(f"Computed indicators for {symbol}")
            
            # Get the latest data points
            latest_15m = df_15m.iloc[-1]
            latest_5m = df_5m.iloc[-1]  
            latest_1m = df_1m.iloc[-1]

            # ---- 4 Essentials for Trade Condition ---- 
            essentials = {
                '5m_donchian': 1 if latest_5m['close'] > latest_5m['donchian_mid'] else -1,
                '5m_ao': 1 if latest_5m['ao_value'] > 0 else -1,
                '15m_donchian': 1 if latest_15m['close'] > latest_15m['donchian_mid'] else -1,
                '15m_ao': 1 if latest_15m['ao_value'] > 0 else -1,
            }
        
            positive_essentials = sum(1 for value in essentials.values() if value == 1)

            # ---- Trade Decision Logic ---- #
            # All 4 essentials must be in same direction (all positive or all negative)
            if positive_essentials == 4 or positive_essentials == 0:
                sma_logger.info(f"All 4 essentials in same direction. Proceeding with further checks...")
                
                # Check Flag 1: 5min and 15min conditions
                flag1_5m = (latest_5m['close'] > latest_5m['donchian_mid'] or 
                           latest_5m['close'] > latest_5m['sma_20'])
                flag1_15m = (latest_15m['close'] > latest_15m['donchian_mid'] or 
                            latest_15m['close'] > latest_15m['sma_20'])
                
                if flag1_5m and flag1_15m:
                    sma_logger.info("Flag 1 conditions met (5m and 15m checks passed)")
                    
                    # Check Flag 2: 1m price touching SMA 20 or mid donchian
                    tol = 1.0000005  # Tolerance level to avoid floating point issues
                    close = latest_1m['close']

                    if abs(close - latest_1m['sma_20']) <= tol or abs(close - latest_1m['donchian_mid_1m']) <= tol:
                        sma_logger.info("Flag 2 conditions met (1m price touching SMA 20 or mid donchian)")
                        
                        # Final condition: price above both 5 SMA and 20 SMA on 1m
                        if (latest_1m['close'] > latest_1m['sma_5']) and (latest_1m['close'] > latest_1m['sma_20']):
                            sma_logger.info("ALL conditions met for position taking. Generating trade signal...")
                            
                            # Get current spot price and fetch options data
                            spot_price = get_current_spot_price(df_1m)
                            options_data = fetch_latest_delta_data(symbol)
                            
                            if not options_data:
                                sma_logger.error("No options data available")
                                return None
                            
                            # Select ITM (buy) and ATM (sell) CE options
                            buy_ce, sell_ce = select_itm_atm_options(options_data, spot_price)
                            
                            if buy_ce and sell_ce:
                                # Check for duplicate signal
                                if is_duplicate_signal(buy_ce, sell_ce):
                                    sma_logger.info(f"Duplicate signal detected, skipping")
                                    return None
                                
                                # Standardize option data for paper trader
                                buy_ce['last_price'] = buy_ce.get('ltp', buy_ce.get('last_price', 0))
                                sell_ce['last_price'] = sell_ce.get('ltp', sell_ce.get('last_price', 0))
                                
                                # Update signal tracking
                                update_signal_tracking(buy_ce, sell_ce)
                                
                                # Return signal in format expected by paper trader
                                # Using ce_option for buy (ITM) and pe_option for sell (ATM) to reuse existing logic
                                return {"ce_option": buy_ce, "pe_option": sell_ce, "strategy_type": "sma_spread"}
                            else:
                                sma_logger.error("Failed to select appropriate options")
                                return None
                        else:
                            sma_logger.info("Final SMA conditions not met on 1m chart")
                            return None
                    else:
                        sma_logger.info("Flag 2 conditions not met (1m price not touching SMA 20 or mid donchian)")
                        return None
                else:
                    sma_logger.info("Flag 1 conditions not met")
                    return None
            else:
                sma_logger.info(f"Essentials not in same direction ({positive_essentials}/4 positive). No trade.")
                return None

        except Exception as e:
            sma_logger.error(f"Error in SMA strategy for {symbol}: {e}")
            return None
