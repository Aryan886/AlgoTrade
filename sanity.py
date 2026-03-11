import sqlite3
conn = sqlite3.connect('db/trading_bot.db')
cursor = conn.cursor()

# Check if duplicates are widespread or isolated to a specific time range
cursor.execute("""
    SELECT MIN(timestamp), MAX(timestamp), COUNT(*) 
    FROM (
        SELECT timestamp
        FROM market_data_1m 
        WHERE symbol='NIFTY50'
        GROUP BY timestamp
        HAVING COUNT(*) > 1
    )
""")
print(cursor.fetchone())
conn.close()