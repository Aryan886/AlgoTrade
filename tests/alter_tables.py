import sqlite3

DB_PATH = "db/trading_bot.db"

# ✅ Columns as defined in your latest create_equity_table()
EXPECTED_COLUMNS = {
    "timestamp": "TEXT NOT NULL",
    "symbol": "TEXT NOT NULL",
    "open": "REAL",
    "high": "REAL",
    "low": "REAL",
    "close": "REAL",
    "volume": "REAL",
    "vwap": "REAL",
    "ao_value": "REAL",
    "ao_color": "INTEGER",
    "donchian_upper": "REAL",
    "donchian_lower": "REAL",
    "donchian_mid": "REAL"
}

def add_missing_columns():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    intervals = ["1m", "5m", "15m"]

    for interval in intervals:
        tbl = f"equity_data_{interval}"
        print(f"\n🔍 Checking table: {tbl}")

        # Get existing columns
        cur.execute(f"PRAGMA table_info({tbl});")
        existing_cols = [row[1] for row in cur.fetchall()]

        # Find missing ones
        missing = [(col, dtype) for col, dtype in EXPECTED_COLUMNS.items() if col not in existing_cols]

        if not missing:
            print("✅ All columns already exist.")
            continue

        for col, dtype in missing:
            try:
                cur.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {dtype};")
                print(f"🆕 Added column: {col} ({dtype})")
            except sqlite3.OperationalError as e:
                print(f"⚠️ Could not add column {col}: {e}")

    conn.commit()
    conn.close()
    print("\n🎉 Schema update complete!")

if __name__ == "__main__":
    add_missing_columns()
