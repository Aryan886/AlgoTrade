import pandas as pd
import sqlite3
import os
from datetime import datetime

def simple_data_test():
    """Simple test to verify data exists in database"""
    
    print("SIMPLE DATA AVAILABILITY TEST")
    print("="*50)
    
    # FIXED: Use the correct path that matches your db_func.py
    db_path = "db/trading_bot.db"  # This was the issue!
    
    print(f"Database path: {db_path}")
    print(f"Database exists: {os.path.exists(db_path)}")
    print(f"Absolute path: {os.path.abspath(db_path)}")
    
    try:
        # Connect to database with correct path
        conn = sqlite3.connect(db_path)
        
        # Test 1: Check if tables exist and have data
        print("\n1. TABLE EXISTENCE & ROW COUNTS:")
        tables_to_check = ['market_data_5m', 'market_data_15m', 'vix_data']
        
        for table in tables_to_check:
            try:
                count_query = f"SELECT COUNT(*) FROM {table}"
                count = pd.read_sql_query(count_query, conn).iloc[0, 0]
                print(f"  {table}: {count} rows")
                
                if count > 0:
                    # Get latest timestamp
                    latest_query = f"SELECT MAX(timestamp) FROM {table}"
                    latest = pd.read_sql_query(latest_query, conn).iloc[0, 0]
                    print(f"    Latest timestamp: {latest}")
                
            except Exception as e:
                print(f"  {table}: ERROR - {e}")
        
        # Test 2: Get recent NIFTY50 data directly
        print("\n2. RECENT NIFTY50 DATA (Direct Query):")
        
        for interval in ['5m', '15m']:
            try:
                query = f"""
                SELECT timestamp, open, high, low, close, volume 
                FROM market_data_{interval} 
                WHERE symbol='NIFTY50' 
                ORDER BY timestamp DESC 
                LIMIT 3
                """
                df = pd.read_sql_query(query, conn)
                print(f"\n  market_data_{interval} (last 3 rows):")
                if not df.empty:
                    print(df.to_string(index=False))
                else:
                    print("    NO DATA FOUND!")
                    
            except Exception as e:
                print(f"    ERROR: {e}")
        
        # Test 3: Get VIX data
        print("\n3. RECENT VIX DATA:")
        try:
            vix_query = """
            SELECT timestamp, vix_value
            FROM vix_data 
            WHERE symbol='NIFTY50' 
            ORDER BY timestamp DESC 
            LIMIT 3
            """
            vix_df = pd.read_sql_query(vix_query, conn)
            if not vix_df.empty:
                print(vix_df.to_string(index=False))
            else:
                print("NO VIX DATA FOUND!")
                
        except Exception as e:
            print(f"VIX ERROR: {e}")
        
        conn.close()
        
        # Test 4: Test your actual fetch functions with debug
        print("\n4. TESTING YOUR FETCH FUNCTIONS:")
        try:
            # Add debug prints to see what's happening
            print("Importing fetch functions...")
            from utils.db_func import fetch_market_data, fetch_vix_data
            
            print("Calling fetch_market_data(NIFTY50, 5m)...")
            result_5m = fetch_market_data(symbol="NIFTY50", interval="5m")
            print(f"Result type: {type(result_5m)}, Length: {len(result_5m)}, Empty: {result_5m.empty}")
            if not result_5m.empty:
                print("✅ 5m data fetch SUCCESS!")
                print(f"Columns: {list(result_5m.columns)}")
                print(f"Latest timestamp: {result_5m.index[-1]}")
            
            print("Calling fetch_market_data(NIFTY50, 15m)...")
            result_15m = fetch_market_data(symbol="NIFTY50", interval="15m")
            print(f"Result type: {type(result_15m)}, Length: {len(result_15m)}, Empty: {result_15m.empty}")
            if not result_15m.empty:
                print("✅ 15m data fetch SUCCESS!")
            
            print("Calling fetch_vix_data(VIX)...")
            result_vix = fetch_vix_data(symbol="VIX")
            print(f"Result type: {type(result_vix)}, Length: {len(result_vix)}, Empty: {result_vix.empty}")
            if not result_vix.empty:
                print("✅ VIX data fetch SUCCESS!")
            
        except Exception as e:
            print(f"FETCH FUNCTION ERROR: {e}")
            import traceback
            traceback.print_exc()
        
    except Exception as e:
        print(f"DATABASE CONNECTION ERROR: {e}")

def create_minimal_test_strategy():
    """Create a minimal version of your strategy for testing"""
    
    print("\n" + "="*50)
    print("MINIMAL STRATEGY TEST")
    print("="*50)
    
    try:
        # FIXED: Use correct database path
        conn = sqlite3.connect("db/trading_bot.db")  # This was "trading_bot.db" before
        
        # Get data directly
        df_5min = pd.read_sql_query(
            "SELECT * FROM market_data_5m WHERE symbol='NIFTY50' ORDER BY timestamp", 
            conn, 
            index_col='timestamp',
            parse_dates=['timestamp']
        )
        
        df_15min = pd.read_sql_query(
            "SELECT * FROM market_data_15m WHERE symbol='NIFTY50' ORDER BY timestamp", 
            conn, 
            index_col='timestamp',
            parse_dates=['timestamp']
        )
        
        vix_data = pd.read_sql_query(
            "SELECT * FROM vix_data WHERE symbol='NIFTY50' ORDER BY timestamp", 
            conn, 
            index_col='timestamp',
            parse_dates=['timestamp']
        )
        
        conn.close()
        
        print(f"Direct fetch results:")
        print(f"  5m data: {len(df_5min)} rows")
        print(f"  15m data: {len(df_15min)} rows") 
        print(f"  VIX data: {len(vix_data)} rows")
        
        if df_5min.empty or df_15min.empty or vix_data.empty:
            print("❌ DIRECT FETCH ALSO SHOWS EMPTY DATA!")
            missing = []
            if df_5min.empty: missing.append("5m")
            if df_15min.empty: missing.append("15m")
            if vix_data.empty: missing.append("VIX")
            print(f"Missing: {missing}")
        else:
            print("✅ DIRECT FETCH SUCCESSFUL!")
            
            # Test the actual strategy function
            print("\n5. TESTING ACTUAL STRATEGY:")
            try:
                from strategies.strategy import donchian_ao_strategy
                result = donchian_ao_strategy(symbol="NIFTY50")
                print(f"Strategy result: {result}")
                if result is None:
                    print("Strategy returned None - this might be expected based on conditions")
                else:
                    print("✅ Strategy executed successfully!")
            except Exception as e:
                print(f"Strategy error: {e}")
                import traceback
                traceback.print_exc()
            
    except Exception as e:
        print(f"DIRECT FETCH ERROR: {e}")
        import traceback
        traceback.print_exc()

def test_indicators():
    """Test if the indicator functions work with your data"""
    print("\n" + "="*50)
    print("INDICATOR FUNCTIONS TEST")
    print("="*50)
    
    try:
        from utils.db_func import fetch_market_data
        from strategies.indicators import add_donchian_channel, add_awesome_oscillator
        
        # Get some data
        df_5min = fetch_market_data(symbol="NIFTY50", interval="5m")
        
        if df_5min.empty:
            print("❌ No data to test indicators")
            return
        
        print(f"Original data shape: {df_5min.shape}")
        print(f"Original columns: {list(df_5min.columns)}")
        
        # Test Donchian Channel
        df_with_donchian = add_donchian_channel(df_5min.copy(), period=20, suffix="_5m")
        print(f"After Donchian: {df_with_donchian.shape}")
        print(f"New columns: {[col for col in df_with_donchian.columns if col not in df_5min.columns]}")
        
        # Test Awesome Oscillator
        df_with_ao = add_awesome_oscillator(df_5min.copy())
        print(f"After AO: {df_with_ao.shape}")
        print(f"AO columns: {[col for col in df_with_ao.columns if 'AO' in col.upper()]}")
        
        # Test both together
        df_complete = add_donchian_channel(df_5min.copy(), period=20, suffix="_5m")
        df_complete = add_awesome_oscillator(df_complete)
        
        print(f"Complete indicators: {df_complete.shape}")
        print("✅ Indicators working correctly!")
        
        # Show latest values
        latest = df_complete.iloc[-1]
        print(f"\nLatest values:")
        print(f"  Close: {latest.get('close', 'N/A')}")
        print(f"  Donchian Mid: {latest.get('Donchian_Mid_5m', 'N/A')}")
        print(f"  AO: {latest.get('AO', 'N/A')}")
        
    except Exception as e:
        print(f"Indicator test error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    simple_data_test()
    create_minimal_test_strategy()
    test_indicators()