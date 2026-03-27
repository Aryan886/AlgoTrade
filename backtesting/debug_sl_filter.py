"""
Debug script to verify gate check calculations for the 09:48:00 trade entry.

This script loads historical data and validates all gate conditions to confirm
which gates passed/failed and identify any bugs.

Usage:
    python backtesting/debug_sl_filter.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtesting.data_provider import HistoricalDataProvider


def _rolling_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add required indicators to dataframe."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if "sma_20" not in df.columns:
        df["sma_20"] = _rolling_sma(df["close"], 20)
    if "sma_50" not in df.columns:
        df["sma_50"] = _rolling_sma(df["close"], 50)
    if "sma_200" not in df.columns:
        df["sma_200"] = _rolling_sma(df["close"], 200)
    if "sma_5_low" not in df.columns:
        df["sma_5_low"] = df["low"].rolling(window=5, min_periods=5).mean()

    return df


def calculate_sl_filter(df_1m: pd.DataFrame, lookback: int, threshold: float) -> dict:
    """Calculate SL filter values for a given lookback window."""
    if df_1m is None or df_1m.empty or len(df_1m) < lookback:
        return {"error": "Insufficient data", "passed": False}

    window = df_1m.iloc[-lookback:]
    highest_high = float(window["high"].max())
    close = float(df_1m.iloc[-1]["close"])
    sl_pts = highest_high - close
    passed = sl_pts <= threshold

    return {
        "lookback": lookback,
        "highest_high": round(highest_high, 2),
        "close": round(close, 2),
        "sl_pts": round(sl_pts, 2),
        "threshold": threshold,
        "passed": passed,
    }


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> float | None:
    """Calculate simple RSI."""
    if df is None or df.empty or len(df) < period + 1:
        return None

    prices = df["close"].iloc[-(period + 1):]
    delta = prices.diff().iloc[1:]
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)

    avg_gain = float(gains.sum()) / float(period)
    avg_loss = float(losses.sum()) / float(period)

    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    if avg_gain == 0.0:
        return 0.0

    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def check_main_category_a(df_1h: pd.DataFrame) -> dict:
    """Check Main Category A gate."""
    df_1h = compute_indicators(df_1h)
    if df_1h.empty:
        return {"passed": False, "error": "No 1h data"}

    last = df_1h.iloc[-1]
    close = float(last["close"])
    sma_20 = float(last["sma_20"]) if pd.notna(last["sma_20"]) else None
    sma_50 = float(last["sma_50"]) if pd.notna(last["sma_50"]) else None
    sma_200 = float(last["sma_200"]) if pd.notna(last["sma_200"]) else None

    if sma_20 is None or sma_50 is None or sma_200 is None:
        return {"passed": False, "error": "SMAs not ready"}

    passed = (close < sma_20) and (close < sma_50) and (close < sma_200)
    return {
        "passed": passed,
        "close": round(close, 2),
        "sma_20": round(sma_20, 2),
        "sma_50": round(sma_50, 2),
        "sma_200": round(sma_200, 2),
    }


def check_second_flag(df_1m: pd.DataFrame) -> dict:
    """Check 2nd flag: 1m close < SMA20 AND < SMA5_Low."""
    df_1m = compute_indicators(df_1m)
    if df_1m.empty:
        return {"passed": False, "error": "No 1m data"}

    last = df_1m.iloc[-1]
    close = float(last["close"])
    sma_20 = float(last["sma_20"]) if pd.notna(last["sma_20"]) else None
    sma_5_low = float(last["sma_5_low"]) if pd.notna(last["sma_5_low"]) else None

    if sma_20 is None or sma_5_low is None:
        return {"passed": False, "error": "Indicators not ready"}

    below_sma20 = close < sma_20
    below_sma5_low = close < sma_5_low
    passed = below_sma20 and below_sma5_low

    return {
        "passed": passed,
        "close": round(close, 2),
        "sma_20": round(sma_20, 2),
        "sma_5_low": round(sma_5_low, 2),
        "below_sma20": below_sma20,
        "below_sma5_low": below_sma5_low,
    }


def check_vix_regime(df_vix: pd.DataFrame) -> dict:
    """Check VIX regime to determine position type."""
    if df_vix is None or df_vix.empty:
        return {"passed": False, "error": "No VIX data"}

    if "vix_value" not in df_vix.columns:
        return {"passed": False, "error": "vix_value column missing"}

    df_vix = df_vix.copy()
    df_vix["vix_sma20"] = df_vix["vix_value"].rolling(window=20, min_periods=20).mean()

    last = df_vix.iloc[-1]
    vix_value = float(last["vix_value"])
    vix_sma20 = float(last["vix_sma20"]) if pd.notna(last["vix_sma20"]) else None

    if vix_sma20 is None:
        return {"passed": False, "error": "VIX SMA20 not ready"}

    # Type A if VIX > VIX_SMA20, else Type B
    regime = "A" if vix_value > vix_sma20 else "B"
    threshold = 25.0 if regime == "A" else 35.0

    return {
        "passed": True,
        "vix_value": round(vix_value, 2),
        "vix_sma20": round(vix_sma20, 2),
        "regime": regime,
        "sl_threshold": threshold,
    }


def debug_all_gates(
    data_provider: HistoricalDataProvider,
    target_time: datetime,
) -> None:
    """Debug all gate conditions at a specific time."""
    print(f"\n{'='*70}")
    print(f"GATE CHECK DEBUG at {target_time}")
    print(f"{'='*70}")

    # Fetch data
    df_1m = data_provider.fetch_market_data(target_time, "1m", limit=300)
    df_5m = data_provider.fetch_market_data(target_time, "5m", limit=300)
    df_1h = data_provider.fetch_market_data(target_time, "1h", limit=300)
    df_vix = data_provider.fetch_vix_data(target_time)

    if df_1m.empty:
        print("ERROR: No 1m data available")
        return

    df_1m = compute_indicators(df_1m)
    df_5m = compute_indicators(df_5m) if not df_5m.empty else df_5m
    df_1h = compute_indicators(df_1h) if not df_1h.empty else df_1h

    print(f"\nLatest 1m candle: {df_1m.index[-1]}")

    # Gate 1: Main Category A
    print(f"\n{'='*50}")
    print("GATE 1: Main Category A (1h close < all SMAs)")
    print("=" * 50)
    cat_a = check_main_category_a(df_1h)
    status = "PASS" if cat_a["passed"] else "FAIL"
    print(f"  Status: [{status}]")
    for k, v in cat_a.items():
        if k != "passed":
            print(f"  {k}: {v}")

    # Gate 2: VIX Regime
    print(f"\n{'='*50}")
    print("GATE 2: VIX Regime")
    print("=" * 50)
    vix = check_vix_regime(df_vix)
    status = "PASS" if vix["passed"] else "FAIL"
    print(f"  Status: [{status}]")
    for k, v in vix.items():
        if k != "passed":
            print(f"  {k}: {v}")

    sl_threshold = vix.get("sl_threshold", 35.0)

    # Gate 3: 2nd Flag
    print(f"\n{'='*50}")
    print("GATE 3: 2nd Flag (1m close < SMA20 AND < SMA5_Low)")
    print("=" * 50)
    second = check_second_flag(df_1m)
    status = "PASS" if second["passed"] else "FAIL"
    print(f"  Status: [{status}]")
    for k, v in second.items():
        if k != "passed":
            print(f"  {k}: {v}")

    # Gate 4: RSI Filter (Section 2)
    print(f"\n{'='*50}")
    print("GATE 4: RSI Filter (5m RSI > 40)")
    print("=" * 50)
    rsi = calculate_rsi(df_5m, period=14)
    rsi_passed = rsi is not None and rsi > 40.0
    status = "PASS" if rsi_passed else "FAIL"
    print(f"  Status: [{status}]")
    print(f"  5m RSI(14): {rsi}")
    print(f"  Threshold: 40.0")

    # Gate 5: SL Filter with different lookbacks
    print(f"\n{'='*50}")
    print(f"GATE 5: SL Filter (threshold={sl_threshold})")
    print("=" * 50)

    print("\n  Last 10 candles (highs):")
    for i, (ts, row) in enumerate(df_1m.tail(10).iterrows()):
        marker = " <-- current" if i == 9 else ""
        print(f"    {ts}: High={row['high']:.2f}{marker}")

    for lookback in [5, 7]:
        sl = calculate_sl_filter(df_1m, lookback, sl_threshold)
        status = "PASS" if sl["passed"] else "FAIL"
        print(f"\n  {lookback}-candle lookback: [{status}]")
        print(f"    HH={sl.get('highest_high')}, Close={sl.get('close')}, SL_pts={sl.get('sl_pts')}")

    # Summary
    print(f"\n{'='*70}")
    print("SECTION 2 ENTRY VERDICT")
    print("=" * 70)

    sl_7 = calculate_sl_filter(df_1m, 7, sl_threshold)

    all_gates_pass = (
        cat_a["passed"] and
        vix["passed"] and
        second["passed"] and
        rsi_passed and
        sl_7["passed"]
    )

    print(f"  Main Category A: {'PASS' if cat_a['passed'] else 'FAIL'}")
    print(f"  VIX Regime:      {'PASS' if vix['passed'] else 'FAIL'} (Type {vix.get('regime', '?')})")
    print(f"  2nd Flag:        {'PASS' if second['passed'] else 'FAIL'}")
    print(f"  RSI Filter:      {'PASS' if rsi_passed else 'FAIL'}")
    print(f"  SL Filter (7):   {'PASS' if sl_7['passed'] else 'FAIL'}")
    print(f"\n  --> Section 2 should {'TRIGGER' if all_gates_pass else 'NOT trigger'}")


def main():
    # Initialize data provider for the test date
    test_date = datetime(2026, 3, 24)

    print(f"Loading historical data for {test_date.date()}...")
    data_provider = HistoricalDataProvider(
        db_path="db/trading_bot.db",
        symbol="NIFTY50",
    )

    # Load data for the test date (with warmup)
    data_provider.load_all_data(
        start_date=test_date,
        end_date=test_date + timedelta(days=1)
    )

    # Test times around the problematic trade
    test_times = [
        datetime(2026, 3, 24, 9, 47, 0),   # 1 min before
        datetime(2026, 3, 24, 9, 48, 0),   # Trade triggered at this time
    ]

    for target_time in test_times:
        debug_all_gates(data_provider, target_time)

    print("\n" + "=" * 70)
    print("DEBUG COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
