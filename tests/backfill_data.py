import sqlite3
import pandas as pd
from utils.db_func import fetch_market_data, store_sma_from_df
from core.indicators import compute_smas_with_high_low

DB = 'db/trading_bot.db'
SYMBOL = 'NIFTY50'
INTERVALS = ['1m','5m','15m']          # adjust as needed
LIMIT = 1000                           # <--- only last 1000 rows

def rewrite_sma(interval):
    print(f"recomputing {interval}…")
    # pull just the most recent 1 000 candles
    df = fetch_market_data(symbol=SYMBOL,
                           interval=interval,
                           limit=LIMIT,
                           db_path=DB)
    if df.empty:
        print("  no data – skipping")
        return

    # recompute using whatever logic your updated function now contains
    df_smas = compute_smas_with_high_low(df)

    # overwrite the table for those timestamps
    store_sma_from_df(df_smas, SYMBOL, interval, db_path=DB)
    print(f"  wrote {len(df_smas)} rows")

if __name__ == "__main__":
    for iv in INTERVALS:
        rewrite_sma(iv)
    print("done")