import os
import sqlite3
import sys
import tempfile
import unittest
import logging
from unittest.mock import patch

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.strat_nifty import NiftyOptionsStrategy
from utils.db_setup import create_tables
from utils.db_func import store_market_data
from utils.market_data_1h import (
    backfill_market_data_1h,
    build_1h_market_data_from_15m,
)


def make_15m_df(periods: int = 9) -> pd.DataFrame:
    index = pd.date_range("2026-03-10 09:15:00", periods=periods, freq="15min")
    base = [100.0 + i for i in range(periods)]
    return pd.DataFrame(
        {
            "open": base,
            "high": [value + 2.0 for value in base],
            "low": [value - 1.0 for value in base],
            "close": [value + 0.5 for value in base],
        },
        index=index,
    )


class MarketData1hTests(unittest.TestCase):
    @staticmethod
    def _logger_tuple():
        logger = logging.getLogger("test.market_data_1h")
        return (logger, logger, logger, logger, logger, logger)

    def test_create_tables_adds_market_data_1h(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            create_tables(db_path)

            conn = sqlite3.connect(db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='market_data_1h'")
                self.assertIsNotNone(cursor.fetchone())
                cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ux_market_data_1h_symbol_timestamp'")
                self.assertIsNotNone(cursor.fetchone())
            finally:
                conn.close()

    def test_build_1h_market_data_from_15m_keeps_only_complete_buckets(self):
        df_15m = make_15m_df(periods=9)

        hourly = build_1h_market_data_from_15m(df_15m)

        self.assertEqual(list(hourly.index), [
            pd.Timestamp("2026-03-10 09:15:00"),
            pd.Timestamp("2026-03-10 10:15:00"),
        ])
        self.assertEqual(float(hourly.iloc[0]["open"]), 100.0)
        self.assertEqual(float(hourly.iloc[0]["high"]), 105.0)
        self.assertEqual(float(hourly.iloc[0]["low"]), 99.0)
        self.assertEqual(float(hourly.iloc[0]["close"]), 103.5)

    def test_backfill_market_data_1h_populates_hourly_and_sma_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            create_tables(db_path)
            store_market_data(make_15m_df(periods=9), symbol="NIFTY50", interval="15m", db_path=db_path)

            conn = sqlite3.connect(db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS market_sma_1h (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        sma_5 REAL,
                        sma_5_high REAL,
                        sma_5_low REAL,
                        sma_20 REAL,
                        sma_50 REAL,
                        sma_200 REAL,
                        PRIMARY KEY (timestamp, symbol)
                    )
                """)
                cursor.execute("""
                    INSERT OR REPLACE INTO market_data_1h (timestamp, symbol, open, high, low, close)
                    VALUES ('2026-03-10 15:15:00', 'NIFTY50', 1, 1, 1, 1)
                """)
                cursor.execute("""
                    INSERT OR REPLACE INTO market_sma_1h (timestamp, symbol, sma_20)
                    VALUES ('2026-03-10 15:15:00', 'NIFTY50', 999)
                """)
                conn.commit()
            finally:
                conn.close()

            summary = backfill_market_data_1h(db_path=db_path)
            summary_again = backfill_market_data_1h(db_path=db_path)

            self.assertEqual(summary["NIFTY50"], 2)
            self.assertEqual(summary_again["NIFTY50"], 2)

            conn = sqlite3.connect(db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM market_data_1h WHERE symbol = 'NIFTY50'")
                self.assertEqual(cursor.fetchone()[0], 2)
                cursor.execute("SELECT timestamp FROM market_data_1h WHERE symbol = 'NIFTY50' ORDER BY timestamp")
                self.assertEqual(
                    [row[0] for row in cursor.fetchall()],
                    ["2026-03-10 09:15:00", "2026-03-10 10:15:00"],
                )
                cursor.execute("SELECT COUNT(*) FROM market_sma_1h WHERE symbol = 'NIFTY50'")
                self.assertEqual(cursor.fetchone()[0], 2)
            finally:
                conn.close()

    def test_nifty_strategy_falls_back_to_resampled_15m_when_1h_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = os.path.join(temp_dir, "nifty_strategy_state.json")
            with patch("core.strat_nifty.setup_paper_trading_logger", return_value=self._logger_tuple()):
                strategy = NiftyOptionsStrategy(symbol="NIFTY50", state_file=state_file)

            df_15m = make_15m_df(periods=9)

            def market_data_side_effect(symbol: str, interval: str, limit=None):
                if interval == "1h":
                    raise Exception("no such table: market_data_1h")
                if interval == "15m":
                    return df_15m.copy()
                raise AssertionError(f"Unexpected interval requested: {interval}")

            with patch("core.strat_nifty.fetch_market_data", side_effect=market_data_side_effect):
                df_1h = strategy._get_df("1h", limit=2)

            self.assertEqual(list(df_1h.index), [
                pd.Timestamp("2026-03-10 09:15:00"),
                pd.Timestamp("2026-03-10 10:15:00"),
            ])
            self.assertIn("ao_value", df_1h.columns)
            self.assertIn("donchian_mid", df_1h.columns)


if __name__ == "__main__":
    unittest.main()
