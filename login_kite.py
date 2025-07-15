#!/usr/bin/env python3
"""
Kite API Login Script

This script helps authenticate with Kite API before running the live data automation.
Only need to run this once, or when access token expires.

"""

import sys
import os

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from broker.zerodha_client import login_kite
import logging

def main():
    """Main function to login to Kite API"""
    print("=" * 60)
    print("Kite API Authentication")
    print("=" * 60)
    print("This will help you login to Kite API for live data access.")
    print("You only need to do this once, or when your token expires.")
    print("=" * 60)
    
    try:
        # Attempt to login
        kite = login_kite()
        
        if kite:
            print("\n✅ Login successful!")
            print("Your access token has been saved.")
            print("You can now run the live data automation.")
            print("\nNext steps:")
            print("1. Run: python run_live_data.py")
            print("2. The automation will start automatically during market hours")
        else:
            print("\n❌ Login failed!")
            print("Please check your API credentials and try again.")
            
    except Exception as e:
        print(f"\n❌ Error during login: {e}")
        print("Please ensure:")
        print("1. Your API key and secret are correct in config/secrets.yaml")
        print("2. You have a stable internet connection")
        print("3. You follow the login URL and paste the request token correctly")

if __name__ == "__main__":
    main() 