"""
Equity Trading Strategy - Donchian + AO + VWAP + SMA
Based on equityStrat.txt requirements
"""

import pandas as pd
from typing import Dict, Optional
from datetime import datetime
from utils.db_func import fetch_equity_data, fetch_equity_sma_data
from utils.utility import setup_paper_trading_logger

# Initialize logger
paper_logger, trade_logger, position_logger, sma_logger, equity_logger = setup_paper_trading_logger()
strategy_logger = equity_logger

# Test logging immediately
strategy_logger.info("=== EQUITY STRATEGY MODULE LOADED ===")

def check_15min_conditions(symbol: str) -> bool:
    """
    Check 15min timeframe conditions:
    - Close price above mid-donchian
    - Price above VWAP
    - AO is positive OR price above 20 SMA
    """
    try:
        df_15m = fetch_equity_data(symbol=symbol, interval='15m', limit=50)
        df_sma_15m = fetch_equity_sma_data(symbol=symbol, interval='15m', limit=50)
        
        if df_15m.empty or df_sma_15m.empty:
            return False
        
        # Get latest candle
        latest = df_15m.iloc[-1]
        latest_sma = df_sma_15m.iloc[-1]
        
        # Condition checks
        close = latest['close']
        mid_donchian = latest['donchian_mid']
        vwap = latest['vwap']
        ao_color = latest['ao_color']
        ao_value = latest['ao_value']
        sma_20 = latest_sma['sma_20']
        
        # Validate data
        if pd.isna(close) or pd.isna(mid_donchian) or pd.isna(vwap):
            return False
        
        # Check conditions
        close_above_mid = close > mid_donchian
        price_above_vwap = close > vwap
        ao_positive = ao_value  if pd.notna(ao_value) else False
        price_above_20sma = close > sma_20 if pd.notna(sma_20) else False
        
        # AO positive OR price above 20 SMA
        ao_or_sma = ao_positive or price_above_20sma
        
        # Log condition details
        strategy_logger.debug(f"15min conditions for {symbol}:")
        strategy_logger.debug(f"  Close: {close:.2f}, Mid-Donchian: {mid_donchian:.2f} -> Above Mid: {close_above_mid}")
        strategy_logger.debug(f"  Close: {close:.2f}, VWAP: {vwap:.2f} -> Above VWAP: {price_above_vwap}")
        strategy_logger.debug(f"  AO Value: {ao_value}, Above 20 SMA: {price_above_20sma} -> AO or SMA: {ao_or_sma}")
        
        result = close_above_mid and price_above_vwap and ao_or_sma
        strategy_logger.debug(f"15min conditions result for {symbol}: {result}")
        
        return result
        
    except Exception as e:
        strategy_logger.error(f"Error in check_15min_conditions: {e}")
        return False


def check_5min_conditions(symbol: str) -> bool:
    """
    Check 5min timeframe conditions:
    - Price above mid-donchian
    - Price above VWAP
    - AO is positive
    """
    try:
        df_5m = fetch_equity_data(symbol=symbol, interval='5m', limit=50)
        
        if df_5m.empty:
            return False
        
        # Get latest candle
        latest = df_5m.iloc[-1]
        
        # Condition checks
        close = latest['close']
        mid_donchian = latest['donchian_mid']
        vwap = latest['vwap']
        ao_color = latest['ao_color']
        ao_value = latest['ao_value']
        
        # Validate data
        if pd.isna(close) or pd.isna(mid_donchian) or pd.isna(vwap) or pd.isna(ao_color):
            return False
        
        # Check conditions
        price_above_mid = close > mid_donchian
        price_above_vwap = close > vwap
        ao_positive = ao_value 
        
        # Log condition details
        strategy_logger.debug(f"5min conditions for {symbol}:")
        strategy_logger.debug(f"  Close: {close:.2f}, Mid-Donchian: {mid_donchian:.2f} -> Above Mid: {price_above_mid}")
        strategy_logger.debug(f"  Close: {close:.2f}, VWAP: {vwap:.2f} -> Above VWAP: {price_above_vwap}")
        strategy_logger.debug(f"  AO Value: {ao_value} -> AO Positive: {ao_positive}")
        
        result = price_above_mid and price_above_vwap and ao_positive
        strategy_logger.debug(f"5min conditions result for {symbol}: {result}")
        
        return result
        
    except Exception as e:
        strategy_logger.error(f"Error in check_5min_conditions: {e}")
        return False


def check_1min_entry_condition_a(symbol: str) -> Optional[Dict]:
    """
    1min Entry Condition A:
    Price above VWAP OR price above mid-donchian OR AO is positive OR price above 20 SMA
    Returns entry signal with price if conditions met
    """
    try:
        df_1m = fetch_equity_data(symbol=symbol, interval='1m', limit=50)
        df_sma_1m = fetch_equity_sma_data(symbol=symbol, interval='1m', limit=50)
        
        if df_1m.empty or df_sma_1m.empty:
            return None
        
        # Get latest candle
        latest = df_1m.iloc[-1]
        latest_sma = df_sma_1m.iloc[-1]
        
        close = latest['close']
        mid_donchian = latest['donchian_mid']
        vwap = latest['vwap']
        ao_color = latest['ao_color']
        ao_value = latest['ao_value']
        sma_20 = latest_sma['sma_20']
        
        # Check any condition is true (OR logic)
        price_above_vwap = close > vwap if pd.notna(vwap) else False
        price_above_mid = close > mid_donchian if pd.notna(mid_donchian) else False
        ao_positive = ao_value if pd.notna(ao_color) else False
        price_above_20sma = close > sma_20 if pd.notna(sma_20) else False
        
        if price_above_vwap or price_above_mid or ao_positive or price_above_20sma:
            return {
                'entry_price': close,
                'entry_method': 'condition_a',
                'timestamp': df_1m.index[-1]
            }
        
        return None
        
    except Exception as e:
        strategy_logger.error(f"Error in check_1min_entry_condition_a: {e}")
        return None


def check_1min_entry_condition_b(symbol: str) -> Optional[Dict]:
    """
    1min Entry Condition B:
    When 1min price closes above 5 SMA
    Returns entry signal with price if conditions met
    """
    try:
        df_1m = fetch_equity_data(symbol=symbol, interval='1m', limit=50)
        df_sma_1m = fetch_equity_sma_data(symbol=symbol, interval='1m', limit=50)
        
        if df_1m.empty or df_sma_1m.empty:
            return None
        
        # Get latest candle
        latest = df_1m.iloc[-1]
        latest_sma = df_sma_1m.iloc[-1]
        
        close = latest['close']
        sma_5 = latest_sma['sma_5']
        
        if pd.isna(close) or pd.isna(sma_5):
            return None
        
        if close > sma_5:
            return {
                'entry_price': close,
                'entry_method': 'condition_b',
                'timestamp': df_1m.index[-1]
            }
        
        return None
        
    except Exception as e:
        strategy_logger.error(f"Error in check_1min_entry_condition_b: {e}")
        return None


def calculate_skipping_low_stop_loss(symbol: str, filter_pct: float = 0.0025) -> Optional[float]:
    """
    Calculate skipping low stop loss:
    - Look back at previous 7 candles (including current)
    - Find lowest low
    - Apply filter: stop_loss = lowest_low - (lowest_low * filter_pct)
    """
    try:
        df_1m = fetch_equity_data(symbol=symbol, interval='1m', limit=10)
        
        if len(df_1m) < 7:
            return None
        
        # Get last 7 candles
        last_7_candles = df_1m.iloc[-7:]
        
        # Find lowest low
        lowest_low = last_7_candles['low'].min()
        
        # Apply filter
        #stop_loss = lowest_low - (lowest_low * filter_pct)
        stop_loss  = lowest_low  # No filter as per updated requirement
        
        return stop_loss
        
    except Exception as e:
        strategy_logger.error(f"Error in calculate_skipping_low_stop_loss: {e}")
        return None


def equity_strategy(symbol: str) -> Optional[Dict]:
    """
    Main strategy function - checks all conditions in sequence
    Returns signal dict if all conditions pass, None otherwise
    """
    try:
        # Force immediate logging to ensure it shows up
        strategy_logger.info(f"=== EQUITY STRATEGY CALLED for {symbol} ===")
        strategy_logger.debug(f"Starting strategy check for {symbol}")
        
        # Step 1: Check 15min conditions
        strategy_logger.debug(f"Checking 15min conditions for {symbol}")
        if not check_15min_conditions(symbol):
            strategy_logger.debug(f"15min conditions failed for {symbol}")
            return None
        strategy_logger.info(f"15min conditions PASSED for {symbol}")
        
        # Step 2: Check 5min conditions
        strategy_logger.debug(f"Checking 5min conditions for {symbol}")
        if not check_5min_conditions(symbol):
            strategy_logger.debug(f"5min conditions failed for {symbol}")
            return None
        strategy_logger.info(f"5min conditions PASSED for {symbol}")
        
        # Step 3: Check 1min entry conditions (try condition A first, then B)
        strategy_logger.debug(f"Checking 1min entry conditions for {symbol}")
        entry_signal = check_1min_entry_condition_a(symbol)
        if not entry_signal:
            strategy_logger.debug(f"1min condition A failed, trying condition B for {symbol}")
            entry_signal = check_1min_entry_condition_b(symbol)
        
        if not entry_signal:
            strategy_logger.debug(f"All 1min entry conditions failed for {symbol}")
            return None
        
        strategy_logger.info(f"1min entry condition PASSED for {symbol} - Method: {entry_signal['entry_method']}")
        
        # Step 4: Calculate stop loss
        strategy_logger.debug(f"Calculating stop loss for {symbol}")
        stop_loss = calculate_skipping_low_stop_loss(symbol)
        if stop_loss is None:
            strategy_logger.warning(f"Could not calculate stop loss for {symbol}")
            return None
        
        strategy_logger.info(f"Stop loss calculated for {symbol}: {stop_loss:.2f}")
        
        # Return complete signal
        signal = {
            'symbol': symbol,
            'action': 'BUY',
            'entry_price': entry_signal['entry_price'],
            'stop_loss': stop_loss,
            'entry_method': entry_signal['entry_method'],
            'timestamp': entry_signal['timestamp']
        }
        
        strategy_logger.info(f"STRATEGY SIGNAL GENERATED for {symbol}:")
        strategy_logger.info(f"  Entry Price: {signal['entry_price']:.2f}")
        strategy_logger.info(f"  Stop Loss: {signal['stop_loss']:.2f}")
        strategy_logger.info(f"  Entry Method: {signal['entry_method']}")
        
        return signal
        
    except Exception as e:
        strategy_logger.error(f"Error in equity_donchian_strategy: {e}")
        return None


def get_current_price(symbol: str) -> Optional[float]:
    """Get current price from latest 1min candle"""
    try:
        df_1m = fetch_equity_data(symbol=symbol, interval='1m', limit=1)
        if df_1m.empty:
            return None
        return df_1m.iloc[-1]['close']
    except Exception as e:
        strategy_logger.error(f"Error getting current price: {e}")
        return None


def check_ao_red_candles(symbol: str, count: int = 5) -> bool:
    """
    Check if last 'count' AO candles are red (ao_color = -1)
    """
    try:
        df_1m = fetch_equity_data(symbol=symbol, interval='1m', limit=count + 5)
        
        if len(df_1m) < count:
            return False
        
        # Get last 'count' candles
        last_candles = df_1m.iloc[-count:]
        
        # Check if all ao_color values are -1 (red)
        if 'ao_color' not in last_candles.columns:
            return False
        
        all_red = (last_candles['ao_color'] == -1).all()
        
        return all_red
        
    except Exception as e:
        strategy_logger.error(f"Error in check_ao_red_candles: {e}")
        return False


def get_5sma_low(symbol: str, filter_pct: float = 0.0025) -> Optional[float]:
    """
    Get 5 SMA low value with filter applied
    Returns: 5_sma_low - (5_sma_low * filter_pct)
    """
    try:
        df_sma_1m = fetch_equity_sma_data(symbol=symbol, interval='1m', limit=1)
        
        if df_sma_1m.empty:
            return None
        
        sma_5_low = df_sma_1m.iloc[-1]['sma_5_low']
        
        if pd.isna(sma_5_low):
            return None
        
        # Apply filter
        stop_loss = sma_5_low - (sma_5_low * filter_pct)
        
        return stop_loss
        
    except Exception as e:
        strategy_logger.error(f"Error in get_5sma_low: {e}")
        return None