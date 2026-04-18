import sqlite3

db_path = 'db/trading_bot.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get the latest timestamp from option_data
cursor.execute("SELECT MAX(timestamp) FROM option_data WHERE symbol = 'NIFTY50'")
latest_ts = cursor.fetchone()[0]
print(f"Latest option_data timestamp: {latest_ts}")

# Get all strikes for that timestamp
cursor.execute("""
    SELECT DISTINCT strike_price, option_type 
    FROM option_data 
    WHERE timestamp = ? AND symbol = 'NIFTY50'
    ORDER BY strike_price, option_type
""", (latest_ts,))

rows = cursor.fetchall()
print(f"Total unique (strike, type) pairs in latest option_data snapshot: {len(rows)}")

# Show strikes around 22800-23200
relevant_rows = [r for r in rows if 22700 <= r[0] <= 23300]
print(f"\nStrikes in range 22700-23300:")
for strike, opt_type in relevant_rows:
    print(f"  {strike} {opt_type}")

# Check specific strikes
check_strikes = [22800, 22850, 22900, 22950, 23000]
print(f"\n=== SPECIFIC STRIKES IN LATEST option_data ===")
for strike in check_strikes:
    cursor.execute("""
        SELECT option_type, ltp, tradingsymbol, expiry_date
        FROM option_data 
        WHERE timestamp = ? AND symbol = 'NIFTY50' AND strike_price = ?
    """, (latest_ts, strike))
    results = cursor.fetchall()
    if results:
        for r in results:
            print(f"Strike {strike}: {r[0]} ltp={r[1]} symbol={r[2]} exp={r[3]}")
    else:
        print(f"Strike {strike}: NOT FOUND")

conn.close()
