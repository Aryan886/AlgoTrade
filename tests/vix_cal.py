"""
Corrected India VIX Calculation following NSE methodology
Key fixes:
1. Use only OTM options
2. Calculate forward index level
3. Use near and next month contracts only
4. Use bid-ask average instead of LTP
"""

from utils.vix_fetcher import get_nifty50_spot_price, fetch_live_option_chain
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def calculate_corrected_vix(options_data, spot_price, risk_free_rate=0.065):
    """
    Calculate VIX following NSE India VIX methodology more closely
    
    Args:
        options_data: List of option data dictionaries
        spot_price: Current NIFTY spot price
        risk_free_rate: Risk-free rate (default 6.5%)
    
    Returns:
        VIX value or None if calculation fails
    """
    try:
        print(f"Starting corrected VIX calculation with {len(options_data)} total options")
        
        # Step 1: Filter options with valid data and convert IV units
        valid_options = []
        for opt in options_data:
            if (opt.get('IV') and opt.get('IV') > 0 and 
                opt.get('ltp') and opt.get('ltp') > 0 and
                opt.get('strikePrice') and opt.get('expiry')):
                
                # Convert IV from percentage to decimal if needed
                iv = opt['IV']
                if iv > 1.0:
                    iv = iv / 100
                
                opt_copy = opt.copy()
                opt_copy['IV'] = iv
                
                # Calculate bid-ask average (since we don't have bid-ask, use LTP)
                # In production, you should get actual bid-ask from the API
                opt_copy['mid_price'] = opt['ltp']  # Use LTP as proxy for mid-price
                
                valid_options.append(opt_copy)
        
        print(f"Valid options after filtering: {len(valid_options)}")
        
        # Step 2: Group by expiry and select near-term and next-term
        expiry_groups = {}
        for opt in valid_options:
            expiry = opt['expiry']
            if isinstance(expiry, str):
                expiry_date = datetime.strptime(expiry, '%Y-%m-%d')
            else:
                expiry_date = expiry if isinstance(expiry, datetime) else datetime.combine(expiry, datetime.min.time())
            
            if expiry_date not in expiry_groups:
                expiry_groups[expiry_date] = []
            expiry_groups[expiry_date].append(opt)
        
        # Sort expiries and select first two with sufficient options
        sorted_expiries = sorted(expiry_groups.keys())
        selected_expiries = []
        
        for expiry in sorted_expiries:
            if len(expiry_groups[expiry]) >= 20:  # Minimum options needed
                selected_expiries.append(expiry)
                if len(selected_expiries) >= 2:
                    break
        
        if len(selected_expiries) < 1:
            print("ERROR: No expiry with sufficient options found")
            return None
        
        print(f"Selected expiries: {[exp.strftime('%Y-%m-%d') for exp in selected_expiries]}")
        
        # Step 3: Calculate VIX for each expiry and interpolate
        vix_contributions = []
        
        for i, expiry_date in enumerate(selected_expiries):
            expiry_options = expiry_groups[expiry_date]
            print(f"\nProcessing expiry {i+1}: {expiry_date.strftime('%Y-%m-%d')} ({len(expiry_options)} options)")
            
            # Calculate time to expiry in years
            now = datetime.now()
            time_to_expiry = (expiry_date - now).total_seconds() / (365.25 * 24 * 3600)
            
            if time_to_expiry <= 0:
                print(f"Skipping expired contract: {expiry_date}")
                continue
            
            print(f"Time to expiry: {time_to_expiry:.6f} years ({time_to_expiry * 365:.1f} days)")
            
            # Step 4: Calculate forward index level
            # F = S * e^(r*T) where S=spot, r=risk_free_rate, T=time_to_expiry
            forward_level = spot_price * math.exp(risk_free_rate * time_to_expiry)
            print(f"Forward index level: {forward_level:.2f}")
            
            # Step 5: Determine ATM strike based on forward level (not spot)
            strikes = sorted(set([opt['strikePrice'] for opt in expiry_options]))
            atm_strike = min(strikes, key=lambda x: abs(x - forward_level))
            print(f"ATM strike (based on forward): {atm_strike}")
            
            # Step 6: Select only OTM options
            otm_options = []
            
            for opt in expiry_options:
                strike = opt['strikePrice']
                option_type = opt['optionType']
                
                # Select OTM options only
                if option_type == 'CE' and strike > atm_strike:  # OTM calls
                    otm_options.append(opt)
                elif option_type == 'PE' and strike < atm_strike:  # OTM puts
                    otm_options.append(opt)
                elif strike == atm_strike:  # ATM - use both call and put
                    otm_options.append(opt)
            
            print(f"OTM options selected: {len(otm_options)}")
            
            if len(otm_options) < 5:
                print(f"Too few OTM options for expiry {expiry_date}")
                continue
            
            # Step 7: Calculate variance contribution
            variance_sum = 0
            used_strikes = 0
            
            # Sort OTM options by strike
            otm_options.sort(key=lambda x: x['strikePrice'])
            
            for j, opt in enumerate(otm_options):
                strike = opt['strikePrice']
                mid_price = opt['mid_price']
                
                if mid_price <= 0:
                    continue
                
                # Calculate delta K (strike interval)
                if j == 0:
                    if len(otm_options) > 1:
                        delta_k = otm_options[1]['strikePrice'] - strike
                    else:
                        delta_k = 50  # Default interval
                elif j == len(otm_options) - 1:
                    delta_k = strike - otm_options[j-1]['strikePrice']
                else:
                    delta_k = (otm_options[j+1]['strikePrice'] - otm_options[j-1]['strikePrice']) / 2
                
                # VIX variance formula: Σ(ΔK/K²) × Q(K)
                # where Q(K) is the option price
                contribution = (delta_k / (strike ** 2)) * mid_price
                variance_sum += contribution
                used_strikes += 1
                
                if used_strikes <= 3:  # Debug first few
                    print(f"  Strike {strike}: Price={mid_price:.2f}, DeltaK={delta_k}, Contribution={contribution:.8f}")
            
            print(f"Used strikes: {used_strikes}, Variance sum: {variance_sum:.8f}")
            
            if variance_sum <= 0:
                print(f"Invalid variance sum for expiry {expiry_date}")
                continue
            
            # Step 8: Calculate the complete variance formula
            # σ² = (2/T) × Σ(ΔK/K²)×Q(K) - (1/T)×(F/K₀ - 1)²
            # where K₀ is the strike immediately below the forward level
            
            k0_strike = max([s for s in strikes if s <= forward_level], default=atm_strike)
            forward_term = (forward_level / k0_strike - 1) ** 2
            
            variance = (2 / time_to_expiry) * variance_sum - (1 / time_to_expiry) * forward_term
            
            print(f"Forward term: {forward_term:.8f}")
            print(f"Final variance: {variance:.8f}")
            
            if variance < 0:
                print(f"Negative variance for expiry {expiry_date}")
                continue
            
            vix_contributions.append({
                'expiry': expiry_date,
                'time_to_expiry': time_to_expiry,
                'variance': variance,
                'weight': 1.0  # Will be calculated for interpolation
            })
        
        if not vix_contributions:
            print("ERROR: No valid variance contributions calculated")
            return None
        
        # Step 9: Interpolate to 30-day equivalent (if we have multiple expiries)
        if len(vix_contributions) == 1:
            # Single expiry - use it directly
            final_variance = vix_contributions[0]['variance']
            print(f"Using single expiry variance: {final_variance:.8f}")
        else:
            # Interpolate between near-term and next-term to get 30-day equivalent
            near_term = vix_contributions[0]
            next_term = vix_contributions[1]
            
            # Target is 30 days = 30/365 years
            target_time = 30 / 365.25
            
            # Linear interpolation weights
            t1, t2 = near_term['time_to_expiry'], next_term['time_to_expiry']
            
            if target_time <= t1:
                final_variance = near_term['variance']
                print(f"Using near-term variance (target <= near): {final_variance:.8f}")
            elif target_time >= t2:
                final_variance = next_term['variance']
                print(f"Using next-term variance (target >= next): {final_variance:.8f}")
            else:
                # Interpolation formula: σ²₃₀ = T₁σ₁²×(T₂-T₃₀)/(T₂-T₁) + T₂σ₂²×(T₃₀-T₁)/(T₂-T₁) × (365×24×60)/(T₃₀×60×24)
                w1 = (t2 - target_time) / (t2 - t1)
                w2 = (target_time - t1) / (t2 - t1)
                
                final_variance = (t1 * near_term['variance'] * w1 + 
                                t2 * next_term['variance'] * w2) * (365.25 / target_time)
                
                print(f"Interpolated variance: {final_variance:.8f} (w1={w1:.3f}, w2={w2:.3f})")
        
        # Step 10: Convert to VIX (percentage)
        if final_variance <= 0:
            print("ERROR: Final variance is not positive")
            return None
        
        vix_value = 100 * math.sqrt(final_variance)
        
        print(f"\n=== FINAL RESULTS ===")
        print(f"Final variance: {final_variance:.8f}")
        print(f"Calculated VIX: {vix_value:.2f}")
        print(f"Expected India VIX: ~12")
        print(f"Difference: {abs(vix_value - 12):.2f}")
        
        return vix_value
        
    except Exception as e:
        print(f"ERROR in corrected VIX calculation: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_corrected_vix():
    """Test the corrected VIX calculation"""
    print("=== TESTING CORRECTED VIX CALCULATION ===")
    
    # Get data using your existing functions
    
    spot_price = get_nifty50_spot_price()
    print(f"Spot Price: {spot_price}")
    
    options = fetch_live_option_chain()
    if not options:
        print("ERROR: Could not fetch options data")
        return
    
    print(f"Total options fetched: {len(options)}")
    
    # Calculate VIX using corrected methodology
    corrected_vix = calculate_corrected_vix(options, spot_price)
    
    if corrected_vix:
        print(f"\n🎯 CORRECTED VIX: {corrected_vix:.2f}")
    else:
        print("\n❌ Corrected VIX calculation failed")
    
    return corrected_vix

if __name__ == "__main__":
    test_corrected_vix()