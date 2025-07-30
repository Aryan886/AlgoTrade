"""
Black-Scholes Option Pricing and Delta Calculation
High-accuracy delta calculation for options trading strategies
"""

import math
import numpy as np
from scipy.stats import norm
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from utils.iv import ProductionIVCalculator

# Risk-free rate (can be made configurable)
RISK_FREE_RATE = 0.065  # 6.5%

def calculate_black_scholes_delta(
    spot_price: float,
    strike_price: float,
    time_to_expiry: float,
    implied_volatility: float,
    option_type: str,
    risk_free_rate: float = RISK_FREE_RATE
) -> float:
    """
    Calculate option delta using Black-Scholes formula.
    
    Parameters:
        spot_price: Current spot price
        strike_price: Option strike price
        time_to_expiry: Time to expiry in years
        implied_volatility: Implied volatility (as decimal, e.g., 0.20 for 20%)
        option_type: 'CE' for call, 'PE' for put
        risk_free_rate: Risk-free rate (default: 6.5%)
    
    Returns:
        Option delta
    """
    # SAFETY CHECK: Warn and auto-convert if IV > 1.0 (likely percentage)
    if implied_volatility > 1.0:
        print(f"[WARN] Implied Volatility appears to be in percent ({implied_volatility:.2f}), converting to decimal.")
        implied_volatility = implied_volatility / 100.0
    print(f"[DEBUG] Black-Scholes Delta Calculation:")
    print(f"  Spot Price: {spot_price}")
    print(f"  Strike Price: {strike_price}")
    #print(f"  Time to Expiry (years): {time_to_expiry:.4f}")
    print(f"  Time to Expiry (days): {time_to_expiry * 365:.1f}")
    print(f"  Implied Volatility: {implied_volatility:.4f} ({implied_volatility * 100:.2f}%)")
    print(f"  Option Type: {option_type}")
    print(f"  Risk-Free Rate: {risk_free_rate:.4f} ({risk_free_rate * 100:.2f}%)")

    if time_to_expiry <= 0:
        print("  [WARN] Time to expiry is zero or negative. Returning delta=0.0.")
        print(f"  Delta (NSE style): 0.0")
        return 0.0  # Expired options
    
    if implied_volatility <= 0:
        print("  [WARN] Implied volatility is zero or negative. Returning delta=0.0.")
        print(f"  Delta (NSE style): 0.0")
        return 0.0  # Invalid IV
    
    # Black-Scholes parameters
    S = spot_price
    K = strike_price
    T = time_to_expiry
    r = risk_free_rate
    sigma = implied_volatility
    
    # Calculate d1 and d2
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d1 = float(d1)
    d2 = d1 - sigma * math.sqrt(T)
    d2 = float(d2)
    
    print(f"  d1: {d1:.6f}")
    print(f"  d2: {d2:.6f}")
    print(f"  N(d1): {norm.cdf(d1):.6f}")
    print(f"  N(d2): {norm.cdf(d2):.6f}")
    
    # Calculate delta
    if option_type.upper() == 'CE':
        delta = float(norm.cdf(d1))
        print(f"  Call Delta: N(d1) = {delta:.6f}")
    elif option_type.upper() == 'PE':
        delta = float(norm.cdf(d1) - 1)
        print(f"  Put Delta: N(d1) - 1 = {delta:.6f}")
    else:
        raise ValueError(f"Invalid option type: {option_type}. Must be 'CE' or 'PE'")
    
    print(f"  Delta (NSE style): {delta * 100:.2f}")
    return delta

def log_option_details(prefix: str, option: Dict, T: float):
    print(f"{prefix}")
    print(f"  Tradingsymbol: {option.get('tradingsymbol')}")
    print(f"  Strike Price: {option.get('strike_price')}")
    print(f"  Option Type: {option.get('option_type')}")
    print(f"  Expiry Date: {option.get('expiry_date')}")
    print(f"  IV: {option.get('iv')}")
    print(f"  LTP: {option.get('ltp')}")
    print(f"  Volume: {option.get('volume')}")
    print(f"  OI: {option.get('oi')}")
    print(f"  Time to Expiry (years): {T}")
    

def calculate_delta_for_strike_band(
    spot_price: float,
    strike_band: List[int],
    option_data: List[Dict],
    current_date: Optional[date] = None
) -> Dict[int, Dict[str, Dict[str, float]]]:
    """
    Calculate delta for a band of strikes using cached option data.
    Prioritizes options with better liquidity and reasonable time to expiry.
    """
    if current_date is None:
        current_date = date.today()

    delta_results = {}
    iv_calc = ProductionIVCalculator()

    for strike in strike_band:
        # Find all CE and PE options for this strike
        ce_options = [opt for opt in option_data if opt['strike_price'] == strike and opt['option_type'] == 'CE']
        pe_options = [opt for opt in option_data if opt['strike_price'] == strike and opt['option_type'] == 'PE']

        # Helper to get best option based on liquidity and time to expiry
        def get_best_option(options):
            if not options:
                return None
            
            valid_options = []
            for opt in options:
                expiry = opt['expiry_date']
                # Convert expiry to string if it's a datetime/date
                if not isinstance(expiry, str):
                    expiry = str(expiry)
                
                # Check if option is not expired
                if expiry >= str(current_date):
                    # Calculate time to expiry using iv_calc
                    T = iv_calc.calculate_time_to_expiry(expiry)
                    
                    # Filter options with reasonable time to expiry (1-60 days preferred)
                    if T is not None and 1/365 <= T <= 60/365:  # 1 day to 60 days
                        valid_options.append((opt, T))
            
            if not valid_options:
                # If no options in preferred range, use any non-expired option
                for opt in options:
                    expiry = opt['expiry_date']
                    if not isinstance(expiry, str):
                        expiry = str(expiry)
                    if expiry >= str(current_date):
                        T = iv_calc.calculate_time_to_expiry(expiry)
                        if T is not None and T > 0:
                            valid_options.append((opt, T))
            
            if not valid_options:
                return None
            
            # Sort by time to expiry (prefer options closer to 30 days)
            # and by liquidity (higher volume/open interest preferred)
            def sort_key(item):
                opt, T = item
                # Prefer options around 30 days to expiry
                time_preference = abs(T - 30/365)
                # Prefer higher volume (if available)
                volume = opt.get('volume', 0) or opt.get('oi', 0) or 0
                return time_preference, -volume
            
            valid_options.sort(key=sort_key)
            return valid_options[0][0]  # Return the best option

        ce_option = get_best_option(ce_options)
        pe_option = get_best_option(pe_options)

        strike_results = {}

        # Calculate CE delta
        if ce_option:
            try:
                T = iv_calc.calculate_time_to_expiry(ce_option['expiry_date'])
                log_option_details(f"[DEBUG] Using CE option for strike {strike}:", ce_option, T)
                ce_delta = iv_calc.calculate_delta(
                    spot=spot_price,
                    strike=strike,
                    T=T,
                    iv=ce_option['iv'],
                    option_type='CE'
                )
                print(f"  Delta: {ce_delta*100}")
                strike_results['CE'] = {
                    'delta': ce_delta,
                    'iv': ce_option['iv'],
                    'ltp': ce_option['ltp'],
                    'tradingsymbol': ce_option['tradingsymbol'],
                    'expiry_date': ce_option['expiry_date'],
                    'time_to_expiry': T
                }
            except Exception as e:
                print(f"Error calculating CE delta for strike {strike}: {e}")

        # Calculate PE delta
        if pe_option:
            try:
                T = iv_calc.calculate_time_to_expiry(pe_option['expiry_date'])
                log_option_details(f"[DEBUG] Using PE option for strike {strike}:", pe_option, T)
                pe_delta = iv_calc.calculate_delta(
                    spot=spot_price,
                    strike=strike,
                    T=T,
                    iv=pe_option['iv'],
                    option_type='PE'
                )
                print(f"  Delta: {pe_delta*100}")
                strike_results['PE'] = {
                    'delta': pe_delta,
                    'iv': pe_option['iv'],
                    'ltp': pe_option['ltp'],
                    'tradingsymbol': pe_option['tradingsymbol'],
                    'expiry_date': pe_option['expiry_date'],
                    'time_to_expiry': T
                }
            except Exception as e:
                print(f"Error calculating PE delta for strike {strike}: {e}")

        delta_results[strike] = strike_results

    return delta_results

def interpolate_delta(
    target_strike: int,
    delta_results: Dict[int, Dict[str, Dict[str, float]]],
    option_type: str
) -> Optional[float]:
    """
    Interpolate delta for a strike not in the cache.
    
    Parameters:
        target_strike: Strike to interpolate for
        delta_results: Dictionary of calculated deltas by strike
        option_type: 'CE' or 'PE'
    
    Returns:
        Interpolated delta or None if not enough data
    """
    strikes = sorted(delta_results.keys())
    
    if len(strikes) < 2:
        return None
    
    # Find the two closest strikes
    lower_strike = None
    upper_strike = None
    
    for strike in strikes:
        if strike <= target_strike:
            lower_strike = strike
        if strike >= target_strike:
            upper_strike = strike
            break
    
    if lower_strike is None or upper_strike is None:
        return None
    
    if lower_strike == upper_strike:
        # Exact match
        if option_type in delta_results[lower_strike]:
            return delta_results[lower_strike][option_type].get('delta')
        return None
    
    # Get deltas for interpolation
    lower_delta = None
    upper_delta = None
    
    if option_type in delta_results[lower_strike]:
        lower_delta = delta_results[lower_strike][option_type].get('delta')
    if option_type in delta_results[upper_strike]:
        upper_delta = delta_results[upper_strike][option_type].get('delta')
    
    if lower_delta is None or upper_delta is None:
        return None
    
    # Linear interpolation
    weight = (target_strike - lower_strike) / (upper_strike - lower_strike)
    interpolated_delta = lower_delta + weight * (upper_delta - lower_delta)
    
    return interpolated_delta

def get_current_delta(
    option_type: str,
    strike: int,
    spot_price: float,
    cached_options: List[Dict],
    current_date: Optional[date] = None
) -> Optional[float]:
    """
    Get current delta for a specific option using cached data and live spot price.
    Uses improved option selection logic for better accuracy.
    
    Parameters:
        option_type: 'CE' or 'PE'
        strike: Option strike price
        spot_price: Current spot price
        cached_options: List of cached option data
        current_date: Current date for T calculation
    
    Returns:
        Calculated delta or None if not available
    """
    if current_date is None:
        current_date = date.today()
    
    iv_calc = ProductionIVCalculator()
    # Find all options for this strike and type
    matching_options = [
        opt for opt in cached_options 
        if opt['strike_price'] == strike and opt['option_type'] == option_type
    ]
    
    if not matching_options:
        return None
    
    # Use the same selection logic as calculate_delta_for_strike_band
    valid_options = []
    for opt in matching_options:
        expiry = opt['expiry_date']
        if not isinstance(expiry, str):
            expiry = str(expiry)
        
        if expiry >= str(current_date):
            T = iv_calc.calculate_time_to_expiry(expiry)
            if 1/365 <= T <= 60/365:  # 1 day to 60 days preferred
                valid_options.append((opt, T))
    
    if not valid_options:
        # If no options in preferred range, use any non-expired option
        for opt in matching_options:
            expiry = opt['expiry_date']
            if not isinstance(expiry, str):
                expiry = str(expiry)
            if expiry >= str(current_date):
                T = iv_calc.calculate_time_to_expiry(expiry)
                if T > 0:
                    valid_options.append((opt, T))
    
    if not valid_options:
        return None
    
    # Sort by time to expiry preference and liquidity
    def sort_key(item):
        opt, T = item
        time_preference = abs(T - 30/365)  # Prefer around 30 days
        volume = opt.get('volume', 0) or opt.get('oi', 0) or 0
        return time_preference, -volume
    
    valid_options.sort(key=sort_key)
    target_option, T = valid_options[0][0], valid_options[0][1]
    
    try:
        log_option_details(f"[DEBUG] Selected {option_type} option for strike {strike}:", target_option, T)
        print(f"[DEBUG] Inputs to calculate_black_scholes_delta: spot_price={spot_price}, strike_price={strike}, T={T}, IV={target_option['iv']}, option_type={option_type}")
        delta = calculate_black_scholes_delta(
            spot_price=spot_price,
            strike_price=strike,
            time_to_expiry=T,
            implied_volatility=target_option['iv'],
            option_type=option_type
        )
        return delta
    except Exception as e:
        print(f"Error calculating delta for {option_type} {strike}: {e}")
        return None 


if __name__ == "__main__":
    from utils.db_func import calculate_and_store_high_accuracy_delta
    """
    Main entry point for calculating and storing high-accuracy deltas.
    Usage: python utils/db_func.py
    """
    from pprint import pprint
    symbol = "NIFTY50"
    print("\n=== High-Accuracy Delta Calculation & Storage ===")
    result = calculate_and_store_high_accuracy_delta(symbol)
    if result:
        print(f"\nSpot Price: {result['spot_price']}")
        print(f"Strike Band: {result['strike_band']}")
        print(f"Timestamp: {result['timestamp']}")
        print("\nDelta Results:")
        print(f"{'Strike':>8} | {'Type':>3} | {'Delta':>8} | {'IV':>8} | {'Expiry':>10}")
        print("-"*55)
        for strike in result['strike_band']:
            strike_data = result['delta_results'].get(strike, {})
            for option_type in ["CE", "PE"]:
                data = strike_data.get(option_type)
                if data:
                    print(f"{strike:8} | {option_type:>3} | {data['delta']*100:8.2f} | {data['iv']*100:8.2f} | {data['expiry_date']:>10}")
                else:
                    print(f"{strike:8} | {option_type:>3} | {'N/A':>8} | {'N/A':>8} | {'N/A':>10}")
        print("\n✅ Deltas calculated and stored in delta_cache table.")
    else:
        print("❌ Failed to calculate/store deltas.")

