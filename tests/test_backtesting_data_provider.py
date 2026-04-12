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

    def test_merge_with_sma_1h_uses_exact_timestamp_match_only(self):
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
                        ("2026-03-10 10:15:00", "NIFTY50", 111.0),
                        ("2026-03-10 10:20:00", "NIFTY50", 222.0),
                    ],
                )
                conn.commit()

                merged = provider._merge_with_sma_1h(df_1h, conn)
            finally:
                conn.close()

        self.assertEqual(float(merged.iloc[-1]["sma_20"]), 111.0)

    def test_load_all_data_prefers_stored_1h_and_merges_sma_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE market_data_1m (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE market_data_5m (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE market_data_15m (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE market_data_1h (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE market_sma_1h (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        sma_20 REAL
                    )
                    """
                )
                rows = []
                for idx, ts in enumerate(pd.date_range("2026-03-10 09:15:00", periods=8, freq="15min")):
                    rows.append((ts.strftime("%Y-%m-%d %H:%M:%S"), "NIFTY50", 100 + idx, 101 + idx, 99 + idx, 100.5 + idx))
                conn.executemany(
                    "INSERT INTO market_data_15m (timestamp, symbol, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
                conn.execute(
                    "INSERT INTO market_data_1m (timestamp, symbol, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-03-10 09:15:00", "NIFTY50", 1, 2, 0, 1.5),
                )
                conn.execute(
                    "INSERT INTO market_data_5m (timestamp, symbol, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-03-10 09:15:00", "NIFTY50", 10, 11, 9, 10.5),
                )
                conn.execute(
                    "INSERT INTO market_data_1h (timestamp, symbol, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-03-10 10:15:00", "NIFTY50", 500, 510, 490, 505),
                )
                conn.execute(
                    "INSERT INTO market_sma_1h (timestamp, symbol, sma_20) VALUES (?, ?, ?)",
                    ("2026-03-10 10:15:00", "NIFTY50", 999.0),
                )
                conn.commit()
            finally:
                conn.close()

            provider = HistoricalDataProvider(db_path=db_path)
            provider.load_all_data(datetime(2026, 3, 10, 9, 15), datetime(2026, 3, 10, 12, 0))

            hourly = provider.fetch_market_data(datetime(2026, 3, 10, 11, 15), "1h", limit=10)

        self.assertFalse(hourly.empty)
        self.assertEqual(float(hourly.iloc[-1]["close"]), 505.0)
        self.assertEqual(float(hourly.iloc[-1]["sma_20"]), 999.0)

    def test_load_all_data_uses_warmup_window_for_option_data_coverage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE market_data_1m (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE market_data_5m (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE market_data_15m (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE market_data_1h (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE option_data (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        strike_price INTEGER,
                        option_type TEXT,
                        ltp REAL,
                        expiry_date TEXT,
                        tradingsymbol TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE delta_cache (
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        strike_price INTEGER,
                        option_type TEXT,
                        ltp REAL,
                        expiry_date TEXT,
                        tradingsymbol TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE vix_data (
                        timestamp TEXT NOT NULL,
                        vix_value REAL
                    )
                    """
                )

                market_rows = [
                    ("2026-03-05 09:15:00", "NIFTY50", 1, 2, 0, 1.5),
                    ("2026-03-05 09:16:00", "NIFTY50", 2, 3, 1, 2.5),
                ]
                conn.executemany(
                    "INSERT INTO market_data_1m (timestamp, symbol, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
                    market_rows,
                )
                conn.execute(
                    "INSERT INTO market_data_5m (timestamp, symbol, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-03-05 09:15:00", "NIFTY50", 10, 11, 9, 10.5),
                )
                conn.execute(
                    "INSERT INTO market_data_15m (timestamp, symbol, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-03-05 09:15:00", "NIFTY50", 20, 21, 19, 20.5),
                )
                conn.execute(
                    "INSERT INTO market_data_1h (timestamp, symbol, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-03-05 09:15:00", "NIFTY50", 30, 31, 29, 30.5),
                )
                conn.execute(
                    "INSERT INTO vix_data (timestamp, vix_value) VALUES (?, ?)",
                    ("2026-03-05 09:26:45", 15.5),
                )
                conn.execute(
                    "INSERT INTO delta_cache (timestamp, symbol, strike_price, option_type, ltp, expiry_date, tradingsymbol) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("2026-03-05 08:44:07", "NIFTY50", 22400, "PE", 101.0, "2026-03-26", "OPTPE"),
                )
                conn.executemany(
                    "INSERT INTO option_data (timestamp, symbol, strike_price, option_type, ltp, expiry_date, tradingsymbol) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("2026-03-05 08:44:07", "NIFTY50", 22400, "PE", 101.0, "2026-03-26", "OPTPE"),
                        ("2026-03-05 09:18:14", "NIFTY50", 22400, "PE", 102.0, "2026-03-26", "OPTPE"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            provider = HistoricalDataProvider(db_path=db_path)
            provider.load_all_data(datetime(2026, 3, 5, 9, 15), datetime(2026, 3, 5, 15, 30))
            coverage = provider.get_backtest_data_coverage()

        self.assertEqual(coverage["option_data"]["start"], datetime(2026, 3, 5, 8, 44, 7))
        self.assertEqual(coverage["option_data"]["end"], datetime(2026, 3, 5, 9, 18, 14))

    def test_fetch_vix_data_is_snapshot_based(self):
        provider = HistoricalDataProvider(db_path=":memory:")
        provider._data_loaded = True
        provider._vix_data = pd.DataFrame(
            {"vix_value": [22.1, 22.4, 22.8]},
            index=pd.DatetimeIndex(
                [
                    pd.Timestamp("2026-03-27 15:20:51"),
                    pd.Timestamp("2026-03-27 15:21:12"),
                    pd.Timestamp("2026-03-27 15:26:48"),
                ]
            ),
        )

        filtered = provider.fetch_vix_data(datetime(2026, 3, 27, 15, 21, 30))

        self.assertEqual(list(filtered.index), [
            pd.Timestamp("2026-03-27 15:20:51"),
            pd.Timestamp("2026-03-27 15:21:12"),
        ])

    def test_fetch_next_delta_snapshot_returns_first_delta_cache_snapshot_after_signal(self):
        provider = HistoricalDataProvider(db_path=":memory:")
        provider._data_loaded = True
        provider._delta_cache = pd.DataFrame(
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
                    "timestamp": pd.Timestamp("2026-03-27 12:42:09"),
                    "tradingsymbol": "NIFTY26MAR22900PE",
                    "strike_price": 22900,
                    "option_type": "PE",
                    "expiry_date": "2026-03-30",
                    "ltp": 207.25,
                },
            ]
        )

        snapshot_time, options = provider.fetch_next_delta_snapshot(datetime(2026, 3, 27, 12, 42, 7))

        self.assertEqual(snapshot_time, datetime(2026, 3, 27, 12, 42, 9))
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["ltp"], 207.25)

        equal_time, equal_options = provider.fetch_next_delta_snapshot(datetime(2026, 3, 27, 12, 42, 9))
        self.assertEqual(equal_time, datetime(2026, 3, 27, 12, 42, 9))
        self.assertEqual(equal_options[0]["ltp"], 207.25)

    def test_fetch_next_delta_snapshot_falls_back_to_option_data_when_delta_cache_is_empty(self):
        provider = HistoricalDataProvider(db_path=":memory:")
        provider._data_loaded = True
        provider._option_data = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-03-27 12:42:09"),
                    "tradingsymbol": "NIFTY26MAR22900PE",
                    "strike_price": 22900,
                    "option_type": "PE",
                    "expiry_date": "2026-03-30",
                    "ltp": 207.25,
                },
            ]
        )

        snapshot_time, options = provider.fetch_next_delta_snapshot(datetime(2026, 3, 27, 12, 42, 7))

        self.assertEqual(snapshot_time, datetime(2026, 3, 27, 12, 42, 9))
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["ltp"], 207.25)

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

    def test_fetch_delta_data_returns_delta_cache_when_option_data_is_empty(self):
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
            ]
        )

        options = provider.fetch_delta_data(datetime(2026, 3, 27, 12, 42, 8))

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["tradingsymbol"], "NIFTY26MAR22950PE")
        self.assertEqual(options[0]["ltp"], 226.95)
        self.assertEqual(options[0]["delta"], -25.0)

    def test_fetch_delta_data_enriches_delta_cache_with_matching_option_snapshot(self):
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
                    "iv": 0.22,
                    "open_interest": 125000,
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

        self.assertEqual({row["strike_price"] for row in options}, {22950, 23000})
        delta_by_strike = {row["strike_price"]: row.get("delta") for row in options}
        self.assertEqual(delta_by_strike[22950], -25.0)
        self.assertEqual(delta_by_strike[23000], -27.5)
        enriched = {row["strike_price"]: row for row in options}
        self.assertEqual(enriched[22950]["iv"], 0.22)
        self.assertEqual(enriched[22950]["open_interest"], 125000)

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

    def test_get_backtest_data_coverage_reports_loaded_ranges_for_required_tables(self):
        provider = HistoricalDataProvider(db_path=":memory:")
        provider._data_loaded = True
        provider._market_data["1m"] = make_market_df("2026-04-07 09:15:00", periods=3, freq="1min")
        provider._vix_data = pd.DataFrame(
            {"vix_value": [13.1, 13.4]},
            index=pd.DatetimeIndex([
                pd.Timestamp("2026-04-07 09:20:00"),
                pd.Timestamp("2026-04-07 15:20:00"),
            ]),
        )
        provider._delta_cache = pd.DataFrame(
            [
                {"timestamp": pd.Timestamp("2026-04-07 09:16:00"), "ltp": 100.0},
                {"timestamp": pd.Timestamp("2026-04-07 15:25:00"), "ltp": 110.0},
            ]
        )
        provider._option_data = pd.DataFrame(
            [
                {"timestamp": pd.Timestamp("2026-04-07 09:17:00"), "ltp": 101.0},
                {"timestamp": pd.Timestamp("2026-04-07 15:26:00"), "ltp": 111.0},
            ]
        )

        coverage = provider.get_backtest_data_coverage()

        self.assertEqual(coverage["market_data_1m"]["start"], datetime(2026, 4, 7, 9, 15))
        self.assertEqual(coverage["market_data_1m"]["end"], datetime(2026, 4, 7, 9, 17))
        self.assertEqual(coverage["vix_data"]["start"], datetime(2026, 4, 7, 9, 20))
        self.assertEqual(coverage["vix_data"]["end"], datetime(2026, 4, 7, 15, 20))
        self.assertEqual(coverage["delta_cache"]["start"], datetime(2026, 4, 7, 9, 16))
        self.assertEqual(coverage["delta_cache"]["end"], datetime(2026, 4, 7, 15, 25))
        self.assertEqual(coverage["option_data"]["start"], datetime(2026, 4, 7, 9, 17))
        self.assertEqual(coverage["option_data"]["end"], datetime(2026, 4, 7, 15, 26))


if __name__ == "__main__":
    unittest.main()
