#calculation of req stuff for strategies
import math
import pandas as pd
from database.load_data import load_latest_data
from utils.db_func import fetch_vix_data
from utils.iv import ProductionIVCalculator


def add_awesome_oscillator(df, small=5, large=34):
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    df["medianprice"] = (df["high"] + df["low"]) / 2
    ao_small = df["medianprice"].rolling(window=small).mean()
    ao_large = df["medianprice"].rolling(window=large).mean()

    df["ao_value"] = ao_small - ao_large
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
    df.columns = [col.lower() for col in df.columns]  # make sure all cols lowercase

    df[f"donchian_high{suffix}"] = df["high"].rolling(window=period).max()
    df[f"donchian_low{suffix}"] = df["low"].rolling(window=period).min()
    df[f"donchian_mid{suffix}"] = (df[f"donchian_high{suffix}"] + df[f"donchian_low{suffix}"]) / 2
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
        option_type = row.get("optionType")

        if iv is None or oi is None or option_type is None:
            continue

        # Only use strikes close to ATM
        if abs(strike - spot_price) > strike_window:
            continue

        if option_type == "CE" and strike < spot_price:
            continue
        if option_type == "PE" and strike > spot_price:
            continue

        weight = oi
        iv_decimal = (iv / 100.0) if treat_as_percent else iv
        weighted_iv_sum += weight * (iv_decimal ** 2)
        total_weight += weight

    if total_weight == 0:
        print("[WARN] No valid OI data for VIX calculation.")
        return None

    vix = 100 * math.sqrt(weighted_iv_sum / total_weight)
    print(f"Calculated vix: {vix}")
    return vix

def calculate_vix2(option_data, spot_price, strike_window=300):
    """
    Calculates a simplified VIX estimate using IVs from ProductionIVCalculator.
    """
    iv_calc = ProductionIVCalculator()

    total_weight = 0
    weighted_iv_sum = 0

    for row in option_data:
        strike = row.get("strikePrice")
        ltp = row.get("lastPrice")   
        expiry_date = row.get("expiry")  # 'YYYY-MM-DD'
        option_type = row.get("optionType")
        oi = row.get("openInterest")

        # Convert expiry to string format for IV calculation
        if expiry_date:
            if isinstance(expiry_date, str):
                expiry_date_stri = expiry_date
            else:
                expiry_date_stri = expiry_date.strftime('%Y-%m-%d') if hasattr(expiry_date, 'strftime') else str(expiry_date)
        else:
            continue  # Skip if no expiry date

        # Skip incomplete data
        if None in (strike, ltp, expiry_date, option_type, oi):
            continue

        # Filter strikes near ATM
        if abs(strike - spot_price) > strike_window:
            continue
        if option_type == "CE" and strike < spot_price:
            continue
        if option_type == "PE" and strike > spot_price:
            continue

        # Calculate IV using your iv.py function
        iv_result = iv_calc.calculate_iv(
            spot=spot_price,
            strike=strike,
            ltp=ltp,
            expiry_date_str=expiry_date_stri,
            option_type=option_type
        )

        if iv_result["status"] != "success" or iv_result["iv"] is None:
            continue

        iv_decimal = iv_result["iv"]  # Already in decimal form
        print(iv_decimal)
        weight = oi
        weighted_iv_sum += weight * (iv_decimal ** 2)
        total_weight += weight

    if total_weight == 0:
        print("[WARN] No valid OI data for VIX calculation.")
        return None

    vix = 100 * math.sqrt(weighted_iv_sum / total_weight)
    print(f"Calculated vix (from calculated IVs): {vix}")
    return vix


def calculate_enhanced_vix(option_data, spot_price, futures_price=None, strike_window=300, spread_threshold=0.30):
    """
    Enhanced VIX calculation following NSE methodology using option mid-prices.

    Parameters:
        option_data: list of option dicts (must contain strikePrice, midPrice/lastPrice, bestBid, bestAsk, openInterest, optionType, expiry)
        spot_price: current spot index (float)
        futures_price: optional forward/futures price (float); if provided used as forward index
        strike_window: include strikes within +/- strike_window of forward index
        spread_threshold: maximum allowed relative spread (ask-bid)/mid to accept an option (default 0.30)

    Returns:
        float VIX (percentage), or None on failure.
    """
    import math
    from datetime import datetime, time

    if not option_data:
        print("No option data provided")
        return None

    forward_index = futures_price if futures_price else spot_price

    # Build mapping strike -> {'CE': row, 'PE': row}
    strikes_data = {}
    for row in option_data:
        strike = row.get("strikePrice")
        if strike is None:
            continue
        strikes_data.setdefault(strike, {})
        opt_type = row.get("optionType")
        if opt_type in ("CE", "PE"):
            strikes_data[strike][opt_type] = row

    if not strikes_data:
        print("No strikes found in option_data")
        return None

    sorted_strikes = sorted(strikes_data.keys())

    # determine K0: largest strike <= forward_index, else smallest strike
    strikes_below_forward = [s for s in sorted_strikes if s <= forward_index]
    if strikes_below_forward:
        K0 = max(strikes_below_forward)
    else:
        K0 = min(sorted_strikes)

    # Choose strikes within strike_window
    candidate_strikes = [s for s in sorted_strikes if abs(s - forward_index) <= strike_window]
    if len(candidate_strikes) < 3:
        # fallback to using all strikes if range was too narrow
        candidate_strikes = sorted_strikes

    # Collect Q(Ki) (premium to be used) per strike following NSE logic:
    # - If Ki < F: use Put (PE)
    # - If Ki > F: use Call (CE)
    # - If Ki == K0: handled as ATM (average CE+PE)
    valid_options = []
    filtered_counts = {"missing": 0, "spread": 0, "zero": 0, "selected": 0}

    for strike in candidate_strikes:
        if strike == K0:
            # ATM will be added later
            continue

        row_pair = strikes_data.get(strike, {})
        if strike < forward_index:
            selected = row_pair.get("PE")
        else:
            selected = row_pair.get("CE")

        if not selected:
            filtered_counts["missing"] += 1
            continue

        mid = selected.get("midPrice") or selected.get("lastPrice") or 0
        oi = selected.get("openInterest", 0)
        bid = selected.get("bestBid") or 0
        ask = selected.get("bestAsk") or 0

        if mid <= 0 or oi <= 0:
            filtered_counts["zero"] += 1
            continue

        # compute relative spread if possible
        spread = 0.0
        if bid and ask and mid:
            spread = (ask - bid) / mid
            if spread > spread_threshold:
                filtered_counts["spread"] += 1
                continue

        valid_options.append({
            "strike": strike,
            "mid_price": float(mid),
            "oi": oi,
            "bid": bid,
            "ask": ask,
            "expiry": selected.get("expiry")
        })
        filtered_counts["selected"] += 1

    # Add ATM (K0) as average of CE and PE if both present
    ce_atm = strikes_data.get(K0, {}).get("CE")
    pe_atm = strikes_data.get(K0, {}).get("PE")
    if ce_atm or pe_atm:
        call_mid = ce_atm.get("midPrice") if ce_atm else None
        put_mid = pe_atm.get("midPrice") if pe_atm else None
        call_mid = call_mid or (ce_atm.get("lastPrice") if ce_atm else 0)
        put_mid = put_mid or (pe_atm.get("lastPrice") if pe_atm else 0)
        if call_mid and put_mid:
            atm_mid = (float(call_mid) + float(put_mid)) / 2.0
            atm_oi = (ce_atm.get("openInterest", 0) + pe_atm.get("openInterest", 0)) / 2.0
            valid_options.append({
                "strike": K0,
                "mid_price": float(atm_mid),
                "oi": atm_oi,
                "bid": ce_atm.get("bestBid") or pe_atm.get("bestBid") or 0,
                "ask": ce_atm.get("bestAsk") or pe_atm.get("bestAsk") or 0,
                "expiry": ce_atm.get("expiry") if ce_atm else pe_atm.get("expiry")
            })

    if not valid_options:
        print("No valid options for VIX calculation after filtering")
        print(f"Filter counts: {filtered_counts}")
        return None

    # Sort by strike
    valid_options.sort(key=lambda x: x["strike"])
    strikes_list = [opt["strike"] for opt in valid_options]

    # delta_k calculation (NSE method)
    def delta_k_at(i, strikes):
        if len(strikes) == 1:
            return strikes[0] * 0.1 if strikes[0] != 0 else 100
        if i == 0:
            return strikes[1] - strikes[0]
        if i == len(strikes) - 1:
            return strikes[-1] - strikes[-2]
        return (strikes[i+1] - strikes[i-1]) / 2.0

    # Time to expiry T in years: use first expiry found (assume all same near-term series)
    T = 30.0 / 365.0
    # try to calculate real T precisely
    expiry_candidate = None
    for opt in valid_options:
        exp = opt.get("expiry")
        if exp:
            expiry_candidate = exp
            break

    if expiry_candidate:
        try:
            # expiry might be a string 'YYYY-MM-DD' or a date/datetime
            if isinstance(expiry_candidate, str):
                expiry_dt = datetime.strptime(expiry_candidate, "%Y-%m-%d")
            elif hasattr(expiry_candidate, "strftime"):
                expiry_dt = expiry_candidate if isinstance(expiry_candidate, datetime) else datetime.combine(expiry_candidate, time(15,30))
            else:
                expiry_dt = datetime.strptime(str(expiry_candidate), "%Y-%m-%d")
            # NSE expiry time is 15:30 by convention for option series
            expiry_dt = expiry_dt.replace(hour=15, minute=30, second=0, microsecond=0)
            now = datetime.now()
            seconds = (expiry_dt - now).total_seconds()
            if seconds <= 0:
                T = 1.0 / 365.0
            else:
                T = seconds / (365.0 * 24.0 * 3600.0)
        except Exception as e:
            print(f"[WARN] Could not compute precise expiry, using default T=30/365. Error: {e}")

    # Now compute total contribution Σ[(ΔKi / Ki^2) * Q(Ki)]
    total_contrib = 0.0
    for i, opt in enumerate(valid_options):
        Ki = opt["strike"]
        QKi = opt["mid_price"]
        dk = delta_k_at(i, strikes_list)
        total_contrib += (dk / (Ki * Ki)) * QKi
        if i < 12:
            print(f"Debug contrib - Strike {Ki}: ΔK={dk}, Q(K)={QKi:.2f}, term={(dk / (Ki*Ki) * QKi):.8f}")

    if total_contrib <= 0:
        print(f"Invalid total contribution: {total_contrib}")
        return None

    forward_term = ((forward_index / K0) - 1.0) ** 2
    sigma_squared = (2.0 / T) * total_contrib - (1.0 / T) * forward_term

    print(f"Summary: forward_index={forward_index}, K0={K0}, T={T:.6f} years")
    print(f"Total contribution: {total_contrib:.8f}, forward_term: {forward_term:.8f}")
    print(f"Sigma^2 (raw): {sigma_squared:.8f}")

    if sigma_squared <= 0:
        # Try fallback without forward term if forward_term dominates (conservative)
        print("[WARN] Non-positive sigma_squared computed. Trying fallback without forward adjustment.")
        sigma_squared = (2.0 / T) * total_contrib
        if sigma_squared <= 0:
            print("[ERROR] Fallback sigma_squared still non-positive")
            return None

    vix = 100.0 * math.sqrt(sigma_squared)
    print(f"Enhanced VIX: {vix:.2f}")
    return vix

if __name__ == "__main__":
    calculate_vix()