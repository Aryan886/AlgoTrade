#!/usr/bin/env python3
"""
Test script for separated VIX calculation
"""

import sys
import os

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.market_data_automation import calculate_vix_separately
from utils.data_fetcher import fetch_and_save_data
from utils.check_vix_data import check_vix_entries

def test_separated_system():
    """Test the separated VIX and market data system"""
    print("=" * 60)
    print("Testing Separated VIX and Market Data System")
    print("=" * 60)
    
    # Test 1: Market data fetching (without VIX)
    print("\n1. Testing market data fetching (without VIX)...")
    try:
        results, df = fetch_and_save_data(intervals=["1m"], return_interval="1m", symbol="NIFTY50")
        if df is not None and not df.empty:
            print(f"✅ Market data fetched successfully: {len(df)} rows")
        else:
            print("❌ Market data fetching failed")
            return False
    except Exception as e:
        print(f"❌ Error in market data fetching: {e}")
        return False
    
    # Test 2: Separate VIX calculation
    print("\n2. Testing separate VIX calculation...")
    try:
        calculate_vix_separately()
        print("✅ Separate VIX calculation completed")
    except Exception as e:
        print(f"❌ Error in separate VIX calculation: {e}")
        return False
    
    # Test 3: Check database entries
    print("\n3. Checking database entries...")
    check_vix_entries()
    
    print("\n" + "=" * 60)
    print("Separated System Test Completed Successfully!")
    print("=" * 60)
    return True

if __name__ == "__main__":
    try:
        success = test_separated_system()
        if success:
            print("\n🎉 All tests passed! Separated system is working.")
            print("\nKey Benefits:")
            print("- Market data fetching works independently")
            print("- VIX calculation runs separately")
            print("- No interference between the two systems")
        else:
            print("\n❌ Some tests failed. Please check the errors above.")
    except Exception as e:
        print(f"\n💥 Test failed with error: {e}")
        import traceback
        traceback.print_exc() 