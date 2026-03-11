import os
import sqlite3
import sys
import tempfile
import unittest

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.indicators import nifty_skipping_high, nifty_skipping_low
from utils.db_func import store_market_data
from utils.db_setup import create_tables


class SkippingIndicatorTests(unittest.TestCase):
    def test_skipping_levels_hold_seed_without_new_trigger(self):
        index = pd.date_range("2026-03-10 09:15:00", periods=4, freq="1min")
        df = pd.DataFrame(
            {
                "high": [101.0, 101.0, 101.0, 101.0],
                "low": [99.0, 99.0, 99.0, 99.0],
                "close": [100.0, 100.0, 100.0, 100.0],
            },
            index=index,
        )

        df = nifty_skipping_low(df, filter_pct=0.0, initial_sl=98.5)
        df = nifty_skipping_high(df, filter_pct=0.0, initial_sh=101.5)

        self.assertEqual(df["SL"].tolist(), [98.5, 98.5, 98.5, 98.5])
        self.assertEqual(df["SH"].tolist(), [101.5, 101.5, 101.5, 101.5])

    def test_store_market_data_persists_seeded_sl_sh_for_incremental_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "trading_bot.db")
            create_tables(db_path)

            historical_rows = [
                ("2026-03-10 09:15:00", "NIFTY50", 100.0, 101.0, 99.0, 100.0, 98.5, 101.5),
                ("2026-03-10 09:16:00", "NIFTY50", 100.0, 101.0, 99.0, 100.0, None, None),
                ("2026-03-10 09:17:00", "NIFTY50", 100.0, 101.0, 99.0, 100.0, None, None),
                ("2026-03-10 09:18:00", "NIFTY50", 100.0, 101.0, 99.0, 100.0, None, None),
                ("2026-03-10 09:19:00", "NIFTY50", 100.0, 101.0, 99.0, 100.0, None, None),
                ("2026-03-10 09:20:00", "NIFTY50", 100.0, 101.0, 99.0, 100.0, None, None),
                ("2026-03-10 09:21:00", "NIFTY50", 100.0, 101.0, 99.0, 100.0, None, None),
            ]

            conn = sqlite3.connect(db_path)
            conn.executemany(
                """
                INSERT INTO market_data_1m (
                    timestamp, symbol, open, high, low, close, SL, SH
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                historical_rows,
            )
            conn.commit()
            conn.close()

            incoming_index = pd.DatetimeIndex([pd.Timestamp("2026-03-10 09:22:00")], name="timestamp")
            incoming_df = pd.DataFrame(
                {
                    "open": [100.0],
                    "high": [101.0],
                    "low": [99.0],
                    "close": [100.0],
                },
                index=incoming_index,
            )

            stored_rows = store_market_data(
                incoming_df,
                symbol="NIFTY50",
                interval="1m",
                db_path=db_path,
            )

            self.assertEqual(stored_rows, 1)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                """
                SELECT SL, SH
                FROM market_data_1m
                WHERE symbol = 'NIFTY50' AND timestamp = '2026-03-10 09:22:00'
                """
            ).fetchone()
            conn.close()

            self.assertEqual(row, (98.5, 101.5))


if __name__ == "__main__":
    unittest.main()
