import pandas as pd
import logging
from strategies.indicators import add_donchian_channel, add_awesome_oscillator
from utils.db_func import (
    fetch_market_data, 
    fetch_vix_data, 
    calculate_and_store_high_accuracy_delta, 
    fetch_latest_delta_data
)

logging.basicConfig(filename='logs/trade_actions.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
CUTOFF_TIME = pd.to_datetime("13:00").time()

def donchian_ao_strategy(symbol="NIFTY50"):
    # 1. Fetch Data from database
    print(f"[DEBUG] Running strategy for symbol : {symbol}")
    if not symbol:
        symbol = "NIFTY50"

    print(f"[DEBUG] fetch_market_data called eith symbol : {symbol}")
    df_5min = fetch_market_data(symbol=symbol, interval="5m")
    df_15min = fetch_market_data(symbol=symbol, interval="15m")
    vix_data = fetch_vix_data(symbol=symbol) # Fetches from vix_data table

    if df_5min.empty or df_15min.empty or vix_data.empty:
        logging.warning("Data not available for one or more timeframes. Skipping strategy.")
        return None

    # 2. Calculate Indicators
    df_5min = add_donchian_channel(df_5min, period=20, suffix="_5m")
    df_5min = add_awesome_oscillator(df_5min)
    
    df_15min = add_donchian_channel(df_15min, period=20, suffix="_15m")
    df_15min = add_awesome_oscillator(df_15min)
    
    vix_data = add_donchian_channel(vix_data, period=20, suffix="_vix")
    
    # Get the latest data point
    latest_5min = df_5min.iloc[-1]
    latest_15min = df_15min.iloc[-1]
    latest_vix = vix_data.iloc[-1]

    # --- Conditions to Avoid Taking a Position ---
    
    # 1. VIX Condition
    if latest_vix['close'] > latest_vix['donchian_mid_vix']:
        logging.info("VIX is too high. No positions will be taken.")
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
    #Ask chatgpt-man I'm not sure how this works(something like constructor loop)
    positive_essentials = sum(1 for value in essentials.values() if value == 1)

    # --- Trade Decision Logic ---
    
    # Avoid trade if all essentials are in the same direction
    if positive_essentials == 4 or positive_essentials == 0:
        logging.info("All essentials are in the same direction. No trade.")
        return None

    # Take a trade if 1 or 2 essentials are positive
    if positive_essentials == 1 or positive_essentials == 2 :
        # Check for the specific condition to sell ATM Call and Put
        if positive_essentials ==1 and essentials['5m_donchian'] == 1:
            logging.info("SELL signal: 5m'close is above 5m mid Donchian, and all other essentials are negative.")
            
            # 1. Calculate and store latest deltas
            calculate_and_store_high_accuracy_delta(symbol=symbol)
            
            # 2. Fetch options data with deltas
            options_data = fetch_latest_delta_data(symbol=symbol)
            if not options_data:
                logging.info("No options data with delta available to place a trade.")
                return None

            first_leg, second_leg = select_options_for_trade(options_data)

            if first_leg and second_leg:
                logging.info(f"Selected pair for trade: {first_leg['tradingsymbol']} (Delta: {first_leg['delta']}) and {second_leg['tradingsymbol']} (Delta: {second_leg['delta']})")
                return first_leg, second_leg
            else:
                logging.warning("Could not select a suitable pair of options for the trade.")
                return None

        
        elif positive_essentials == 2 and (essentials['5m_donchian'] == 1 and essentials['5m_ao'] == 1):
            logging.info("SELL Signal: 5m'close is above mid-donchian and 5m AO is positive")

            #1.Calculate and store deltas 
            calculate_and_store_high_accuracy_delta(symbol=symbol)  
            #2.Fetch options data with deltas
            options_data = fetch_latest_delta_data(symbol=symbol)

            #Hybrid filter of PE/CE 
            pe_candidates = sorted(
                [opt for opt in options_data if opt['option_type'] == 'PE' and 30<= abs(opt['delta']) <= 50], 
                key=lambda x: (-x['ltp'], abs(abs(x['delta']) - 40))
            )
            
            ce_candidates = sorted(
                [opt for opt in options_data if opt['option_type'] == 'CE' and 30<= abs(opt['delta']) <= 50], 
                key=lambda x: (x['ltp'], abs(abs(x['delta']) - 40 ))
            )

            for pe in pe_candidates:
                for ce in ce_candidates:
                    if (pe['ltp'] - ce['ltp']) > 18:
                        logging.info(f"Selected PE: {pe['tradingsymbol']} ({pe['ltp']} | {pe['delta']}), CE: {ce['tradingsymbol']} ({ce['ltp']} | {ce['delta']})")
                        return pe, ce
        
        else:
            logging.info("Trade condition met, but not the specific one for selling ATM options.")
            return None

    return

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

    # 1. Find the option with delta'closest to 50
    all_options = calls + puts
    first_leg = min(all_options, key=lambda x: abs(abs(x['delta']) - 50))

    # Ensure the first leg is within the +/- 5 range
    if abs(abs(first_leg['delta']) - 50) > 5:
        print(f"No option found with delta within +/- 5 of 50.'closest was {first_leg['delta']}.")
        return None, None

    # 2. Find the second leg
    second_leg = None
    first_leg_delta = abs(first_leg['delta'])
    
    if first_leg['option_type'] == 'CE':
        # Find a put with delta within +/- 4 of the first leg's delta
        candidates = [p for p in puts if abs(abs(p['delta']) - first_leg_delta) <= 4]
        if candidates:
            second_leg = min(candidates, key=lambda x: abs(abs(x['delta']) - first_leg_delta))
    else: # First leg is a Put
        # Find a call with delta within +/- 4 of the first leg's delta
        candidates = [c for c in calls if abs(abs(c['delta']) - first_leg_delta) <= 4]
        if candidates:
            second_leg = min(candidates, key=lambda x: abs(abs(x['delta']) - first_leg_delta))

    if not second_leg:
        print(f"Could not find a second leg to match the first leg (delta: {first_leg_delta}).")
        return None, None
        
    return first_leg, second_leg

def get_current_entry_criteria(symbol="NIFTY50"):
    """
    Extract current market conditin for entry criteria validation
    This function duplicates the logic from donchian_ao_strategy to get current conditions
    """
    if not symbol:
        symbol = "NIFTY50"

    try:
        #Fetch the same data as in the main strategy
        df_5min = fetch_market_data(symbol=symbol, interval="5m")
        df_15min = fetch_market_data(symbol=symbol, interval="15m")
        vix_data = fetch_vix_data(symbol="VIX")

        if df_5min.empty or df_15min.empty or vix_data.empty:
            return None
        
        # Calculate indicators
        df_5min = add_donchian_channel(df_5min, period=20, suffix="_5m")
        df_5min = add_awesome_oscillator(df_5min)
        
        df_15min = add_donchian_channel(df_15min, period=20, suffix="_15m")
        df_15min = add_awesome_oscillator(df_15min)
        
        vix_data = add_donchian_channel(vix_data, period=20, suffix="_vix")
        
        # Get latest values
        latest_5min = df_5min.iloc[-1]
        latest_15min = df_15min.iloc[-1]
        latest_vix = vix_data.iloc[-1]

        # Return the same essentials structure used in the main strategy
        essentials = {
            '5m_donchian': 1 if latest_5min['close'] > latest_5min['donchian_mid_5m'] else -1,
            '5m_ao': 1 if latest_5min['ao_value'] > 0 else -1,
            '15m_donchian': 1 if latest_15min['close'] > latest_15min['donchian_mid_15m'] else -1,
            '15m_ao': 1 if latest_15min['ao_value'] > 0 else -1,
            'vix_condition': 1 if latest_vix['close'] <= latest_vix['donchian_mid_vix'] else -1
        }
        
        return essentials
        
    except Exception as e:
        logging.error(f"Error getting current entry criteria: {e}")
        return None

if __name__ == "__main__":
    donchian_ao_strategy()