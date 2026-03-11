"""
Forward-fill NULL SL/SH values for March 2026 (up to current date) with the last known non-NULL values.
This fills NULL values with the most recent calculated SL/SH until a new one is formed.
"""

import sqlite3
import pandas as pd
from datetime import datetime

DB_PATH = 'db/trading_bot.db'

def forward_fill_sl_sh_for_interval(interval: str, db_path: str = DB_PATH) -> dict:
    """
    Forward-fill NULL SL/SH values for March 2026 (up to current date) with last known non-NULL values.

    Returns:
        dict with keys: interval, updated_rows, last_sl, last_sh
    """
    table_name = f"market_data_{interval}"
    print(f"\n{'='*60}")
    print(f"Forward-filling {interval} interval ({table_name})")
    print(f"{'='*60}")

    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    # Get the last non-NULL SL/SH values before March 2026
    print(f"[1/3] Finding last known SL/SH values...")
    cursor.execute(f"""
        SELECT SL, SH
        FROM {table_name}
        WHERE symbol = 'NIFTY50'
        AND SL IS NOT NULL AND SH IS NOT NULL
        AND timestamp < '2026-03-01 00:00:00'
        ORDER BY timestamp DESC
        LIMIT 1
    """)

    last_values = cursor.fetchone()
    if not last_values:
        print(f"[WARN] No previous SL/SH values found for {table_name}")
        conn.close()
        return {
            'interval': interval,
            'updated_rows': 0,
            'last_sl': None,
            'last_sh': None,
            'error': 'No previous values found'
        }

    last_sl, last_sh = last_values
    print(f"     Last SL: {last_sl}, Last SH: {last_sh}")

    # Count NULL values in March 2026 up to current date
    cursor.execute(f"""
        SELECT COUNT(*)
        FROM {table_name}
        WHERE symbol = 'NIFTY50'
        AND (SL IS NULL OR SH IS NULL)
        AND timestamp >= '2026-03-01 00:00:00'
        AND timestamp <= datetime('now')
    """)

    null_count = cursor.fetchone()[0]
    print(f"     NULL values in March 2026 (up to today): {null_count}")

    if null_count == 0:
        print(f"[INFO] No NULL values to fill for {table_name}")
        conn.close()
        return {
            'interval': interval,
            'updated_rows': 0,
            'last_sl': last_sl,
            'last_sh': last_sh,
            'error': 'No NULL values to fill'
        }

    # Update NULL SL values in March 2026 up to current date
    print(f"[2/3] Updating NULL SL values...")
    cursor.execute(f"""
        UPDATE {table_name}
        SET SL = ?
        WHERE symbol = 'NIFTY50'
        AND SL IS NULL
        AND timestamp >= '2026-03-01 00:00:00'
        AND timestamp <= datetime('now')
    """, (last_sl,))

    sl_updated = cursor.rowcount
    print(f"     Updated {sl_updated} SL values")

    # Update NULL SH values in March 2026 up to current date
    print(f"[3/3] Updating NULL SH values...")
    cursor.execute(f"""
        UPDATE {table_name}
        SET SH = ?
        WHERE symbol = 'NIFTY50'
        AND SH IS NULL
        AND timestamp >= '2026-03-01 00:00:00'
        AND timestamp <= datetime('now')
    """, (last_sh,))

    sh_updated = cursor.rowcount
    print(f"     Updated {sh_updated} SH values")

    conn.commit()
    conn.close()

    total_updated = sl_updated + sh_updated
    print(f"     Total updated: {total_updated}")

    return {
        'interval': interval,
        'updated_rows': total_updated,
        'last_sl': last_sl,
        'last_sh': last_sh,
        'error': None
    }

def forward_fill_all_intervals(db_path: str = DB_PATH):
    """
    Forward-fill NULL SL/SH values for March 2026 (up to current date) across all intervals.
    """
    print("\n" + "="*60)
    print("FORWARD-FILL SL/SH VALUES FOR MARCH 2026")
    print("="*60)
    print(f"Database: {db_path}")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = []
    intervals = ['1m', '5m', '15m']

    for interval in intervals:
        try:
            result = forward_fill_sl_sh_for_interval(interval, db_path)
            results.append(result)
        except Exception as e:
            print(f"[ERROR] Failed to process {interval}: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                'interval': interval,
                'updated_rows': 0,
                'last_sl': None,
                'last_sh': None,
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
            if result['last_sl'] is not None:
                print(f"           Last SL: {result['last_sl']}, Last SH: {result['last_sh']}")

    print(f"\nEnd time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

if __name__ == "__main__":
    forward_fill_all_intervals()
