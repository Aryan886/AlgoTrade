#calculation of req stuff for strategies
import math
import pandas as pd
from database.load_data import load_latest_data
from utils.db_func import fetch_vix_data
from utils.iv import ProductionIVCalculator
from datetime import datetime, time

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



def calculate_enhanced_vix(
    option_data,
    spot_price,
    futures_price=None,
    spread_threshold=0.75,
    oi_min=20,
    forward_pct_cutoff=0.10,
    qk_cap_fraction=1/3,
    verbose=False
):
    """
    Production-ready NSE-like VIX estimator (price-based).

    Parameters:
        option_data: list of dicts containing (per option): 
                     strikePrice (or strike), optionType ('CE'/'PE'), expiry (YYYY-MM-DD),
                     bestBid/bid, bestAsk/ask, lastPrice/last_price/ltp, openInterest/oi
        spot_price: current spot index (float)
        futures_price: optional forward/futures price (float). If provided, used as forward F.
        spread_threshold: (ask-bid)/QK max allowed before excluding the strike.
        oi_min: minimum open interest for robust inclusion (used downstream if desired).
        forward_pct_cutoff: exclude strikes outside [F*(1-forward_pct_cutoff), F*(1+forward_pct_cutoff)]
        qk_cap_fraction: caps Q(K) at (max(spot, F) * qk_cap_fraction)
        verbose: print debug info when True

    Returns:
        float VIX (percentage) or None on failure.
    """

    if not option_data:
        if verbose: print("No option_data provided")
        return None

    # --- Normalize rows ---
    rows = []
    for r in option_data:
        try:
            strike = r.get("strikePrice") or r.get("strike")
            if strike is None:
                continue
            opt_type = str(r.get("optionType") or r.get("instrument_type") or "").upper()
            expiry = r.get("expiry")
            bid = r.get("bestBid") or r.get("bid") or 0.0
            ask = r.get("bestAsk") or r.get("ask") or 0.0
            last = r.get("lastPrice") or r.get("last_price") or r.get("ltp") or 0.0
            mid = (float(bid) + float(ask)) / 2.0 if (bid and ask) else float(last or 0.0)
            oi = float(r.get("openInterest") or r.get("oi") or 0.0)
            rows.append({
                "strike": float(strike),
                "type": opt_type,
                "expiry": expiry,
                "bid": float(bid or 0.0),
                "ask": float(ask or 0.0),
                "mid": float(mid or 0.0),
                "oi": oi,
                "last": float(last or 0.0)
            })
        except Exception:
            continue

    if not rows:
        if verbose: print("No normalized rows")
        return None

    # --- Group per strike ---
    strikes_map = {}
    for r in rows:
        strikes_map.setdefault(r["strike"], []).append(r)
    sorted_strikes = sorted(strikes_map.keys())

    # --- Forward index, K0 ---
    forward_index = float(futures_price) if futures_price is not None else float(spot_price)
    strikes_below_f = [s for s in sorted_strikes if s <= forward_index]
    K0 = max(strikes_below_f) if strikes_below_f else sorted_strikes[0]

    # Build per-strike CE/PE map
    per_strike = {}
    for s in sorted_strikes:
        ce = next((x for x in strikes_map[s] if x["type"] == "CE"), None)
        pe = next((x for x in strikes_map[s] if x["type"] == "PE"), None)
        per_strike[s] = {"CE": ce, "PE": pe}

    # --- Forward cutoff range ---
    lower_bound = forward_index * (1 - forward_pct_cutoff)
    upper_bound = forward_index * (1 + forward_pct_cutoff)

    # --- NSE-style expansion from K0 until 2 consecutive zero bids, but respect forward cutoff ---
    selected = set([K0])
    idx0 = sorted_strikes.index(K0)

    # left side (puts)
    consecutive_zero = 0
    for i in range(idx0 - 1, -1, -1):
        s = sorted_strikes[i]
        if s < lower_bound:
            break
        pe = per_strike[s].get("PE")
        bid = pe.get("bid", 0) if pe else 0
        selected.add(s)
        if bid == 0:
            consecutive_zero += 1
        else:
            consecutive_zero = 0
        if consecutive_zero >= 2:
            break

    # right side (calls)
    consecutive_zero = 0
    for i in range(idx0 + 1, len(sorted_strikes)):
        s = sorted_strikes[i]
        if s > upper_bound:
            break
        ce = per_strike[s].get("CE")
        bid = ce.get("bid", 0) if ce else 0
        selected.add(s)
        if bid == 0:
            consecutive_zero += 1
        else:
            consecutive_zero = 0
        if consecutive_zero >= 2:
            break

    strikes_used = sorted(selected)
    if verbose:
        print(f"Strikes in chain: {len(sorted_strikes)}, strikes used: {len(strikes_used)}, K0={K0}, forward={forward_index:.2f}")

    # --- Determine nearest expiry among used strikes ---
    today = datetime.now().date()
    expiry_dates = []
    for s in strikes_used:
        pair = per_strike[s]
        for side in ("CE", "PE"):
            obj = pair.get(side)
            if obj and obj.get("expiry"):
                try:
                    ed = datetime.strptime(str(obj["expiry"]), "%Y-%m-%d").date()
                    if ed >= today:
                        expiry_dates.append(ed)
                except:
                    pass
    nearest_expiry = min(expiry_dates) if expiry_dates else None

    # compute T (years)
    if nearest_expiry:
        expiry_dt = datetime.combine(nearest_expiry, time(15, 30))
        seconds = (expiry_dt - datetime.now()).total_seconds()
        T = max(seconds / (365 * 24 * 3600), 1/365)
    else:
        T = 30.0 / 365.0
    if verbose:
        print(f"Nearest expiry: {nearest_expiry}, T(years)={T:.6f}")

    # --- qk cap --- 
    cap_base = max(float(spot_price or 0.0), float(forward_index or 0.0)) or 1.0
    qk_cap = cap_base * qk_cap_fraction

    # --- compute contributions using time value ---
    def delta_k(i, strikes):
        if len(strikes) == 1:
            return strikes[0] * 0.1
        if i == 0:
            return strikes[1] - strikes[0]
        if i == len(strikes) - 1:
            return strikes[-1] - strikes[-2]
        return (strikes[i + 1] - strikes[i - 1]) / 2.0

    contribs = []
    for i, s in enumerate(strikes_used):
        pair = per_strike[s]
        # determine which side to use or ATM
        if s == K0 and pair.get("CE") and pair.get("PE"):
            ce = pair["CE"]
            pe = pair["PE"]
            tv_ce = max(ce["mid"] - max(spot_price - s, 0), 0)
            tv_pe = max(pe["mid"] - max(s - spot_price, 0), 0)
            QK = (tv_ce + tv_pe) / 2.0
            oi = (ce["oi"] + pe["oi"]) / 2.0
            bid = ce["bid"] or pe["bid"] or 0.0
            ask = ce["ask"] or pe["ask"] or 0.0
            side = "ATM"
        else:
            if s < forward_index:
                chosen = pair.get("PE")
                side = "PE"
            else:
                chosen = pair.get("CE")
                side = "CE"
            if not chosen:
                continue
            mid = chosen["mid"]
            oi = chosen["oi"]
            bid = chosen["bid"]
            ask = chosen["ask"]
            intrinsic = max(s - spot_price, 0) if side == "PE" else max(spot_price - s, 0)
            QK = max(mid - intrinsic, 0)

        if QK <= 0 or oi <= 0:
            continue

        # spread filter
        rel_spread = None
        if bid and ask and QK:
            rel_spread = (ask - bid) / QK
            if rel_spread > spread_threshold:
                # exclude if spread too wide
                continue

        # cap QK
        if QK > qk_cap:
            QK = qk_cap

        dk = delta_k(i, strikes_used)
        term = (dk / (s * s)) * QK
        contribs.append({"strike": s, "side": side, "QK": QK, "oi": oi, "dk": dk, "term": term, "rel_spread": rel_spread})

    if not contribs:
        if verbose: print("No contributions after filtering/time-value")
        return None

    total_contrib = sum(c["term"] for c in contribs)
    forward_term = ((forward_index / K0) - 1.0) ** 2

    # sigma^2
    sigma_sq = (2.0 / T) * total_contrib - (1.0 / T) * forward_term
    if sigma_sq <= 0:
        sigma_sq = (2.0 / T) * total_contrib
        if sigma_sq <= 0:
            if verbose: print("Non-positive sigma squared after fallback")
            return None

    vix = 100.0 * math.sqrt(sigma_sq)

    if verbose:
        contribs_sorted = sorted(contribs, key=lambda x: x["term"], reverse=True)
        print(f"Contributors used: {len(contribs_sorted)}, total_contrib={total_contrib:.10f}, forward_term={forward_term:.10f}")
        for c in contribs_sorted[:20]:
            print(f"  {c['strike']:8.0f} {c['side']:3} QK={c['QK']:.4f} oi={c['oi']:.0f} dk={c['dk']:.1f} term={c['term']:.8f} spread={c['rel_spread'] if c['rel_spread'] is not None else 'NA'}")
        print(f"VIX computed: {vix:.4f}")

    return vix

def calculate_enhanced_vix_30d(
    option_data,
    spot_price,
    futures_price=None,
    spread_threshold=0.6,
    oi_min=100,
    forward_pct_cutoff=0.10,
    qk_cap_fraction=0.20,
    verbose=False
):
    """
    30-day constant maturity VIX using two nearest expiries (official-style).
    Relies on calculate_enhanced_vix() for per-expiry variance computation.

    Parameters match calculate_enhanced_vix().
    """
    if not option_data:
        if verbose: print("No option data provided.")
        return None

    # Group by expiry date
    expiry_map = {}
    for r in option_data:
        expiry = r.get("expiry")
        if not expiry:
            continue
        try:
            ed = datetime.strptime(str(expiry), "%Y-%m-%d").date()
        except ValueError:
            continue
        expiry_map.setdefault(ed, []).append(r)

    if not expiry_map:
        if verbose: print("No valid expiries in option data.")
        return None

    today = datetime.now().date()
    target_days = 30.0
    T_target = target_days / 365.0

    expiries_sorted = sorted(expiry_map.keys())
    # Find the two expiries around 30 days
    before_expiry = None
    after_expiry = None
    for ed in expiries_sorted:
        days_to_expiry = (ed - today).days
        if days_to_expiry < target_days:
            before_expiry = ed
        elif days_to_expiry >= target_days and after_expiry is None:
            after_expiry = ed
            break

    if before_expiry is None:
        # No expiry before 30d, take nearest two after
        if len(expiries_sorted) < 2:
            if verbose: print("Not enough expiries to interpolate.")
            return None
        before_expiry, after_expiry = expiries_sorted[0], expiries_sorted[1]
    elif after_expiry is None:
        # No expiry after 30d, take nearest two before
        if len(expiries_sorted) < 2:
            if verbose: print("Not enough expiries to interpolate.")
            return None
        before_expiry, after_expiry = expiries_sorted[-2], expiries_sorted[-1]

    if verbose:
        print(f"Using expiries {before_expiry} and {after_expiry} for 30-day interpolation.")

    # Helper: compute variance from VIX
    def variance_from_expiry(expiry_date):
        vix_val = calculate_enhanced_vix(
            expiry_map[expiry_date],
            spot_price,
            futures_price=futures_price,
            spread_threshold=spread_threshold,
            oi_min=oi_min,
            forward_pct_cutoff=forward_pct_cutoff,
            qk_cap_fraction=qk_cap_fraction,
            verbose=verbose
        )
        if vix_val is None:
            return None, None
        days_to_expiry = max((expiry_date - today).days, 1)
        T = days_to_expiry / 365.0
        variance = (vix_val / 100.0) ** 2
        return variance, T

    var1, T1 = variance_from_expiry(before_expiry)
    var2, T2 = variance_from_expiry(after_expiry)

    if var1 is None or var2 is None or T1 is None or T2 is None:
        if verbose: print("Could not compute variance for one of the expiries.")
        return None

    # Interpolate to 30 days (official formula)
    w1 = (T2 - T_target) / (T2 - T1)
    w2 = (T_target - T1) / (T2 - T1)
    sigma2_30d = (T1 * var1 * w1 + T2 * var2 * w2) / T_target
    if sigma2_30d <= 0:
        if verbose: print("Non-positive sigma² after interpolation.")
        return None

    vix_30d = 100.0 * (sigma2_30d ** 0.5)

    if verbose:
        print(f"VIX from {before_expiry} ({T1*365:.1f}d): {math.sqrt(var1)*100:.4f}")
        print(f"VIX from {after_expiry} ({T2*365:.1f}d): {math.sqrt(var2)*100:.4f}")
        print(f"Interpolated 30-day VIX: {vix_30d:.4f}")

    return vix_30d



if __name__ == "__main__":
    calculate_vix()