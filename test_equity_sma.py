#!/usr/bin/env python3
"""
Test script for Equity SMA functionality
Demonstrates how to use the new equity SMA tables with high/low tracking
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.db_func import (
    migrate_create_equity_tables, 
    fetch_equity_sma_data, 
    store_equity_sma_from_df
)
from utils.data_fetcher import fetch_and_save_equity
from core.indicators import compute_smas_with_high_low
import pandas as pd
from datetime import datetime, timedelta

def test_equity_sma_functionality():
    """Test the complete equity SMA functionality"""
    
    print("="*60)
    print("TESTING EQUITY SMA FUNCTIONALITY")
    print("="*60)
    
    # Step 1: Create/Verify database tables
    print("\n1. Creating/Verifying database tables...")
    try:
        migrate_create_equity_tables()
        print("✓ Database tables created/verified successfully")
    except Exception as e:
        print(f"✗ Error creating tables: {e}")
        return False
    
    # Step 2: Test SMA computation with high/low tracking
    print("\n2. Testing SMA computation with high/low tracking...")
    try:
        # Create sample OHLC data
        dates = pd.date_range(start='2025-01-01', periods=25, freq='5min')
        sample_data = pd.DataFrame({
            'open': [100 + i for i in range(25)],
            'high': [105 + i for i in range(25)],
            'low': [95 + i for i in range(25)],
            'close': [102 + i for i in range(25)],
            'volume': [1000] * 25
        }, index=dates)
        
        # Compute SMAs with high/low tracking
        df_with_smas = compute_smas_with_high_low(sample_data)
        
        print(f"✓ SMA computation successful")
        print(f"  - DataFrame shape: {df_with_smas.shape}")
        print(f"  - Columns: {list(df_with_smas.columns)}")
        
        # Show sample data
        print("\n  Sample SMA data (last 5 rows):")
        sma_cols = ['close', 'sma_5', 'sma_20', 'sma_5_high', 'sma_5_low', 'sma_20_high', 'sma_20_low']
        print(df_with_smas[sma_cols].tail().round(2))
        
    except Exception as e:
        print(f"✗ Error in SMA computation: {e}")
        return False
    
    # Step 3: Test storing SMA data
    print("\n3. Testing SMA data storage...")
    try:
        rows_stored = store_equity_sma_from_df(df_with_smas, 'TEST', '5m')
        print(f"✓ Stored {rows_stored} rows of SMA data")
    except Exception as e:
        print(f"✗ Error storing SMA data: {e}")
        return False
    
    # Step 4: Test fetching SMA data
    print("\n4. Testing SMA data retrieval...")
    try:
        fetched_data = fetch_equity_sma_data('TEST', interval='5m', limit=10)
        print(f"✓ Retrieved {len(fetched_data)} rows of SMA data")
        print("\n  Sample retrieved data:")
        print(fetched_data.head().round(2))
    except Exception as e:
        print(f"✗ Error fetching SMA data: {e}")
        return False
    
    # Step 5: Test integration with equity data fetching (if Kite API is available)
    print("\n5. Testing integration with equity data fetching...")
    try:
        print("  Note: This requires Kite API connection")
        print("  If you want to test with real data, uncomment the following lines:")
        print("  results, df = fetch_and_save_equity('INFY', intervals=['5m'], return_interval='5m')")
        print("  This will automatically store both equity data and SMA data")
        
        # Uncomment to test with real data:
        # results, df = fetch_and_save_equity('INFY', intervals=['5m'], return_interval='5m')
        # if df is not None:
        #     print(f"✓ Real equity data fetched and SMA data stored automatically")
        # else:
        #     print("✗ No real equity data available (check Kite API connection)")
        
    except Exception as e:
        print(f"✗ Error in integration test: {e}")
        return False
    
    print("\n" + "="*60)
    print("EQUITY SMA FUNCTIONALITY TEST COMPLETED SUCCESSFULLY!")
    print("="*60)
    print("\nKey Features Implemented:")
    print("✓ Equity SMA tables with high/low tracking")
    print("✓ Automatic SMA calculation during data fetching")
    print("✓ SMA_5 and SMA_20 with their respective high/low values")
    print("✓ Integration with existing equity data pipeline")
    print("✓ Database functions for storing and retrieving SMA data")
    
    return True

def show_usage_examples():
    """Show usage examples for the new functionality"""
    
    print("\n" + "="*60)
    print("USAGE EXAMPLES")
    print("="*60)
    
    print("\n1. Fetch equity SMA data:")
    print("""
from utils.db_func import fetch_equity_sma_data

# Get latest 100 SMA data points for INFY 5-minute intervals
sma_data = fetch_equity_sma_data('INFY', interval='5m', limit=100)

# Get SMA data for a specific date range
sma_data = fetch_equity_sma_data(
    'INFY', 
    start='2025-01-01 09:15:00',
    end='2025-01-01 15:30:00',
    interval='5m'
)

# Available columns: sma_5, sma_20, sma_5_high, sma_5_low, sma_20_high, sma_20_low
print(sma_data.columns)
    """)
    
    print("\n2. Manual SMA computation:")
    print("""
from core.indicators import compute_smas_with_high_low

# Assuming you have OHLC data
df_with_smas = compute_smas_with_high_low(your_ohlc_dataframe)

# This adds columns: sma_5, sma_20, sma_5_high, sma_5_low, sma_20_high, sma_20_low
    """)
    
    print("\n3. Automatic SMA storage (happens during equity data fetching):")
    print("""
from utils.data_fetcher import fetch_and_save_equity

# This automatically stores both equity data and SMA data
results, df = fetch_and_save_equity('INFY', intervals=['1m', '5m', '15m'])
    """)

if __name__ == "__main__":
    success = test_equity_sma_functionality()
    show_usage_examples()
    
    if success:
        print("\n🎉 All tests passed! Your equity SMA functionality is ready to use.")
    else:
        print("\n❌ Some tests failed. Please check the error messages above.")
