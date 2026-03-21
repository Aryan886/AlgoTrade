import unittest

import pandas as pd

from core.strat_nifty import _simple_rsi


class TestSimpleRsi(unittest.TestCase):
    def test_simple_rsi_returns_none_when_not_enough_candles(self):
        df = pd.DataFrame({"close": [100, 101, 102]})

        self.assertIsNone(_simple_rsi(df, period=14))

    def test_simple_rsi_returns_100_for_all_gains_window(self):
        closes = list(range(100, 116))
        df = pd.DataFrame({"close": closes})

        self.assertEqual(_simple_rsi(df, period=14), 100.0)

    def test_simple_rsi_matches_simple_average_formula(self):
        closes = [
            44.0,
            44.15,
            43.9,
            44.35,
            44.55,
            44.2,
            44.4,
            44.75,
            44.5,
            44.9,
            45.05,
            44.85,
            45.25,
            45.1,
            45.45,
        ]
        df = pd.DataFrame({"close": closes})

        # Last 14 deltas:
        # gains = 0.15 + 0.45 + 0.20 + 0.20 + 0.35 + 0.40 + 0.15 + 0.40 + 0.35 = 2.65
        # losses = 0.25 + 0.35 + 0.25 + 0.20 + 0.15 = 1.20
        avg_gain = 2.65 / 14.0
        avg_loss = 1.20 / 14.0
        expected = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

        self.assertAlmostEqual(_simple_rsi(df, period=14), expected, places=12)


if __name__ == "__main__":
    unittest.main()
