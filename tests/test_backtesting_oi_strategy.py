import os
import sys
import unittest
from datetime import datetime
from unittest.mock import Mock

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.backtest_oi_strategy import BacktestableOIStrategy


class BacktestableOIStrategyTests(unittest.TestCase):
    def test_strategy_uses_historical_provider_for_all_data_sources(self):
        provider = Mock()
        current_time = datetime(2026, 4, 28, 9, 30, 0)
        market_df = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex([pd.Timestamp("2026-04-28 09:29:00")]))
        provider.fetch_market_data.return_value = market_df
        provider.fetch_option_snapshot.return_value = [{"tradingsymbol": "OPT1", "ltp": 10.0}]
        provider.fetch_open_interest_snapshot.return_value = [{"tradingsymbol": "OI1", "open_interest": 1000}]
        provider.fetch_open_interest_5m_snapshot.return_value = [{"tradingsymbol": "OI5", "vwap": 15.0}]

        strategy = BacktestableOIStrategy(
            data_provider=provider,
            current_time_fn=lambda: current_time,
            symbol="NIFTY50",
        )

        self.assertEqual(strategy._now(), current_time)
        self.assertEqual(strategy._get_df("1m", limit=10).iloc[-1]["close"], 1.0)
        self.assertEqual(strategy._fetch_option_snapshot()[0]["tradingsymbol"], "OPT1")
        self.assertEqual(strategy._fetch_open_interest_snapshot()[0]["tradingsymbol"], "OI1")
        self.assertEqual(strategy._fetch_open_interest_5m_snapshot()[0]["tradingsymbol"], "OI5")

        provider.fetch_market_data.assert_called_with(
            current_time=current_time,
            interval="1m",
            limit=10,
        )
        provider.fetch_option_snapshot.assert_called_with(current_time)
        provider.fetch_open_interest_snapshot.assert_called_with(current_time)
        provider.fetch_open_interest_5m_snapshot.assert_called_with(current_time)


if __name__ == "__main__":
    unittest.main()
