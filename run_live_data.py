#!/usr/bin/env python3
"""
Live Data Automation Startup Script

This script starts the automated market data fetching during market hours.
It will automatically fetch 1m, 5m, and 15m data for NIFTY 50.

Usage:
    python run_live_data.py

Features:
    - Runs only during market hours (9:15 AM to 3:30 PM IST, Monday to Friday)
    - Handles market holidays
    - Automatic retry on failures
    - Logs all activities
    - Resource efficient with rate limiting
"""

import sys
import os

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.market_data_automation import MarketDataAutomation
import logging

def main():
    """Main function to start the live data automation"""
    print("=" * 60)
    print("NIFTY 50 Live Data Automation")
    print("=" * 60)
    print("This will automatically fetch market data during trading hours.")
    print("Market Hours: 9:15 AM to 3:30 PM IST (Monday to Friday)")
    print("Data Intervals: 1m, 5m, 15m")
    print("=" * 60)
    
    # Create automation instance
    automation = MarketDataAutomation()
    
    # Test connection first
    print("\nTesting connection to Kite API...")
    if not automation.test_connection():
        print("❌ Failed to connect to Kite API!")
        print("\nPlease follow these steps:")
        print("1. Run: python login_kite.py")
        print("2. Follow the login process")
        print("3. Then run this script again: python run_live_data.py")
        print("\nOr ensure:")
        print("- Your API credentials are correct in config/secrets.yaml")
        print("- Your internet connection is working")
        return
    
    print("✅ Connection test successful!")
    
    # Show current market status
    status = automation.get_status()
    if status['market_open']:
        print("✅ Market is currently OPEN")
    else:
        print("❌ Market is currently CLOSED")
        print("The automation will start automatically when market opens.")
    
    print("\nStarting automation...")
    print("Press Ctrl+C to stop the automation")
    print("-" * 60)
    
    try:
        automation.start()
        
        # Keep running and show status
        while automation.is_running:
            import time
            time.sleep(60)  # Update status every minute
            
            current_status = automation.get_status()
            if current_status['market_open']:
                print(f"🟢 Market OPEN - Last fetch times: {current_status['last_fetch_times']}")
            else:
                print("🔴 Market CLOSED - Waiting for market to open...")
                
    except KeyboardInterrupt:
        print("\n🛑 Received stop signal...")
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        automation.stop()
        print("✅ Automation stopped successfully")

if __name__ == "__main__":
    main() 