import pandas as pd
from core.indicators import compute_smas, add_donchian_channel, add_awesome_oscillator
from utils.db_func import fetch_market_data
from utils.utility import setup_paper_trading_logger, emergency_column_fix
from datetime import datetime

#Initialize logger
paper_logger, trade_logger, position_logger, sma_logger = setup_paper_trading_logger()

START_TIME = pd.to_datetime("09:15:00").time()

def sma_strategy(symbol="NIFTY50"):
    """
    Main Strategy Function for SMA strategy with proper error handling and logging 
    """

    current_time = datetime.now().time()
    if current_time < START_TIME:
        sma_logger.info("Market is open but time is before strategy start time. Exiting.")
        return None
    
    else:
        sma_logger.info(f"Market is open. Starting strategy for {symbol}.")

        try:
            sma_logger.debug(f"Starting SMA strategy for {symbol}") 

            # Fetch historical market data
            df_15m = fetch_market_data(symbol, interval="15min")
            df_5m = fetch_market_data(symbol, interval="5min")
            df_1m = fetch_market_data(symbol, interval="1min")

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

            #CRITICAL : Basic data validation
            basic_cols = ['open', 'high', 'low', 'close',]
            for col in basic_cols:
                if col not in df_15m.columns or col not in df_5m.columns or col not in df_1m.columns:
                    sma_logger.error(f"Missing critical column '{col}' in one of the dataframes. Exiting strategy.")
                    return None
                
            sma_logger.debug(f"Computed indicators for {symbol}")
            
            # Get the latest data points
            latest_15m = df_15m.iloc[-1]
            latest_5m = df_5m.iloc[-1]  
            latest_1m = df_1m.iloc[-1]

            # ---- Conditions for Entry and Exit ----
        
            # 1. Essentials for Trade Condition 
            essentials = {
            '5m_donchian': 1 if latest_5m['close'] > latest_5min['donchian_mid_5m'] else -1,
            '5m_ao': 1 if latest_5m['ao_value'] > 0 else -1,
            '15m_donchian': 1 if latest_15m['close'] > latest_15min['donchian_mid_15m'] else -1,
            '15m_ao': 1 if latest_15m['ao_value'] > 0 else -1,
        }
        
        except Exception as e:
            sma_logger.error(f"Error fetching market data for {symbol}: {e}")
            return
