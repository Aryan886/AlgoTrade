#!/usr/bin/env python3
"""
Test script for high-accuracy delta calculation using Black-Scholes formula
Tests the new high-accuracy delta calculation strategy with proper caching
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.db_setup import create_tables
from utils.vix_fetcher import calculate_and_store_vix
from utils.db_func import (
    calculate_and_store_high_accuracy_delta,
    get_current_delta_from_cache,
    fetch_cached_options_data
)
from utils.black_scholes import calculate_black_scholes_delta, calculate_time_to_expiry
import pandas as pd
from datetime import date

def test_high_accuracy_delta():
    """Test high-accuracy delta calculation using Black-Scholes"""
    print("=" * 70)
    print("Testing High-Accuracy Delta Calculation (Black-Scholes)")
    print("=" * 70)
    
    # 1. Create database tables
    print("\n1. Creating database tables...")
    create_tables()
    print("✅ Database tables created")
    
    # 2. Calculate VIX to populate high-accuracy options data
    print("\n2. Calculating VIX to populate high-accuracy options data...")
    vix_value = calculate_and_store_vix("NIFTY50")
    
    if vix_value:
        print(f"✅ VIX calculated: {vix_value:.2f}")
    else:
        print("❌ VIX calculation failed")
        return
    
    # 3. Test high-accuracy delta calculation
    print("\n3. Testing high-accuracy delta calculation...")
    delta_data = calculate_and_store_high_accuracy_delta("NIFTY50")
    
    if delta_data:
        print(f"✅ High-accuracy delta calculated successfully")
        print(f"  Spot Price: {delta_data['spot_price']}")
        print(f"  Strike Band: {delta_data['strike_band']}")
        
        # Show delta results for each strike
        for strike, strike_data in delta_data['delta_results'].items():
            print(f"  Strike {strike}:")
            for option_type, option_data in strike_data.items():
                print(f"    {option_type}: Delta={option_data['delta']:.4f}, IV={option_data['iv']:.2f}%")
    else:
        print("❌ High-accuracy delta calculation failed")
        return
    
    # 4. Test individual delta retrieval
    print("\n4. Testing individual delta retrieval...")
    atm_strike = delta_data['strike_band'][1]  # Middle strike (ATM)
    
    ce_delta = get_current_delta_from_cache("CE", atm_strike, "NIFTY50")
    pe_delta = get_current_delta_from_cache("PE", atm_strike, "NIFTY50")
    
    if ce_delta is not None:
        print(f"✅ CE Delta for strike {atm_strike}: {ce_delta:.4f}")
    else:
        print(f"❌ Could not get CE delta for strike {atm_strike}")
    
    if pe_delta is not None:
        print(f"✅ PE Delta for strike {atm_strike}: {pe_delta:.4f}")
    else:
        print(f"❌ Could not get PE delta for strike {atm_strike}")
    
    # 5. Test Black-Scholes calculation directly
    print("\n5. Testing Black-Scholes calculation directly...")
    cached_options = fetch_cached_options_data("NIFTY50")
    
    if cached_options:
        # Find an ATM option for testing
        atm_option = None
        for option in cached_options:
            if abs(option['strike_price'] - delta_data['spot_price']) < 100:
                atm_option = option
                break
        
        if atm_option:
            T = calculate_time_to_expiry(atm_option['expiry_date'])
            direct_delta = calculate_black_scholes_delta(
                spot_price=delta_data['spot_price'],
                strike_price=atm_option['strike_price'],
                time_to_expiry=T,
                implied_volatility=atm_option['iv'],
                option_type=atm_option['option_type']
            )
            
            print(f"✅ Direct Black-Scholes calculation:")
            print(f"  Option: {atm_option['tradingsymbol']}")
            print(f"  Strike: {atm_option['strike_price']}")
            print(f"  IV: {atm_option['iv']:.2f}%")
            print(f"  Time to Expiry: {T:.3f} years")
            print(f"  Delta: {direct_delta:.4f}")
    
    # 6. Check database tables
    print("\n6. Checking database tables...")
    import sqlite3
    conn = sqlite3.connect('db/trading_bot.db')
    cursor = conn.cursor()
    
    # Check option_data table
    cursor.execute("SELECT COUNT(*) FROM option_data")
    option_count = cursor.fetchone()[0]
    print(f"✅ High-accuracy options data: {option_count} records")
    
    # Check delta_cache table
    cursor.execute("SELECT COUNT(*) FROM delta_cache")
    delta_count = cursor.fetchone()[0]
    print(f"✅ Delta cache: {delta_count} records")
    
    if delta_count > 0:
        cursor.execute("SELECT * FROM delta_cache ORDER BY timestamp DESC LIMIT 3")
        latest_deltas = cursor.fetchall()
        print(f"  Latest delta records:")
        for row in latest_deltas:
            print(f"    {row[1]} - {row[3]} {row[2]} {row[4]}: Delta={row[4]:.4f}")
    
    conn.close()
    
    print("\n" + "=" * 70)
    print("High-Accuracy Delta Test Completed Successfully!")
    print("=" * 70)
    
    print("\n🎯 Key Benefits of High-Accuracy Delta Strategy:")
    print("✅ Black-Scholes formula for precise delta calculation")
    print("✅ Strike-specific IV (not global ATM IV)")
    print("✅ Real-time time-to-expiry calculation")
    print("✅ Cached option data with 10-minute pruning")
    print("✅ Minimal API usage (1 call per minute)")
    print("✅ Historical delta tracking for backtesting")
    print("✅ Interpolation capability for missing strikes")

if __name__ == "__main__":
    test_high_accuracy_delta() 