"""
Quick verification script to confirm the 09:48:00 trade no longer triggers.

Run this after applying the lookback fix.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtesting.runner import BacktestRunner, BacktestConfig


def main():
    print("=" * 70)
    print("VERIFICATION: Running backtest for 2026-03-24")
    print("Expected: NO trade at 09:48:00 (SL filter should fail)")
    print("=" * 70)

    config = BacktestConfig(
        start_date=datetime(2026, 3, 24, 9, 15),
        end_date=datetime(2026, 3, 24, 15, 30),
        symbol="NIFTY50",
        db_path="db/trading_bot.db",
        verbose=False,
        debug_log_path="logs/verification_test.log",
    )

    runner = BacktestRunner(config)
    result = runner.run()

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Total trades: {result.total_trades}")

    if result.total_trades > 0:
        print("\nTrade times:")
        for i, trade in enumerate(runner.trade_log.completed_trades):
            entry_time = trade.get("entry_time", "unknown")
            section = trade.get("reason", {}).get("section", "unknown")
            print(f"  {i+1}. Entry at {entry_time} via {section}")

            # Check if 09:48 trade occurred
            if "09:48" in str(entry_time):
                print("\n  !!! BUG STILL EXISTS: Trade at 09:48 should NOT have triggered !!!")

        # Check for the correct 12:09 trade
        trade_times = [str(t.get("entry_time", "")) for t in runner.trade_log.completed_trades]
        if any("12:09" in t for t in trade_times):
            print("\n  ✓ Trade at 12:09 correctly triggered")
    else:
        print("\nNo trades - this may indicate other issues or no valid signals on this day.")

    print("\n" + "=" * 70)
    print("Check logs/verification_test.log for detailed gate check logs")
    print("=" * 70)


if __name__ == "__main__":
    main()
