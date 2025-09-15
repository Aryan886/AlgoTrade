"""
Comprehensive VIX Calculation Diagnostic Tool
"""
from utils.vix_fetcher import get_nifty50_spot_price, fetch_live_option_chain
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import math


def diagnose_vix_calculation():
    """
    Comprehensive diagnostic for VIX calculation issues
    """
    print("=== VIX CALCULATION DIAGNOSTIC ===\n")
    
    # Step 1: Check the current market data
    print("1. MARKET DATA CHECK")
    print("-" * 40)
    
    spot_price = get_nifty50_spot_price()
    print(f"NIFTY 50 Spot Price: {spot_price}")
    
    # Step 2: Check option data quality
    print("\n2. OPTION DATA QUALITY CHECK")
    print("-" * 40)
    
    option_data = fetch_live_option_chain()
    if not option_data:
        print("ERROR: No option data fetched!")
        return
    
    print(f"Total options fetched: {len(option_data)}")
    
    # Check data completeness
    valid_options = [opt for opt in option_data if opt.get('IV') and opt.get('IV') > 0]
    print(f"Options with valid IV: {len(valid_options)}")
    
    # Step 3: Check IV values and units
    print("\n3. IMPLIED VOLATILITY ANALYSIS")
    print("-" * 40)
    
    analyze_iv_values(valid_options)
    
    # Step 4: Check expiry dates and time to expiry
    print("\n4. EXPIRY DATE ANALYSIS")
    print("-" * 40)
    
    analyze_expiries(valid_options)
    
    # Step 5: Check strike selection for VIX
    print("\n5. STRIKE SELECTION ANALYSIS")
    print("-" * 40)
    
    analyze_strike_selection(valid_options, spot_price)
    
    # Step 6: Manual VIX calculation with debugging
    print("\n6. MANUAL VIX CALCULATION")
    print("-" * 40)
    
    manual_vix_calculation(valid_options, spot_price)

def analyze_iv_values(options):
    """Analyze IV values to check units and reasonableness"""
    if not options:
        print("No valid options to analyze")
        return
    
    iv_values = [opt['IV'] for opt in options if opt.get('IV')]
    iv_array = np.array(iv_values)
    
    print(f"IV Statistics:")
    print(f"  Count: {len(iv_values)}")
    print(f"  Min: {iv_array.min():.6f}")
    print(f"  Max: {iv_array.max():.6f}")
    print(f"  Mean: {iv_array.mean():.6f}")
    print(f"  Median: {np.median(iv_array):.6f}")
    
    # Check for unit issues
    high_iv_count = np.sum(iv_array > 1.0)
    low_iv_count = np.sum(iv_array < 0.01)
    
    print(f"\nIV Unit Analysis:")
    print(f"  IVs > 1.0 (likely percentage): {high_iv_count}")
    print(f"  IVs < 0.01 (very low): {low_iv_count}")
    
    if high_iv_count > len(iv_values) * 0.5:
        print("  ⚠️  WARNING: Most IVs > 1.0, likely in percentage form!")
    
    # Sample some values
    print(f"\nSample IV values (first 10):")
    for i, opt in enumerate(options[:10]):
        print(f"  {i+1}: Strike={opt.get('strikePrice')}, IV={opt.get('IV'):.6f}, Type={opt.get('optionType')}")

def analyze_expiries(options):
    """Analyze expiry dates and time to expiry"""
    from collections import Counter
    
    expiries = [opt.get('expiry') for opt in options if opt.get('expiry')]
    expiry_counts = Counter(expiries)
    
    print(f"Expiry Date Distribution:")
    for expiry, count in sorted(expiry_counts.items())[:5]:  # Top 5 expiries
        # Calculate days to expiry
        if isinstance(expiry, str):
            try:
                expiry_date = datetime.strptime(expiry, '%Y-%m-%d').date()
            except:
                expiry_date = expiry
        else:
            expiry_date = expiry
        
        if isinstance(expiry_date, datetime):
            expiry_date = expiry_date.date()
        
        days_to_expiry = (expiry_date - datetime.now().date()).days
        print(f"  {expiry} ({days_to_expiry} days): {count} options")

def analyze_strike_selection(options, spot_price):
    """Analyze which strikes are being used for VIX calculation"""
    if not spot_price:
        print("No spot price available")
        return
    
    # Group by expiry
    from collections import defaultdict
    expiry_groups = defaultdict(list)
    
    for opt in options:
        expiry = opt.get('expiry')
        if expiry:
            expiry_groups[expiry].append(opt)
    
    # Analyze near-term expiry (should be primary for VIX)
    if not expiry_groups:
        print("No expiry groups found")
        return
    
    # Get the nearest expiry
    nearest_expiry = min(expiry_groups.keys())
    nearest_options = expiry_groups[nearest_expiry]
    
    print(f"Nearest expiry: {nearest_expiry}")
    print(f"Options for nearest expiry: {len(nearest_options)}")
    
    # Check ATM strikes
    strikes = [opt['strikePrice'] for opt in nearest_options]
    strikes.sort()
    
    atm_strike = min(strikes, key=lambda x: abs(x - spot_price))
    print(f"Spot price: {spot_price}")
    print(f"Nearest ATM strike: {atm_strike}")
    
    # Check OTM selection
    otm_calls = [opt for opt in nearest_options if opt['optionType'] == 'CE' and opt['strikePrice'] > spot_price]
    otm_puts = [opt for opt in nearest_options if opt['optionType'] == 'PE' and opt['strikePrice'] < spot_price]
    
    print(f"OTM Calls available: {len(otm_calls)}")
    print(f"OTM Puts available: {len(otm_puts)}")

def manual_vix_calculation(options, spot_price):
    """
    Manual VIX calculation following CBOE methodology
    This will show exactly where the calculation might be going wrong
    """
    print("Manual VIX Calculation (CBOE methodology)")
    
    # Filter for near-term expiry
    from collections import defaultdict
    expiry_groups = defaultdict(list)
    
    for opt in options:
        expiry = opt.get('expiry')
        if expiry and opt.get('IV') and opt.get('IV') > 0:
            expiry_groups[expiry].append(opt)
    
    if not expiry_groups:
        print("ERROR: No valid expiry groups")
        return
    
    # Get nearest expiry with sufficient options
    valid_expiries = [(exp, opts) for exp, opts in expiry_groups.items() if len(opts) >= 10]
    if not valid_expiries:
        print("ERROR: No expiry with sufficient options")
        return
    
    nearest_expiry, nearest_options = min(valid_expiries, key=lambda x: x[0])
    
    print(f"Using expiry: {nearest_expiry}")
    print(f"Using {len(nearest_options)} options")
    
    # Calculate time to expiry
    if isinstance(nearest_expiry, str):
        expiry_date = datetime.strptime(nearest_expiry, '%Y-%m-%d')
    else:
        expiry_date = nearest_expiry
    
    now = datetime.now()
    time_to_expiry = (expiry_date - now).total_seconds() / (365.25 * 24 * 3600)  # In years
    
    print(f"Time to expiry: {time_to_expiry:.6f} years ({time_to_expiry * 365:.1f} days)")
    
    if time_to_expiry <= 0:
        print("ERROR: Expiry date is in the past!")
        return
    
    # Separate calls and puts
    calls = [opt for opt in nearest_options if opt['optionType'] == 'CE']
    puts = [opt for opt in nearest_options if opt['optionType'] == 'PE']
    
    print(f"Calls: {len(calls)}, Puts: {len(puts)}")
    
    # Find ATM strike
    all_strikes = list(set([opt['strikePrice'] for opt in nearest_options]))
    all_strikes.sort()
    atm_strike = min(all_strikes, key=lambda x: abs(x - spot_price))
    
    print(f"ATM strike: {atm_strike}")
    
    # Calculate variance contribution
    total_variance = 0
    used_strikes = 0
    
    for strike in all_strikes:
        # Get call and put for this strike
        call = next((opt for opt in calls if opt['strikePrice'] == strike), None)
        put = next((opt for opt in puts if opt['strikePrice'] == strike), None)
        
        if not call and not put:
            continue
        
        # For OTM options, use the appropriate type
        if strike > atm_strike:  # Use calls for strikes above ATM
            option = call
        elif strike < atm_strike:  # Use puts for strikes below ATM
            option = put
        else:  # ATM - use average of call and put
            if call and put:
                # For ATM, average the prices
                avg_price = (call['lastPrice'] + put['lastPrice']) / 2
                avg_iv = (call['IV'] + put['IV']) / 2
                option = {'lastPrice': avg_price, 'IV': avg_iv, 'strikePrice': strike}
            else:
                option = call or put
        
        if not option or not option.get('lastPrice') or option['lastPrice'] <= 0:
            continue
        
        # Calculate delta K (strike interval)
        strike_index = all_strikes.index(strike)
        if strike_index == 0:
            delta_k = all_strikes[1] - all_strikes[0]
        elif strike_index == len(all_strikes) - 1:
            delta_k = all_strikes[-1] - all_strikes[-2]
        else:
            delta_k = (all_strikes[strike_index + 1] - all_strikes[strike_index - 1]) / 2
        
        # VIX formula contribution: (delta_k / strike^2) * option_price
        contribution = (delta_k / (strike ** 2)) * option['lastPrice']
        total_variance += contribution
        used_strikes += 1
        
        if used_strikes <= 5:  # Show first 5 for debugging
            print(f"  Strike {strike}: Price={option['lastPrice']:.2f}, "
                  f"IV={option.get('IV', 0):.4f}, Contribution={contribution:.8f}")
    
    print(f"Total strikes used: {used_strikes}")
    print(f"Total variance sum: {total_variance:.8f}")
    
    if total_variance <= 0:
        print("ERROR: Total variance is zero or negative!")
        return
    
    # Risk-free rate (approximate - you should get actual rate)
    risk_free_rate = 0.065  # 6.5% - adjust as needed
    
    # Forward price calculation (simplified)
    forward_price = spot_price * math.exp(risk_free_rate * time_to_expiry)
    forward_adjustment = (forward_price / spot_price - 1) ** 2
    
    print(f"Risk-free rate: {risk_free_rate * 100:.2f}%")
    print(f"Forward price: {forward_price:.2f}")
    print(f"Forward adjustment: {forward_adjustment:.8f}")
    
    # Final VIX calculation
    variance = (2 / time_to_expiry) * total_variance - forward_adjustment
    
    if variance < 0:
        print("ERROR: Final variance is negative!")
        return
    
    vix = 100 * math.sqrt(variance)
    
    print(f"\nFINAL RESULTS:")
    print(f"Variance: {variance:.8f}")
    print(f"VIX: {vix:.2f}")
    print(f"Expected VIX (market): ~12")
    print(f"Difference: {abs(vix - 12):.2f}")
    
    # Common issues check
    print(f"\nCOMMON ISSUES CHECK:")
    if vix > 50:
        print("❌ VIX too high - possible IV unit issue (percentage vs decimal)")
    if vix < 5:
        print("❌ VIX too low - possible missing options or wrong calculation")
    if used_strikes < 20:
        print("❌ Too few strikes used - may need wider strike range")
    if time_to_expiry < 0.02:  # Less than 7 days
        print("❌ Very short expiry - may cause calculation issues")

# Additional utility functions
def compare_with_black_scholes_iv():
    """
    Compare calculated IVs with expected Black-Scholes values
    """
    print("\n7. BLACK-SCHOLES IV COMPARISON")
    print("-" * 40)
    
    # This would require implementing Black-Scholes IV calculation
    # and comparing with the IVs from your ProductionIVCalculator
    print("(Implement Black-Scholes comparison if needed)")

def check_vix_components():
    """
    Check individual components that go into VIX calculation
    """
    print("\n8. VIX COMPONENTS CHECK")
    print("-" * 40)
    
    # Check risk-free rate source
    # Check option selection criteria
    # Check bid-ask spread impact
    # Check time to expiry calculation
    print("(Additional component checks)")
    
def debug_vix_calculation_simple():
    """Simple debug function to check VIX calculation step by step"""
    print("=== SIMPLE VIX DEBUG ===")
    
    spot_price = get_nifty50_spot_price()
    print(f"1. Spot Price: {spot_price}")
    
    options = fetch_live_option_chain()  # Use fixed version
    if not options:
        print("2. ERROR: No options fetched")
        return
    
    print(f"2. Options fetched: {len(options)}")
    
    # Check IV units
    valid_options = [opt for opt in options if opt.get('IV') and opt.get('IV') > 0]
    print(f"3. Valid options (IV > 0): {len(valid_options)}")
    
    if valid_options:
        ivs = [opt['IV'] for opt in valid_options]
        print(f"4. IV range: {min(ivs):.6f} to {max(ivs):.6f}")
        
        # Check if we need to convert IV units
        high_iv_count = sum(1 for iv in ivs if iv > 1.0)
        if high_iv_count > len(ivs) * 0.5:
            print("5. Converting IVs from percentage to decimal")
            for opt in valid_options:
                if opt['IV'] > 1.0:
                    opt['IV'] = opt['IV'] / 100
        
        # Now calculate VIX
        from core.indicators import calculate_vix, calculate_vix2
        vix_result = calculate_vix(valid_options, spot_price, 300)
        print(f"6. Raw VIX result: {vix_result}")

        vix_result2 = calculate_vix2(valid_options,spot_price, 300)
        print(f"Vix calculation from second method: {vix_result2}")
        
        # Try both with and without scaling
        if vix_result:
            print(f"7. VIX as decimal: {vix_result:.6f}")
            print(f"8. VIX as percentage: {vix_result * 100:.2f}")
            print(f"9. Expected India VIX: ~12")

if __name__ == "__main__":
    debug_vix_calculation_simple()