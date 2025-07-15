#!/usr/bin/env python3
"""
Test script for live VIX calculation using Kite API
"""

import sys
import os

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.vix_fetcher import calculate_and_store_vix, get_nifty50_spot_price, fetch_live_option_chain
from utils.check_vix_data import check_vix_entries, check_vix_trends

def test_live_vix():
    """Test live VIX calculation"""
    print("=" * 60)
    print("Testing Live VIX Calculation")
    print("=" * 60)
    
    # Test 1: Get NIFTY 50 spot price
    print("\n1. Testing NIFTY 50 spot price fetch...")
    spot_price = get_nifty50_spot_price()
    if spot_price:
        print(f"✅ NIFTY 50 spot price: {spot_price}")
    else:
        print("❌ Failed to get NIFTY 50 spot price")
        return False
    
    # Test 2: Fetch live option chain
    print("\n2. Testing live option chain fetch...")
    option_data = fetch_live_option_chain()
    if option_data and len(option_data) > 0:
        print(f"✅ Found {len(option_data)} NIFTY options")
        print(f"   Sample option: {option_data[0]}")
    else:
        print("❌ Failed to fetch live option chain")
        return False
    
    # Test 3: Calculate and store VIX
    print("\n3. Testing VIX calculation and storage...")
    vix_value = calculate_and_store_vix("NIFTY50")
    if vix_value:
        print(f"✅ Live VIX calculated: {vix_value:.2f}")
    else:
        print("❌ Failed to calculate VIX")
        return False
    
    # Test 4: Check database entries
    print("\n4. Checking database entries...")
    check_vix_entries()
    check_vix_trends()
    
    print("\n" + "=" * 60)
    print("Live VIX Test Completed Successfully!")
    print("=" * 60)
    return True

if __name__ == "__main__":
    try:
        success = test_live_vix()
        if success:
            print("\n🎉 All tests passed! Live VIX calculation is working.")
        else:
            print("\n❌ Some tests failed. Please check the errors above.")
    except Exception as e:
        print(f"\n💥 Test failed with error: {e}")
        import traceback
        traceback.print_exc() 