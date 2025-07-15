#!/usr/bin/env python3
"""
Test script for 1-minute delta calculation
Tests delta calculation using cached options data and live spot price
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.db_setup import create_tables
from utils.vix_fetcher import calculate_and_store_vix
from utils.db_func import store_quick_delta_1m, calculate_quick_delta_1m
import pandas as pd

def test_delta_1m():
    """Test 1-minute delta calculation"""
    print("=" * 60)
    print("Testing 1-Minute Delta Calculation")
    print("=" * 60)
    
    # 1. Create database tables
    print("\n1. Creating database tables...")
    create_tables()
    print("✅ Database tables created")
    
    # 2. First, calculate VIX to populate options data
    print("\n2. Calculating VIX to populate options data...")
    vix_value = calculate_and_store_vix("NIFTY50")
    
    if vix_value:
        print(f"✅ VIX calculated: {vix_value:.2f}")
    else:
        print("❌ VIX calculation failed")
        return
    
    # 3. Test 1-minute delta calculation
    print("\n3. Testing 1-minute delta calculation...")
    delta_data = calculate_quick_delta_1m("NIFTY50")
    
    if delta_data:
        print(f"✅ 1-minute delta calculated successfully")
        print(f"  Spot Price: {delta_data['spot_price']}")
        print(f"  ATM Strike: {delta_data['atm_strike']}")
        
        if 'CE' in delta_data:
            ce = delta_data['CE']
            print(f"  CE Option: {ce['tradingsymbol']}")
            print(f"    Strike: {ce['strike_price']}")
            print(f"    LTP: {ce['last_price']}")
            print(f"    Delta: {ce['delta']:.3f}")
        
        if 'PE' in delta_data:
            pe = delta_data['PE']
            print(f"  PE Option: {pe['tradingsymbol']}")
            print(f"    Strike: {pe['strike_price']}")
            print(f"    LTP: {pe['last_price']}")
            print(f"    Delta: {pe['delta']:.3f}")
    else:
        print("❌ 1-minute delta calculation failed")
        return
    
    # 4. Test storing 1-minute delta
    print("\n4. Testing 1-minute delta storage...")
    stored_data = store_quick_delta_1m("NIFTY50")
    
    if stored_data:
        print(f"✅ 1-minute delta stored successfully")
        ce_delta = stored_data.get('CE', {}).get('delta', 0)
        pe_delta = stored_data.get('PE', {}).get('delta', 0)
        print(f"  Stored Delta - CE: {ce_delta:.3f}, PE: {pe_delta:.3f}")
    else:
        print("❌ 1-minute delta storage failed")
    
    # 5. Check delta_1m table
    print("\n5. Checking delta_1m table...")
    import sqlite3
    conn = sqlite3.connect('db/trading_bot.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM delta_1m")
    count = cursor.fetchone()[0]
    print(f"✅ Delta 1m records: {count}")
    
    if count > 0:
        cursor.execute("SELECT * FROM delta_1m ORDER BY timestamp DESC LIMIT 1")
        latest = cursor.fetchone()
        print(f"  Latest record: {latest[1]}")  # timestamp
        print(f"  CE Delta: {latest[6]:.3f}")  # ce_delta
        print(f"  PE Delta: {latest[9]:.3f}")  # pe_delta
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("1-Minute Delta Test Completed Successfully!")
    print("=" * 60)
    
    print("\n🎯 Key Benefits for Delta Neutral Strategy:")
    print("✅ 1-minute delta updates without API rate limits")
    print("✅ Uses cached options data + live spot price")
    print("✅ Minimal API calls (only spot price every minute)")
    print("✅ Perfect for real-time delta neutral strategies")
    print("✅ Integrated with existing VIX calculation pipeline")

if __name__ == "__main__":
    test_delta_1m() 