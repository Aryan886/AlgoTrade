#!/usr/bin/env python3
"""
Check and print the latest calculated deltas for all strikes in the configured band for the nearest expiry.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.db_func import fetch_cached_options_data, get_current_delta_from_cache
from config.config import CONFIG
from datetime import datetime

symbol = "NIFTY50"

STRIKE_BAND = CONFIG["STRIKE_BAND"]
STRIKE_INTERVAL = CONFIG["STRIKE_INTERVAL"]

# Fetch cached options data
options = fetch_cached_options_data(symbol)
if not options:
    print("No cached options data found.")
    sys.exit(1)

# Find ATM strike
spot_prices = [opt['spot_price'] for opt in options]
if not spot_prices:
    print("No spot price found in cached options data.")
    sys.exit(1)

spot_price = spot_prices[0]
strikes = [opt['strike_price'] for opt in options]
atm_strike = min(strikes, key=lambda x: abs(x - spot_price))

# Build strike band
strike_band = [atm_strike + i*STRIKE_INTERVAL for i in range(-STRIKE_BAND//STRIKE_INTERVAL, STRIKE_BAND//STRIKE_INTERVAL+1)]

print(f"\nLatest Delta Check for {symbol} (Spot: {spot_price}, ATM: {atm_strike})")
print(f"Strike Band: {strike_band}")
print(f"Checked at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("-"*60)
print(f"{'Strike':>8} | {'Type':>3} | {'Delta':>8}")
print("-"*60)

for strike in strike_band:
    for option_type in ["CE", "PE"]:
        delta = get_current_delta_from_cache(option_type, strike, symbol)
        if delta is not None:
            print(f"{strike:8} | {option_type:>3} | {delta:8.4f}")
        else:
            print(f"{strike:8} | {option_type:>3} | {'N/A':>8}") 