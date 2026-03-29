import math
import os
import sys
import unittest
from datetime import datetime

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.metrics import MetricsCalculator, Trade, TradeLog


class BacktestingMetricsTests(unittest.TestCase):
    def _record_closed_trade(
        self,
        trade_log: TradeLog,
        trade_id: str,
        entry_signal_time: datetime,
        entry_fill_time: datetime,
        exit_signal_time: datetime,
        exit_fill_time: datetime,
        pnl: float,
    ) -> None:
        trade_log.record_entry(
            Trade(
                trade_id=trade_id,
                entry_time=entry_fill_time,
                entry_signal_time=entry_signal_time,
                entry_fill_time=entry_fill_time,
                lot_id="lot1",
                position_type="A",
                entry_price_total=1000.0,
            )
        )
        trade_log.record_exit(
            trade_id=trade_id,
            exit_time=exit_fill_time,
            exit_price_total=1100.0,
            pnl=pnl,
            exit_reason="TIME_EXIT",
            exit_signal_time=exit_signal_time,
        )

    def test_daily_sharpe_uses_closed_trade_days_not_equity_curve_frequency(self):
        trade_log = TradeLog()
        trade_log.update_equity(datetime(2026, 3, 20, 9, 20), 0.0)
        trade_log.update_equity(datetime(2026, 3, 20, 9, 25), 0.0)
        trade_log.update_equity(datetime(2026, 3, 20, 9, 30), 0.0)

        self._record_closed_trade(
            trade_log,
            "t1",
            datetime(2026, 3, 20, 9, 20),
            datetime(2026, 3, 20, 9, 20, 10),
            datetime(2026, 3, 20, 9, 45),
            datetime(2026, 3, 20, 9, 45, 5),
            1000.0,
        )
        self._record_closed_trade(
            trade_log,
            "t2",
            datetime(2026, 3, 21, 10, 5),
            datetime(2026, 3, 21, 10, 5, 8),
            datetime(2026, 3, 21, 10, 40),
            datetime(2026, 3, 21, 10, 40, 7),
            -500.0,
        )
        self._record_closed_trade(
            trade_log,
            "t3",
            datetime(2026, 3, 22, 11, 0),
            datetime(2026, 3, 22, 11, 0, 12),
            datetime(2026, 3, 22, 11, 25),
            datetime(2026, 3, 22, 11, 25, 2),
            2000.0,
        )

        calculator = MetricsCalculator(trade_log)
        result = calculator.compute()

        returns = pd.Series([0.01, -0.005, 0.02], dtype=float)
        expected = ((returns.mean() - (0.065 / 252.0)) / returns.std()) * math.sqrt(252.0)

        self.assertAlmostEqual(result.daily_sharpe_ratio, expected, places=10)
        self.assertAlmostEqual(result.sharpe_ratio, expected, places=10)

    def test_daily_sharpe_returns_zero_when_fewer_than_two_return_days(self):
        trade_log = TradeLog()
        self._record_closed_trade(
            trade_log,
            "t1",
            datetime(2026, 3, 20, 9, 20),
            datetime(2026, 3, 20, 9, 20, 10),
            datetime(2026, 3, 20, 9, 45),
            datetime(2026, 3, 20, 9, 45, 5),
            1000.0,
        )

        result = MetricsCalculator(trade_log).compute()

        self.assertEqual(result.daily_sharpe_ratio, 0.0)
        self.assertEqual(result.sharpe_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()
