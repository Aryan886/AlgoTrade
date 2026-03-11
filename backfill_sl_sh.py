"""
Backfill SL/SH values for NIFTY50 market data.
This script recalculates SL (skipping low) and SH (skipping high) values
using the nifty_skipping_low() and nifty_skipping_high() functions from indicators.py
and updates the database.
"""

import sqlite3
import pandas as pd
from datetime import datetime
from core.indicators import nifty_skipping_low, nifty_skipping_high, compute_smas_with_high_low

DB_PATH = 'db/trading_bot.db'

def backfill_sl_sh_for_interval(interval: str, db_path: str = DB_PATH) -> dict:
    """
    Backfill SL/SH for a specific interval.
    Fetches full NIFTY50 history, computes SL/SH, and bulk-updates the DB.
    """
    table_name = f"market_data_{interval}"
    print(f"\n{'='*60}")
    print(f"Processing {interval} interval ({table_name})")
    print(f"{'='*60}")

    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    # Get total row count
    print(f"[1/4] Getting total row count...")
    cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE symbol = 'NIFTY50'")
    total_rows = cursor.fetchone()[0]
    print(f"      Total rows: {total_rows}")

    if total_rows == 0:
        print(f"[WARN] No data found for NIFTY50 in {table_name}")
        conn.close()
        return {
            'interval': interval,
            'total_rows': 0,
            'updated_rows': 0,
            'sl_non_null': 0,
            'sh_non_null': 0,
            'error': 'No data found'
        }

    # Fetch the full interval history so skipping state is recalculated end-to-end
    print(f"[2/4] Fetching full history...")
    query = f"""
        SELECT id, timestamp, symbol, open, high, low, close,
               ao_value, donchian_upper, donchian_lower, donchian_mid
        FROM {table_name}
        WHERE symbol = 'NIFTY50'
        ORDER BY timestamp
    """

    df = pd.read_sql_query(query, conn, parse_dates=['timestamp'])
    print(f"      Fetched {len(df)} rows")

    if df.empty:
        print(f"[WARN] No data fetched for NIFTY50 in {table_name}")
        conn.close()
        return {
            'interval': interval,
            'total_rows': 0,
            'updated_rows': 0,
            'sl_non_null': 0,
            'sh_non_null': 0,
            'error': 'No data found'
        }

    row_ids = df['id'].tolist()
    df = df.drop(columns=['id'])

    # Set timestamp as index for indicator functions
    df.set_index('timestamp', inplace=True)

    # Lowercase columns for consistency
    df.columns = [col.lower() for col in df.columns]

    print(f"[3/4] Computing indicators...")
    df_sma = compute_smas_with_high_low(df.copy())

    df_sl = nifty_skipping_low(df_sma.copy())
    sl_values = df_sl['SL'].copy()

    df_sh = nifty_skipping_high(df_sma.copy())
    sh_values = df_sh['SH'].copy()

    # Set first 7 rows to NULL (insufficient history)
    print(f"      Setting first 7 rows to NULL (insufficient history)...")
    sl_values.iloc[:7] = None
    sh_values.iloc[:7] = None

    # Ensure index exists on (symbol, timestamp) for fast lookups
    print(f"[4/4] Ensuring index exists on (symbol, timestamp)...")
    cursor.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_{table_name}_symbol_timestamp
        ON {table_name} (symbol, timestamp)
    """)
    conn.commit()
    print(f"      Index ready. Updating database (bulk)...")
    updates = []
    for row_id, sl, sh in zip(row_ids, sl_values, sh_values):
        sl_val = None if pd.isna(sl) else float(sl)
        sh_val = None if pd.isna(sh) else float(sh)
        updates.append((sl_val, sh_val, int(row_id)))

    update_query = f"""
        UPDATE {table_name}
        SET SL = ?, SH = ?
        WHERE id = ?
    """

    cursor.executemany(update_query, updates)
    conn.commit()

    updated_count = cursor.rowcount  # rows affected by last executemany
    sl_non_null = sl_values.notna().sum()
    sh_non_null = sh_values.notna().sum()

    print(f"      Updated {len(updates)} rows")
    print(f"      SL non-NULL: {sl_non_null}/{len(df)}")
    print(f"      SH non-NULL: {sh_non_null}/{len(df)}")

    conn.close()

    return {
        'interval': interval,
        'total_rows': len(df),
        'updated_rows': len(updates),
        'sl_non_null': int(sl_non_null),
        'sh_non_null': int(sh_non_null),
        'error': None
    }


def backfill_all_intervals(db_path: str = DB_PATH):
    """
    Backfill SL/SH for all intervals (1m, 5m, 15m).
    """
    print("\n" + "="*60)
    print("SL/SH BACKFILL SCRIPT FOR NIFTY50")
    print("="*60)
    print(f"Database: {db_path}")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = []
    intervals = ['1m', '5m', '15m']

    for interval in intervals:
        try:
            result = backfill_sl_sh_for_interval(interval, db_path)
            results.append(result)
        except Exception as e:
            print(f"[ERROR] Failed to process {interval}: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                'interval': interval,
                'total_rows': 0,
                'updated_rows': 0,
                'sl_non_null': 0,
                'sh_non_null': 0,
                'error': str(e)
            })

    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for result in results:
        if result['error']:
            print(f"{result['interval']}: ERROR - {result['error']}")
        else:
            print(f"{result['interval']}: {result['updated_rows']} rows updated")
            print(f"           SL non-NULL: {result['sl_non_null']}, SH non-NULL: {result['sh_non_null']}")

    print(f"\nEnd time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)


if __name__ == "__main__":
    backfill_all_intervals()
