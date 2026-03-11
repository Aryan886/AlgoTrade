"""
Deduplication script for market_data tables.
For each duplicate (symbol, timestamp) pair, keeps the row with the highest id
and deletes the older duplicate(s).
"""

import sqlite3
from datetime import datetime

DB_PATH = 'db/trading_bot.db'


def dedup_table(interval: str, db_path: str = DB_PATH) -> dict:
    table_name = f"market_data_{interval}"
    print(f"\n{'='*60}")
    print(f"Deduplicating {table_name}")
    print(f"{'='*60}")

    conn = sqlite3.connect(db_path, timeout=60)
    cursor = conn.cursor()

    # Count duplicates before
    print(f"[1/3] Counting duplicates...")
    cursor.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT timestamp FROM {table_name}
            WHERE symbol = 'NIFTY50'
            GROUP BY timestamp
            HAVING COUNT(*) > 1
        )
    """)
    dup_timestamp_count = cursor.fetchone()[0]
    print(f"      Duplicate timestamps: {dup_timestamp_count}")

    if dup_timestamp_count == 0:
        print(f"      No duplicates found, skipping.")
        conn.close()
        return {'interval': interval, 'duplicates_found': 0, 'rows_deleted': 0}

    # Count total rows to be deleted
    cursor.execute(f"""
        SELECT COUNT(*) FROM {table_name}
        WHERE symbol = 'NIFTY50'
        AND id NOT IN (
            SELECT MAX(id) FROM {table_name}
            WHERE symbol = 'NIFTY50'
            GROUP BY timestamp
        )
    """)
    rows_to_delete = cursor.fetchone()[0]
    print(f"      Rows to delete: {rows_to_delete}")

    # Delete duplicates, keeping the row with the MAX id per (symbol, timestamp)
    print(f"[2/3] Deleting duplicates (keeping highest id)...")
    cursor.execute(f"""
        DELETE FROM {table_name}
        WHERE symbol = 'NIFTY50'
        AND id NOT IN (
            SELECT MAX(id) FROM {table_name}
            WHERE symbol = 'NIFTY50'
            GROUP BY timestamp
        )
    """)
    deleted = cursor.rowcount
    conn.commit()
    print(f"      Deleted {deleted} rows")

    # Verify
    print(f"[3/3] Verifying...")
    cursor.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT timestamp FROM {table_name}
            WHERE symbol = 'NIFTY50'
            GROUP BY timestamp
            HAVING COUNT(*) > 1
        )
    """)
    remaining_dups = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE symbol = 'NIFTY50'")
    final_count = cursor.fetchone()[0]
    print(f"      Remaining duplicates: {remaining_dups}")
    print(f"      Final row count: {final_count}")

    conn.close()
    return {
        'interval': interval,
        'duplicates_found': dup_timestamp_count,
        'rows_deleted': deleted,
        'remaining_dups': remaining_dups,
        'final_count': final_count
    }


def dedup_all(db_path: str = DB_PATH):
    print("\n" + "="*60)
    print("DEDUPLICATION SCRIPT FOR NIFTY50")
    print("="*60)
    print(f"Database: {db_path}")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = []
    for interval in ['1m', '5m', '15m']:
        try:
            result = dedup_table(interval, db_path)
            results.append(result)
        except Exception as e:
            print(f"[ERROR] Failed to dedup {interval}: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for r in results:
        print(f"{r['interval']}: {r['rows_deleted']} rows deleted, "
              f"{r.get('final_count', '?')} rows remaining, "
              f"{r.get('remaining_dups', '?')} duplicates left")

    print(f"\nEnd time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)


if __name__ == "__main__":
    dedup_all()