#!/usr/bin/env python3
"""
Test script for signal generation and storage
Verifies that signals are being generated and stored in the database
"""

import sys
import os
# Add the parent directory to the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_setup import create_tables
from utils.db_func import store_signal, fetch_market_data
from strategies.indicators import compute_indicators, generate_signals
import pandas as pd
from datetime import datetime, timedelta

def test_signal_generation():
    """Test signal generation and storage"""
    print("=" * 60)
    print("Testing Signal Generation and Storage")
    print("=" * 60)
    
    # 1. Create database tables
    print("\n1. Creating database tables...")
    create_tables()
    print("✅ Database tables created")
    
    # 2. Create sample market data for testing (since we might not have real data)
    print("\n2. Creating sample market data for testing...")
    
    # Create sample data with datetime index
    dates = pd.date_range(start='2025-01-01 09:15:00', periods=100, freq='5min')
    df = pd.DataFrame({
        'open': [100 + i * 0.1 for i in range(100)],
        'high': [105 + i * 0.1 for i in range(100)],
        'low': [95 + i * 0.1 for i in range(100)],
        'close': [102 + i * 0.1 for i in range(100)],
    }, index=dates)
    
    print(f"✅ Created sample data with {len(df)} rows")
    print(f"Date range: {df.index[0]} to {df.index[-1]}")
    
    # 3. Compute indicators
    print("\n3. Computing indicators...")
    df = compute_indicators(df)
    print("✅ Indicators computed")
    print(f"Available columns: {df.columns.tolist()}")
    
    # 4. Generate signals
    print("\n4. Generating signals...")
    signals = generate_signals(df, "NIFTY50")
    
    if signals:
        print(f"✅ Generated {len(signals)} signals:")
        for i, signal in enumerate(signals, 1):
            print(f"  Signal {i}: {signal['signal']} - {signal['reason']} (confidence: {signal['confidence_score']:.2f})")
    else:
        print("ℹ️  No signals generated (this is normal if no signal conditions are met)")
    
    # 5. Test signal storage
    print("\n5. Testing signal storage...")
    
    # Create a test signal
    test_signal = {
        'timestamp': datetime.now(),
        'symbol': 'NIFTY50',
        'signal': 'BUY',
        'reason': 'Test signal generation',
        'confidence_score': 0.85
    }
    
    try:
        store_signal(**test_signal)
        print("✅ Test signal stored successfully")
    except Exception as e:
        print(f"❌ Failed to store test signal: {e}")
        return
    
    # 6. Verify signal storage
    print("\n6. Verifying signal storage...")
    import sqlite3
    conn = sqlite3.connect("db/trading_bot.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM signals")
    total_signals = cursor.fetchone()[0]
    print(f"✅ Total signals in database: {total_signals}")
    
    cursor.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 3")
    recent_signals = cursor.fetchall()
    
    if recent_signals:
        print("Recent signals:")
        for signal in recent_signals:
            print(f"  ID: {signal[0]}, Time: {signal[1]}, Symbol: {signal[2]}, Signal: {signal[3]}, Reason: {signal[4]}, Confidence: {signal[5]}")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("Signal Generation Test Complete!")
    print("=" * 60)

def test_signal_conditions():
    """Test different signal conditions"""
    print("\n" + "=" * 60)
    print("Testing Signal Conditions")
    print("=" * 60)
    
    # Create sample data with different conditions
    dates = pd.date_range(start='2025-01-01', periods=50, freq='5min')
    
    # Test case 1: AO crossover above 0
    print("\n1. Testing AO crossover above 0...")
    df1 = pd.DataFrame({
        'open': [100] * 50,
        'high': [105] * 50,
        'low': [95] * 50,
        'close': [102] * 50,
        'ao_value': [-0.5] * 25 + [0.5] * 25  # Crossover at index 25
    }, index=dates)
    
    df1 = compute_indicators(df1)
    signals1 = generate_signals(df1, "NIFTY50")
    print(f"Signals generated: {len(signals1)}")
    
    # Test case 2: Donchian breakout
    print("\n2. Testing Donchian breakout...")
    df2 = pd.DataFrame({
        'open': [100] * 50,
        'high': [105] * 50,
        'low': [95] * 50,
        'close': [110] * 50,  # Above upper Donchian
        'ao_value': [0.1] * 50
    }, index=dates)
    
    df2 = compute_indicators(df2)
    signals2 = generate_signals(df2, "NIFTY50")
    print(f"Signals generated: {len(signals2)}")
    
    # Test case 3: Combined signals
    print("\n3. Testing combined signals...")
    df3 = pd.DataFrame({
        'open': [100] * 50,
        'high': [105] * 50,
        'low': [95] * 50,
        'close': [110] * 50,  # Above upper Donchian
        'ao_value': [-0.5] * 25 + [0.5] * 25  # AO crossover + Donchian breakout
    }, index=dates)
    
    df3 = compute_indicators(df3)
    signals3 = generate_signals(df3, "NIFTY50")
    print(f"Signals generated: {len(signals3)}")
    
    print("\n✅ Signal condition tests completed")

if __name__ == "__main__":
    test_signal_generation()
    test_signal_conditions() 