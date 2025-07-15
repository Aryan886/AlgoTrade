import sqlite3
import pandas as pd
from datetime import datetime, timedelta

def check_vix_entries():
    """Check VIX data entries in the database"""
    conn = sqlite3.connect("db/trading_bot.db")
    cursor = conn.cursor()

    # Check vix_data table
    cursor.execute("SELECT COUNT(*) FROM vix_data")
    vix_count = cursor.fetchone()[0]
    print(f"\n[VIX Data Table] Total entries: {vix_count}")

    if vix_count > 0:
        cursor.execute("SELECT * FROM vix_data ORDER BY timestamp DESC LIMIT 5")
        rows = cursor.fetchall()
        print("\n[Latest VIX Data Entries]")
        for row in rows:
            print(f"ID: {row[0]}, Timestamp: {row[1]}, Symbol: {row[2]}, VIX: {row[3]:.2f}, Raw Data: {row[4]}")

    # Check market_data tables for VIX values
    tables = ['market_data_1m', 'market_data_5m', 'market_data_15m']
    
    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"\n[{table}] Total entries: {count}")
            
            if count > 0:
                # Check how many rows have VIX values
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE vix_value IS NOT NULL")
                vix_count = cursor.fetchone()[0]
                print(f"[{table}] Rows with VIX values: {vix_count}")
                
                # Show latest entries with VIX
                cursor.execute(f"SELECT timestamp, symbol, close, vix_value FROM {table} WHERE vix_value IS NOT NULL ORDER BY timestamp DESC LIMIT 3")
                rows = cursor.fetchall()
                print(f"[{table}] Latest entries with VIX:")
                for row in rows:
                    print(f"  {row[0]} | {row[1]} | Close: {row[2]:.2f} | VIX: {row[3]:.2f}")
                    
        except Exception as e:
            print(f"[{table}] Error: {e}")

    conn.close()

def check_vix_trends():
    """Check VIX trends over the last few days"""
    conn = sqlite3.connect("db/trading_bot.db")
    
    # Get VIX data from the last 7 days
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)
    
    query = """
    SELECT timestamp, vix_value 
    FROM vix_data 
    WHERE timestamp >= ? AND timestamp <= ?
    ORDER BY timestamp
    """
    
    df = pd.read_sql_query(query, conn, params=[start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")])
    
    if not df.empty:
        print(f"\n[VIX Trends - Last 7 Days]")
        print(f"Total VIX entries: {len(df)}")
        print(f"VIX Range: {df['vix_value'].min():.2f} - {df['vix_value'].max():.2f}")
        print(f"Average VIX: {df['vix_value'].mean():.2f}")
        print(f"Latest VIX: {df['vix_value'].iloc[-1]:.2f}")
    else:
        print("\n[VIX Trends] No VIX data found in the last 7 days")
    
    conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("VIX Data Verification")
    print("=" * 60)
    
    check_vix_entries()
    check_vix_trends()
    
    print("\n" + "=" * 60)
    print("Verification Complete")
    print("=" * 60)
