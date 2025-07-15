#!/usr/bin/env python3
"""
Simple test script to check data fetching functionality
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.data_fetcher import Nifty50DataFetcher

def test_data_fetch():
    print("Testing NIFTY 50 data fetching...")
    
    # Initialize fetcher
    fetcher = Nifty50DataFetcher()
    
    # Connect to Kite API
    print("1. Connecting to Kite API...")
    if not fetcher.connect():
        print("❌ Failed to connect to Kite API")
        return False
    print("✅ Connected to Kite API")
    
    # Get instrument token
    print("2. Getting instrument token...")
    token = fetcher.get_instrument_token()
    if not token:
        print("❌ Failed to get instrument token")
        return False
    print(f"✅ Got instrument token: {token}")
    
    # Test fetching 1m data
    print("3. Testing 1m data fetch...")
    from datetime import datetime, timedelta
    now = datetime.now()
    start_time = now - timedelta(hours=1)  # Last 1 hour
    
    df = fetcher.fetch_historical_data('minute', start_time, now)
    if df is not None and not df.empty:
        print(f"✅ Successfully fetched {len(df)} rows of 1m data")
        print(f"Sample data before indicators:\n{df.head()}")
        # Compute indicators
        from strategies.indicators import compute_indicators
        df_ind = compute_indicators(df)
        print(f"\n✅ Indicators computed. Columns after indicators: {df_ind.columns.tolist()}")
        # Print first 30 rows of indicator columns
        print("\nFirst 30 rows of indicator columns:")
        print(df_ind[['ao_value', 'donchian_upper', 'donchian_lower', 'donchian_mid']].head(30))
        # Print summary stats
        print("\nIndicator summary stats:")
        for col in ['ao_value', 'donchian_upper', 'donchian_lower', 'donchian_mid']:
            print(f"{col}: count={df_ind[col].count()}, min={df_ind[col].min()}, max={df_ind[col].max()}")
        
        # Test storing and fetching from database
        print("\n4. Testing database storage and retrieval...")
        from utils.db_func import store_market_data, fetch_market_data
        
        # Store the data with indicators
        store_market_data(df_ind, symbol="NIFTY50", interval="minute")
        
        # Fetch the data back from database
        df_from_db = fetch_market_data(symbol="NIFTY50", interval="minute")
        if df_from_db is not None and not df_from_db.empty:
            print(f"✅ Retrieved {len(df_from_db)} rows from database")
            print("Sample data from database:")
            print(df_from_db[['ao_value', 'donchian_upper', 'donchian_lower', 'donchian_mid']].tail(10))
            
            # Check for NaN values in database
            nan_counts = df_from_db[['ao_value', 'donchian_upper', 'donchian_lower', 'donchian_mid']].isna().sum()
            print(f"\nNaN counts in database: {nan_counts.to_dict()}")
        else:
            print("❌ Failed to retrieve data from database")
        
        return True
    else:
        print("❌ Failed to fetch 1m data")
        return False

if __name__ == "__main__":
    success = test_data_fetch()
    if success:
        print("\n🎉 Data fetching is working correctly!")
    else:
        print("\n❌ Data fetching failed!") 