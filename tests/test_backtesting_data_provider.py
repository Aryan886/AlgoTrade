import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.data_provider import HistoricalDataProvider


def make_market_df(start: str, periods: int, freq: str) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq=freq)
    return pd.DataFrame(
        {
            "open": range(periods),
            "high": range(periods),
            "low": range(periods),
            "close": range(periods),
        },
        index=index,
    )


class HistoricalDataProviderTests(unittest.TestCase):
    def test_fetch_market_data_only_returns_fully_closed_candles(self):
        provider = HistoricalDataProvider(db_path=":memory:")
        provider._data_loaded = True
        provider._market_data["1m"] = make_market_df("2026-03-10 09:15:00", periods=3, freq="1min")
        provider._market_data["5m"] = make_market_df("2026-03-10 09:15:00", periods=3, freq="5min")

        df_1m_at_open = provider.fetch_market_data(datetime(2026, 3, 10, 9, 15), "1m")
        df_1m_after_close = provider.fetch_market_data(datetime(2026, 3, 10, 9, 16), "1m")
        df_5m_before_close = provider.fetch_market_data(datetime(2026, 3, 10, 9, 19), "5m")
        df_5m_after_close = provider.fetch_market_data(datetime(2026, 3, 10, 9, 20), "5m")

        self.assertTrue(df_1m_at_open.empty)
        self.assertEqual(list(df_1m_after_close.index), [pd.Timestamp("2026-03-10 09:15:00")])
        self.assertTrue(df_5m_before_close.empty)
        self.assertEqual(list(df_5m_after_close.index), [pd.Timestamp("2026-03-10 09:15:00")])

    def test_merge_with_sma_1h_never_uses_future_row(self):
        provider = HistoricalDataProvider(db_path=":memory:")
        df_1h = pd.DataFrame(
            {"close": [100.0]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-03-10 10:15:00")]),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE market_sma_1h (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        sma_20 REAL
                    )
                    """
                )
                conn.executemany(
                    "INSERT INTO market_sma_1h (timestamp, symbol, sma_20) VALUES (?, ?, ?)",
                    [
                        ("2026-03-10 09:55:00", "NIFTY50", 111.0),
                        ("2026-03-10 10:20:00", "NIFTY50", 222.0),
                    ],
                )
                conn.commit()

                merged = provider._merge_with_sma_1h(df_1h, conn)
            finally:
                conn.close()

        self.assertEqual(float(merged.iloc[-1]["sma_20"]), 111.0)

    def test_fetch_option_price_uses_last_known_contract_quote(self):
        provider = HistoricalDataProvider(db_path=":memory:")
        provider._data_loaded = True
        provider._delta_cache = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-03-12 13:47:44"),
                    "tradingsymbol": "NIFTY2631723800PE",
                    "strike_price": 23800,
                    "option_type": "PE",
                    "expiry_date": "2026-03-17",
                    "ltp": 235.65,
                },
                {
                    "timestamp": pd.Timestamp("2026-03-13 09:13:38"),
                    "tradingsymbol": "NIFTY2631723300PE",
                    "strike_price": 23300,
                    "option_type": "PE",
                    "expiry_date": "2026-03-17",
                    "ltp": 124.80,
                },
            ]
        )

        price = provider.fetch_option_price(
            current_time=datetime(2026, 3, 13, 9, 15),
            tradingsymbol="NIFTY2631723800PE",
            strike_price=23800,
            option_type="PE",
            expiry="2026-03-17",
        )

        self.assertEqual(price, 235.65)

    def test_fetch_delta_data_merges_full_option_snapshot_with_delta_cache(self):
        provider = HistoricalDataProvider(db_path=":memory:")
        provider._data_loaded = True
        provider._delta_cache = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-03-27 12:42:07"),
                    "tradingsymbol": "NIFTY26MAR22950PE",
                    "strike_price": 22950,
                    "option_type": "PE",
                    "expiry_date": "2026-03-30",
                    "ltp": 226.95,
                    "delta": -25.0,
                },
                {
                    "timestamp": pd.Timestamp("2026-03-27 12:42:07"),
                    "tradingsymbol": "NIFTY26MAR23000PE",
                    "strike_price": 23000,
                    "option_type": "PE",
                    "expiry_date": "2026-03-30",
                    "ltp": 251.18,
                    "delta": -27.5,
                },
            ]
        )
        provider._option_data = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-03-27 12:42:06"),
                    "tradingsymbol": "NIFTY26MAR22900PE",
                    "strike_price": 22900,
                    "option_type": "PE",
                    "expiry_date": "2026-03-30",
                    "ltp": 205.50,
                },
                {
                    "timestamp": pd.Timestamp("2026-03-27 12:42:06"),
                    "tradingsymbol": "NIFTY26MAR22950PE",
                    "strike_price": 22950,
                    "option_type": "PE",
                    "expiry_date": "2026-03-30",
                    "ltp": 226.95,
                },
                {
                    "timestamp": pd.Timestamp("2026-03-27 12:42:06"),
                    "tradingsymbol": "NIFTY26MAR23000PE",
                    "strike_price": 23000,
                    "option_type": "PE",
                    "expiry_date": "2026-03-30",
                    "ltp": 251.18,
                },
            ]
        )

        options = provider.fetch_delta_data(datetime(2026, 3, 27, 12, 42, 8))

        self.assertEqual({row["strike_price"] for row in options}, {22900, 22950, 23000})
        delta_by_strike = {row["strike_price"]: row.get("delta") for row in options}
        self.assertIsNone(delta_by_strike[22900])
        self.assertEqual(delta_by_strike[22950], -25.0)
        self.assertEqual(delta_by_strike[23000], -27.5)

    def test_fetch_option_price_falls_back_to_option_data_when_delta_cache_misses_contract(self):
        provider = HistoricalDataProvider(db_path=":memory:")
        provider._data_loaded = True
        provider._delta_cache = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-03-27 12:42:07"),
                    "tradingsymbol": "NIFTY26MAR22950PE",
                    "strike_price": 22950,
                    "option_type": "PE",
                    "expiry_date": "2026-03-30",
                    "ltp": 226.95,
                },
            ]
        )
        provider._option_data = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-03-27 12:42:06"),
                    "tradingsymbol": "NIFTY26MAR22900PE",
                    "strike_price": 22900,
                    "option_type": "PE",
                    "expiry_date": "2026-03-30",
                    "ltp": 205.50,
                },
            ]
        )

        price = provider.fetch_option_price(
            current_time=datetime(2026, 3, 27, 12, 42, 8),
            tradingsymbol="NIFTY26MAR22900PE",
            strike_price=22900,
            option_type="PE",
            expiry="2026-03-30",
        )

        self.assertEqual(price, 205.50)


if __name__ == "__main__":
    unittest.main()
