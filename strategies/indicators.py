#calculation of req stuff for strategies
import math
import pandas as pd
from database.load_data import load_latest_data
from utils.db_func import fetch_vix_data


def add_awesome_oscillator(df, small=5, large=34):
    df = df.copy()

    df["MedianPrice"] = (df["High"] + df["Low"]) / 2
    df["AO_small"] = df["MedianPrice"].rolling(window=small)
    df["AO_large"] = df["MedianPrice"].rolling(window=large)

    #Awesome Oscillator
    df["AO"] = df["AO_large"] - df["AO_small"]
    return df

#This tells us when AO crosses the neutral line
def add_ao_signal(df):
    df = df.copy()
    #Generate signal: if AO crosses above 0
    df["AO_Signal"] = 0

    df.loc[(df["AO"] > 0) & (df["AO"].shift(1) <= 0), "AO_Signal"] = 1 #Crossed above 0, i.e Bullish Market
    df.loc[(df["AO"] < 0) & (df["AO"].shift(1) >= 0), "AO_Signal"] = -1 #Crossed below 0, i.e Bearish Market
    
    return df

def add_donchian_channel(df, period=28, suffix=""):
    df = df.copy()
    df[f"Donchian_High{suffix}"] = df["High"].rolling(window=period).max()
    df[f"Donchian_Low{suffix}"] = df["Low"].rolling(window=period).min()
    df[f"Donchian_Mid{suffix}"] = (df[f"Donchian_High{suffix}"] + df[f"Donchian_Low{suffix}"]) / 2
    return df
    

def sma(df, column='Close', period=20, label=None):
    """
    Adds SMA to the df.
    
    Parameters:
        df : OHLCV df with datetime index.
        column (str): The column to calculate the SMA on (usually 'Close').
        window= period (int): The number of periods for the SMA.
        label (str): Optional. If provided, sets a custom column name.

    Returns:
        df: The same df with a new SMA column added.
    """
    col_name = label if label else f"SMA_{period}"
    df[col_name] = df[column].rolling(window=period).mean()
    return df


def compute_indicators(
    df: pd.DataFrame,
    ao_fast: int = 5,
    ao_slow: int = 34,
    donchian_period: int = 20
) -> pd.DataFrame:
    """
    Computes technical indicators:
    - Awesome Oscillator (AO) using ao_fast and ao_slow periods
    - Donchian Channel using donchian_period
    """
    df = df.copy()
    
    # Handle both uppercase and lowercase column names
    high_col = 'High' if 'High' in df.columns else 'high'
    low_col = 'Low' if 'Low' in df.columns else 'low'
    close_col = 'Close' if 'Close' in df.columns else 'close'
    
    print(f"[DEBUG] Using columns: high={high_col}, low={low_col}, close={close_col}")
    print(f"[DEBUG] Available columns: {df.columns.tolist()}")

    # 1. Awesome Oscillator
    median_price = (df[high_col] + df[low_col]) / 2
    df['ao_value'] = median_price.rolling(window=ao_fast).mean() - median_price.rolling(window=ao_slow).mean()

    # 2. Donchian Channel
    df['donchian_upper'] = df[high_col].rolling(window=donchian_period).max()
    df['donchian_lower'] = df[low_col].rolling(window=donchian_period).min()
    df['donchian_mid'] = (df['donchian_upper'] + df['donchian_lower']) / 2

    # Try to fetch VIX data, but don't fail if it doesn't exist
    try:
        vix_df = fetch_vix_data(symbol="NIFTY50")
        if not vix_df.empty:
            vix_df['ao_value'] = vix_df['vix_value'].rolling(window=5).mean() - vix_df['vix_value'].rolling(window=34).mean()
            vix_df['donchian_upper'] = vix_df['vix_value'].rolling(window=donchian_period).max()
            vix_df['donchian_lower'] = vix_df['vix_value'].rolling(window=donchian_period).min()
            vix_df['donchian_mid'] = (vix_df['donchian_upper'] + vix_df['donchian_lower']) / 2
            print("[DEBUG] VIX data processed successfully")
        else:
            print("[DEBUG] No VIX data available, continuing without VIX indicators")
    except Exception as e:
        print(f"[DEBUG] Error fetching VIX data: {e}, continuing without VIX indicators")

    return df


def generate_signals(df: pd.DataFrame, symbol: str = "NIFTY50") -> list:
    """
    Generate trading signals based on AO and Donchian indicators.
    
    Parameters:
        df: DataFrame with OHLCV data and computed indicators
        symbol: Trading symbol
    
    Returns:
        List of signal dictionaries with timestamp, signal type, reason, and confidence
    """
    signals = []
    
    if df.empty:
        return signals
    
    # Ensure we have the required columns
    required_cols = ['ao_value', 'donchian_upper', 'donchian_lower', 'donchian_mid']
    if not all(col in df.columns for col in required_cols):
        print("[WARN] Missing required columns for signal generation")
        return signals
    
    # Get the latest data point for signal generation
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    
    if prev is None:
        return signals
    
    current_time = df.index[-1]
    current_price = latest.get('close', latest.get('Close', 0))
    
    # 1. AO Signal Generation
    ao_signal = None
    ao_reason = None
    ao_confidence = 0.0
    
    if pd.notna(latest['ao_value']) and pd.notna(prev['ao_value']):
        # AO crosses above 0 (bullish)
        if latest['ao_value'] > 0 and prev['ao_value'] <= 0:
            ao_signal = "BUY"
            ao_reason = "AO crossover above 0"
            ao_confidence = 0.75
        # AO crosses below 0 (bearish)
        elif latest['ao_value'] < 0 and prev['ao_value'] >= 0:
            ao_signal = "SELL"
            ao_reason = "AO crossover below 0"
            ao_confidence = 0.75
    
    # 2. Donchian Channel Signal Generation
    donchian_signal = None
    donchian_reason = None
    donchian_confidence = 0.0
    
    if pd.notna(latest['donchian_upper']) and pd.notna(latest['donchian_lower']):
        # Price breaks above upper Donchian (bullish breakout)
        if current_price > latest['donchian_upper']:
            donchian_signal = "BUY"
            donchian_reason = "Donchian upper breakout"
            donchian_confidence = 0.80
        # Price breaks below lower Donchian (bearish breakout)
        elif current_price < latest['donchian_lower']:
            donchian_signal = "SELL"
            donchian_reason = "Donchian lower breakout"
            donchian_confidence = 0.80
    
    # 3. Combined Signal Logic
    combined_signal = None
    combined_reason = None
    combined_confidence = 0.0
    
    # Both signals agree (strongest signal)
    if ao_signal and donchian_signal and ao_signal == donchian_signal:
        combined_signal = ao_signal
        combined_reason = f"{ao_reason} + {donchian_reason}"
        combined_confidence = min(0.95, (ao_confidence + donchian_confidence) / 2 + 0.1)
    # Only AO signal
    elif ao_signal:
        combined_signal = ao_signal
        combined_reason = ao_reason
        combined_confidence = ao_confidence
    # Only Donchian signal
    elif donchian_signal:
        combined_signal = donchian_signal
        combined_reason = donchian_reason
        combined_confidence = donchian_confidence
    
    # Create signal entry if we have a valid signal
    if combined_signal:
        signal_entry = {
            'timestamp': current_time,
            'symbol': symbol,
            'signal': combined_signal,
            'reason': combined_reason,
            'confidence_score': combined_confidence
        }
        signals.append(signal_entry)
        print(f"[SIGNAL] Generated {combined_signal} signal: {combined_reason} (confidence: {combined_confidence:.2f})")
    
    return signals


def calculate_vix(option_data, spot_price, strike_window=300):
    """
    Calculates a simplified VIX estimate using weighted IVs.

    Parameters:
        option_data (list of dict): Option chain rows
        spot_price (float): Current index value (e.g., NIFTY)
        strike_window (int): Consider strikes within ±window of ATM

    Returns:
        float: Estimated India VIX
    """
    total_weight = 0
    weighted_iv_sum = 0

    # Check if any IV is > 1.0 (likely percent)
    ivs = [row.get("IV") for row in option_data if row.get("IV") is not None]
    treat_as_percent = any(iv is not None and iv > 1.0 for iv in ivs)
    if treat_as_percent:
        print("[WARN] At least one IV > 1.0 detected in VIX calculation. Treating all IVs as percent and converting to decimal.")

    for row in option_data:
        strike = row.get("strikePrice")
        iv = row.get("IV")
        oi = row.get("openInterest")

        if iv is None or oi is None:
            continue

        # Only use strikes close to ATM
        if abs(strike - spot_price) > strike_window:
            continue

        weight = oi
        iv_decimal = (iv / 100.0) if treat_as_percent else iv
        weighted_iv_sum += weight * (iv_decimal ** 2)
        total_weight += weight

    if total_weight == 0:
        print("[WARN] No valid OI data for VIX calculation.")
        return None

    vix = 100 * math.sqrt(weighted_iv_sum / total_weight)
    return vix

