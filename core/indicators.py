#calculation of req stuff for strategies
import math
import pandas as pd
from database.load_data import load_latest_data
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
    volume_col = 'Volume' if 'Volume' in df.columns else 'volume' if 'volume' in df.columns else None

    print(f"[DEBUG] Using columns: high={high_col}, low={low_col}, close={close_col}")
    print(f"[DEBUG] Available columns: {df.columns.tolist()}")

    # 1. Awesome Oscillator
    median_price = (df[high_col] + df[low_col]) / 2
    df['ao_value'] = median_price.rolling(window=ao_fast).mean() - median_price.rolling(window=ao_slow).mean()

    # 2. Donchian Channel
    df['donchian_upper'] = df[high_col].rolling(window=donchian_period).max()
    df['donchian_lower'] = df[low_col].rolling(window=donchian_period).min()
    df['donchian_mid'] = (df['donchian_upper'] + df['donchian_lower']) / 2

    #----Preserver volume column if exists ----#
    if volume_col in df.columns:
        df['volume'] = df[volume_col]

    # Try to fetch VIX data, but don't fail if it doesn't exist
    try:
        from utils.db_func import fetch_vix_data
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
    spread_threshold=0.75,#0.75
    oi_min=20,
    forward_pct_cutoff=0.10,#0.10
    qk_cap_fraction=1/3,#1/3
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

    # --- Compute T (years) with expiry-day safeguard --
    T_min_years = 0.5 / 365.0  # clamp to at least half a day

    if nearest_expiry:
        expiry_dt = datetime.combine(nearest_expiry, time(15, 30))
        seconds_left = (expiry_dt - datetime.now()).total_seconds()
        minutes_left = seconds_left / 60

        # Expiry-day roll rule: if < 180 min to expiry, skip to next expiry
        if nearest_expiry == today and minutes_left < 180:
            if verbose:
                print(f"Expiry day: {minutes_left:.1f} min left, switching to next expiry")
            # find the next expiry after nearest_expiry
            later_expiries = sorted(ed for ed in expiry_dates if ed > nearest_expiry)
            if later_expiries:
                nearest_expiry = later_expiries[0]
                expiry_dt = datetime.combine(nearest_expiry, time(15, 30))
                seconds_left = (expiry_dt - datetime.now()).total_seconds()
                minutes_left = seconds_left / 60
            else:
                if verbose: 
                    print("No later expiry found; continuing with front expiry")

        T = max(seconds_left / (365 * 24 * 3600), T_min_years)
        print(f"DEBUG: Expiry date: {nearest_expiry}")
        print(f"DEBUG: Current time: {datetime.now()}")
        print(f"DEBUG: T (years): {T}")
        print(f"DEBUG: T (days): {T * 365}")
    else:
        T = 30.0 / 365.0

    if verbose:
        print(f"Nearest expiry: {nearest_expiry}, T(years)={T:.6f}")

    # --- Penny option filter (remove ultra-cheap deep OTM contracts) ---
    min_premium = 0.0002 * float(spot_price or 0.0)  # 0.02% of spot (~₹4 for NIFTY)
    filtered_strikes = []
    for s in strikes_used:
        keep_strike = False
        for side in ("CE", "PE"):
            opt = per_strike[s].get(side)
            if opt and opt["mid"] >= min_premium:
                keep_strike = True
                break
        if keep_strike:
            filtered_strikes.append(s)

    # If too few strikes survive, disable the penny filter
    if len(filtered_strikes) < 6:
        if verbose:
            print(f"Penny filter left only {len(filtered_strikes)} strikes — disabling filter for this run.")
        filtered_strikes = strikes_used[:]  # revert to original

    if len(filtered_strikes) != len(strikes_used):
        if verbose:
            dropped = set(strikes_used) - set(filtered_strikes)
            print(f"Dropped {len(dropped)} penny-option strikes: {sorted(dropped)}")

    strikes_used = sorted(filtered_strikes)


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
    print(f"DEBUG: Spot price: {spot_price}")
    print(f"DEBUG: Forward index: {forward_index}")
    print(f"DEBUG: K0: {K0}")
    print(f"DEBUG: QK cap: {qk_cap}")
    print(f"DEBUG: Strikes to process: {len(strikes_used)}")

    for i, s in enumerate(strikes_used):
        total_raw_premiums = 0
        total_time_values = 0
        pair = per_strike[s]
        # determine which side to use or ATM
        if s == K0 and pair.get("CE") and pair.get("PE"):
            ce = pair["CE"]
            pe = pair["PE"]
            raw_premium_avg = (ce["mid"] + pe["mid"]) / 2.0
            tv_ce = max(ce["mid"] - max(spot_price - s, 0), 0)
            tv_pe = max(pe["mid"] - max(s - spot_price, 0), 0)
            QK = (tv_ce + tv_pe) / 2.0
            total_raw_premiums += raw_premium_avg
            total_time_values += QK
            print(f"DEBUG ATM {s}: Raw={(ce['mid']+pe['mid'])/2:.4f}, TimeValue={QK:.4f}")
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
            intrinsic = max(s - spot_price, 0) if side == "PE" else max(spot_price - s, 0)
            QK = max(mid - intrinsic, 0)
            total_raw_premiums += mid
            total_time_values += QK

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


    print(f"DEBUG: Total raw premiums: {total_raw_premiums:.4f}")
    print(f"DEBUG: Total time values used: {total_time_values:.4f}")
    print(f"DEBUG: Total contribution: {total_contrib:.10f}")
    print(f"DEBUG: Forward term: {forward_term:.10f}")
    print(f"DEBUG: Sigma squared: {sigma_sq:.10f}")

    vix = 100.0 * math.sqrt(sigma_sq)

    alt_contribs = []
    for i, s in enumerate(strikes_used):
        pair = per_strike[s]
        if s == K0 and pair.get("CE") and pair.get("PE"):
            ce = pair["CE"]
            pe = pair["PE"]
            QK_alt = (ce["mid"] + pe["mid"]) / 2.0  # Use raw premiums
        else:
            if s < forward_index:
                chosen = pair.get("PE")
            else:
                chosen = pair.get("CE")
            if not chosen:
                continue
            QK_alt = chosen["mid"]  # Use raw premium
        
        if QK_alt > 0:
            dk = delta_k(i, strikes_used)
            term_alt = (dk / (s * s)) * QK_alt
            alt_contribs.append(term_alt)
    
    if alt_contribs:
        alt_total_contrib = sum(alt_contribs)
        alt_sigma_sq = (2.0 / T) * alt_total_contrib - (1.0 / T) * forward_term
        if alt_sigma_sq > 0:
            alt_vix = 100.0 * math.sqrt(alt_sigma_sq)
            print(f"DEBUG: Alternative VIX (using raw premiums): {alt_vix:.4f}")

    if verbose:
        contribs_sorted = sorted(contribs, key=lambda x: x["term"], reverse=True)
        print(f"Contributors used: {len(contribs_sorted)}, total_contrib={total_contrib:.10f}, forward_term={forward_term:.10f}")
        for c in contribs_sorted[:20]:
            print(f"  {c['strike']:8.0f} {c['side']:3} QK={c['QK']:.4f} oi={c['oi']:.0f} dk={c['dk']:.1f} term={c['term']:.8f} spread={c['rel_spread'] if c['rel_spread'] is not None else 'NA'}")
        print(f"VIX computed: {vix:.4f}")

    return vix

def compute_intraday_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Compute intraday VWAP (resets at each calendar day).
    Expects df indexed by timestamp and containing 'high','low','close','volume' (lowercase).
    Returns a pd.Series aligned with df.index.
    """
    if df.empty:
        return pd.Series(dtype='float64')

    # Ensure needed columns exist
    for col in ('high', 'low', 'close', 'volume'):
        if col not in df.columns:
            raise KeyError(f"Missing required column for VWAP: {col}")

    # Prepare output
    vwap_series = pd.Series(index=df.index, dtype='float64')

    # Compute per-day VWAP (reset each day)
    # Group by calendar date (works for multi-day DataFrames)
    for _, group in df.groupby(df.index.date):
        g = df.loc[group.index]
        tp_g = (g['high'] + g['low'] + g['close']) / 3.0
        vol_g = g['volume'].fillna(0.0)

        cum_pv = (tp_g * vol_g).cumsum()
        cum_vol = vol_g.cumsum().replace({0: pd.NA})  # avoid div-by-zero

        v = cum_pv / cum_vol
        vwap_series.loc[g.index] = v.values

    return vwap_series


def compute_smas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a copy of df with 'sma_5' and 'sma_20' columns computed on 'close'
    df may have DatetimeIndex or a simple index; index values are preserverd
    """
    df2 = df.copy()
    #normalize column names to lowercase(make it usable for Close/close)
    df2.columns = [c.lower() for c in df.columns]
    if 'close' not in df2.columns:
        raise ValueError("compute_smas: DataFrame must contain 'close' coumn")
    
    #rolling with min_period=1 so early rows get valid values
    df2['sma_5'] = df2['close'].rolling(window=5, min_periods=1).mean()
    df2['sma_20'] = df2['close'].rolling(window=20, min_periods=1).mean()
    return df2