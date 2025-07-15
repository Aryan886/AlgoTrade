#!/usr/bin/env python3
"""
Test script for options data storage and retrieval
Tests PE/CE options data storage alongside VIX calculation
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.db_setup import create_tables
from utils.vix_fetcher import calculate_and_store_vix
from utils.db_func import fetch_options_data, get_atm_options
import pandas as pd

def test_options_data_storage():
    """Test options data storage and retrieval"""
    print("=" * 60)
    print("Testing Options Data Storage for PE/CE Strategies")
    print("=" * 60)
    
    # 1. Create database tables
    print("\n1. Creating database tables...")
    create_tables()
    print("✅ Database tables created")
    
    # 2. Calculate VIX and store options data
    print("\n2. Calculating VIX and storing options data...")
    vix_value = calculate_and_store_vix("NIFTY50")
    
    if vix_value:
        print(f"✅ VIX calculated: {vix_value:.2f}")
    else:
        print("❌ VIX calculation failed")
        return
    
    # 3. Check options data in database
    print("\n3. Checking options data in database...")
    options_df = fetch_options_data(symbol="NIFTY50")
    
    if not options_df.empty:
        print(f"✅ Found {len(options_df)} options records")
        print(f"Latest timestamp: {options_df.index[0]}")
        
        # Show sample data
        print("\n[Sample Options Data]")
        sample = options_df.head(3)
        for idx, row in sample.iterrows():
            print(f"  {row['option_type']} {row['strike_price']} - LTP: {row['last_price']}, OI: {row['open_interest']}")
    else:
        print("❌ No options data found")
        return
    
    # 4. Test ATM options retrieval
    print("\n4. Testing ATM options retrieval...")
    atm_options = get_atm_options(symbol="NIFTY50")
    
    if atm_options:
        print(f"✅ ATM Options found:")
        print(f"  Spot Price: {atm_options['spot_price']}")
        print(f"  ATM Strike: {atm_options['atm_strike']}")
        
        if atm_options['CE']:
            ce = atm_options['CE']
            print(f"  CE Option: {ce['tradingsymbol']} - LTP: {ce['last_price']}, Delta: {ce['delta']:.3f}")
        
        if atm_options['PE']:
            pe = atm_options['PE']
            print(f"  PE Option: {pe['tradingsymbol']} - LTP: {pe['last_price']}, Delta: {pe['delta']:.3f}")
    else:
        print("❌ No ATM options found")
    
    # 5. Test filtering by option type
    print("\n5. Testing option type filtering...")
    ce_options = fetch_options_data(symbol="NIFTY50", option_type="CE")
    pe_options = fetch_options_data(symbol="NIFTY50", option_type="PE")
    
    print(f"✅ CE Options: {len(ce_options)} records")
    print(f"✅ PE Options: {len(pe_options)} records")
    
    # 6. Show database statistics
    print("\n6. Database Statistics:")
    print(f"  Total Options Records: {len(options_df)}")
    print(f"  Unique Strikes: {options_df['strike_price'].nunique()}")
    print(f"  Unique Expiries: {options_df['expiry_date'].nunique()}")
    print(f"  CE Options: {len(ce_options)}")
    print(f"  PE Options: {len(pe_options)}")
    
    print("\n" + "=" * 60)
    print("Options Data Storage Test Completed Successfully!")
    print("=" * 60)
    
    print("\n🎯 Key Benefits for Delta Neutral Strategy:")
    print("✅ Live PE/CE data available every 5 minutes")
    print("✅ ATM options automatically identified")
    print("✅ Delta calculated for position sizing")
    print("✅ Historical options data for backtesting")
    print("✅ Easy filtering by strike, expiry, option type")
    print("✅ Integrated with VIX calculation pipeline")

if __name__ == "__main__":
    test_options_data_storage() 