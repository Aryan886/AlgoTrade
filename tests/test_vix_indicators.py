#!/usr/bin/env python3
"""
Test script to verify VIX calculation with AO and Donchian indicators
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.vix_fetcher import calculate_live_vix
from utils.db_func import fetch_vix_data
import pandas as pd

def test_vix_indicators():
    print("Testing VIX calculation with indicators...")
    
    # Calculate live VIX with indicators
    print("1. Calculating live VIX with indicators...")
    vix_value = calculate_live_vix()
    
    if vix_value is not None:
        print(f"✅ VIX calculated: {vix_value:.2f}")
        
        # Fetch the latest VIX data from database
        print("2. Fetching VIX data from database...")
        vix_df = fetch_vix_data(symbol="NIFTY50")
        
        if not vix_df.empty:
            latest = vix_df.iloc[-1]
            print(f"✅ Latest VIX data from database:")
            print(f"   VIX Value: {latest.get('vix_value', 'N/A')}")
            print(f"   VIX AO: {latest.get('vix_ao_value', 'N/A')}")
            print(f"   VIX Donchian Upper: {latest.get('vix_donchian_upper', 'N/A')}")
            print(f"   VIX Donchian Lower: {latest.get('vix_donchian_lower', 'N/A')}")
            print(f"   VIX Donchian Mid: {latest.get('vix_donchian_mid', 'N/A')}")
            
            # Show last 5 rows to see the indicators
            print(f"\n3. Last 5 VIX entries with indicators:")
            print(vix_df[['vix_value', 'vix_ao_value', 'vix_donchian_upper', 'vix_donchian_lower', 'vix_donchian_mid']].tail())
            
            return True
        else:
            print("❌ No VIX data found in database")
            return False
    else:
        print("❌ Failed to calculate VIX")
        return False

if __name__ == "__main__":
    success = test_vix_indicators()
    if success:
        print("\n🎉 VIX indicators are working correctly!")
    else:
        print("\n❌ VIX indicators test failed!") 