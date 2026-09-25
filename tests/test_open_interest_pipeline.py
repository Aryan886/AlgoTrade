import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.data_provider import HistoricalDataProvider
from utils.db_func import (
    fetch_latest_open_interest_snapshot,
    fetch_latest_open_interest_5m_snapshot,
    fetch_open_interest_data,
    fetch_open_interest_5m_data,
    rebuild_open_interest_5m_for_day,
    store_open_interest_snapshot,
)
from utils.db_setup import create_open_interest_5m_table, create_open_interest_table
from utils.open_interest_fetcher import (
    NiftyOpenInterestFetcher,
    build_oi_strike_window,
    select_nearest_expiry_instruments,
)


def _instrument(expiry, strike, option_type, token):
    return {
        "name": "NIFTY",
        "instrument_type": option_type,
        "expiry": expiry,
        "strike": strike,
        "tradingsymbol": f"NIFTY{expiry.strftime('%d%b').upper()}{strike}{option_type}",
        "instrument_token": token,
    }


class FakeKite:
    def __init__(self, spot_price=22580.0, missing_key=None):
        self.spot_price = spot_price
        self.missing_key = missing_key
        self.quote_requests = []

    def instruments(self, exchange):
        self.requested_exchange = exchange
        instruments = []
        token = 1000
        for expiry in [date(2026, 4, 10), date(2026, 4, 16), date(2026, 4, 23)]:
            for strike in range(22200, 23101, 50):
                for option_type in ["CE", "PE"]:
                    instruments.append(_instrument(expiry, strike, option_type, token))
                    token += 1
        instruments.append({
            "name": "BANKNIFTY",
            "instrument_type": "CE",
            "expiry": date(2026, 4, 16),
            "strike": 22500,
            "tradingsymbol": "BANKNIFTY_TEST",
            "instrument_token": 999999,
        })
        return instruments

    def quote(self, instruments):
        self.quote_requests.append(instruments)
        if instruments == "NSE:NIFTY 50":
            return {"NSE:NIFTY 50": {"last_price": self.spot_price}}

        quotes = {}
        for idx, key in enumerate(instruments):
            if key == self.missing_key:
                continue
            quotes[key] = {
                "last_price": 100.0 + idx,
                "oi": 1000 + idx,
                "oi_day_high": 1500 + idx,
                "oi_day_low": 900 + idx,
                "average_price": 95.0 + idx,
                "timestamp": datetime(2026, 4, 12, 9, 16, 5),
                "last_trade_time": datetime(2026, 4, 12, 9, 16, 1),
                "volume": 50 + idx,
                "instrument_token": 5000 + idx,
            }
        return quotes


class OpenInterestPipelineTests(unittest.TestCase):
    def test_strike_window_moves_with_spot_price(self):
        self.assertEqual(build_oi_strike_window(22580.0), list(range(22300, 22901, 50)))
        self.assertEqual(build_oi_strike_window(22640.0), list(range(22350, 22951, 50)))

    def test_select_nearest_expiry_excludes_expired_and_far_contracts(self):
        kite = FakeKite()
        nearest = select_nearest_expiry_instruments(
            kite.instruments("NFO"),
            current_date=date(2026, 4, 12),
        )

        self.assertTrue(nearest)
        self.assertEqual({inst["expiry"] for inst in nearest}, {date(2026, 4, 16)})
        self.assertEqual({inst["name"] for inst in nearest}, {"NIFTY"})
        self.assertEqual({inst["instrument_type"] for inst in nearest}, {"CE", "PE"})

    def test_fetch_snapshot_maps_kite_oi_fields_and_skips_missing_quotes(self):
        missing_key = "NFO:NIFTY16APR22300CE"
        kite = FakeKite(missing_key=missing_key)
        fetcher = NiftyOpenInterestFetcher(kite=kite)

        rows = fetcher.fetch_snapshot(
            current_date=date(2026, 4, 12),
            timestamp=datetime(2026, 4, 12, 9, 16, 0),
        )

        self.assertEqual(kite.requested_exchange, "NFO")
        self.assertEqual(len(rows), 25)
        self.assertEqual({row["expiry_date"] for row in rows}, {"2026-04-16"})
        self.assertEqual(min(int(row["strike_price"]) for row in rows), 22300)
        self.assertEqual(max(int(row["strike_price"]) for row in rows), 22900)
        self.assertNotIn("NIFTY16APR22300CE", {row["tradingsymbol"] for row in rows})
        self.assertTrue({"open_interest", "oi_day_high", "oi_day_low", "vwap"}.issubset(rows[0].keys()))
        self.assertTrue(all(row["vwap"] is not None for row in rows))

    def test_store_and_fetch_open_interest_snapshot_is_duplicate_safe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "oi.db")
            base_row = {
                "timestamp": datetime(2026, 4, 12, 9, 16, 0),
                "symbol": "NIFTY50",
                "exchange": "NFO",
                "tradingsymbol": "NIFTY16APR22500CE",
                "instrument_token": 12345,
                "expiry_date": "2026-04-16",
                "strike_price": 22500,
                "option_type": "CE",
                "spot_price": 22580.0,
                "last_price": 101.0,
                "open_interest": 100,
                "oi_day_high": 120,
                "oi_day_low": 80,
                "vwap": 99.5,
                "last_trade_time": datetime(2026, 4, 12, 9, 16, 0),
                "volume": 50,
            }

            self.assertEqual(store_open_interest_snapshot([base_row], db_path=db_path), 1)
            updated = dict(base_row, open_interest=125, last_price=102.5, vwap=100.25)
            self.assertEqual(store_open_interest_snapshot([updated], db_path=db_path), 1)

            latest = fetch_latest_open_interest_snapshot("NIFTY50", db_path=db_path)
            history = fetch_open_interest_data("NIFTY50", db_path=db_path)

            self.assertEqual(len(latest), 1)
            self.assertEqual(latest[0]["open_interest"], 125)
            self.assertEqual(float(latest[0]["last_price"]), 102.5)
            self.assertEqual(float(latest[0]["vwap"]), 100.25)
            self.assertNotIn("quote_timestamp", latest[0])
            self.assertEqual(len(history), 1)
            self.assertIn("vwap", history.columns)
            self.assertNotIn("quote_timestamp", history.columns)

    def test_open_interest_readers_pair_ce_and_pe_by_timestamp_even_with_mixed_insert_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "oi_order.db")
            mixed_rows = [
                {
                    "timestamp": datetime(2026, 4, 12, 9, 20, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22600PE",
                    "instrument_token": 2004,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22600,
                    "option_type": "PE",
                    "spot_price": 22580.0,
                    "last_price": 204.0,
                    "open_interest": 204,
                    "last_trade_time": datetime(2026, 4, 12, 9, 20, 0),
                    "volume": 40,
                },
                {
                    "timestamp": datetime(2026, 4, 12, 9, 15, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22600PE",
                    "instrument_token": 2002,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22600,
                    "option_type": "PE",
                    "spot_price": 22580.0,
                    "last_price": 202.0,
                    "open_interest": 202,
                    "last_trade_time": datetime(2026, 4, 12, 9, 15, 0),
                    "volume": 20,
                },
                {
                    "timestamp": datetime(2026, 4, 12, 9, 15, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500CE",
                    "instrument_token": 1001,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "CE",
                    "spot_price": 22580.0,
                    "last_price": 101.0,
                    "open_interest": 101,
                    "last_trade_time": datetime(2026, 4, 12, 9, 15, 0),
                    "volume": 10,
                },
                {
                    "timestamp": datetime(2026, 4, 12, 9, 20, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500CE",
                    "instrument_token": 1003,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "CE",
                    "spot_price": 22580.0,
                    "last_price": 103.0,
                    "open_interest": 103,
                    "last_trade_time": datetime(2026, 4, 12, 9, 20, 0),
                    "volume": 30,
                },
                {
                    "timestamp": datetime(2026, 4, 12, 9, 15, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500PE",
                    "instrument_token": 1002,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "PE",
                    "spot_price": 22580.0,
                    "last_price": 102.0,
                    "open_interest": 102,
                    "last_trade_time": datetime(2026, 4, 12, 9, 15, 0),
                    "volume": 15,
                },
                {
                    "timestamp": datetime(2026, 4, 12, 9, 20, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500PE",
                    "instrument_token": 1004,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "PE",
                    "spot_price": 22580.0,
                    "last_price": 104.0,
                    "open_interest": 104,
                    "last_trade_time": datetime(2026, 4, 12, 9, 20, 0),
                    "volume": 35,
                },
                {
                    "timestamp": datetime(2026, 4, 12, 9, 15, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22600CE",
                    "instrument_token": 2001,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22600,
                    "option_type": "CE",
                    "spot_price": 22580.0,
                    "last_price": 201.0,
                    "open_interest": 201,
                    "last_trade_time": datetime(2026, 4, 12, 9, 15, 0),
                    "volume": 18,
                },
                {
                    "timestamp": datetime(2026, 4, 12, 9, 20, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22600CE",
                    "instrument_token": 2003,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22600,
                    "option_type": "CE",
                    "spot_price": 22580.0,
                    "last_price": 203.0,
                    "open_interest": 203,
                    "last_trade_time": datetime(2026, 4, 12, 9, 20, 0),
                    "volume": 38,
                },
            ]

            self.assertEqual(store_open_interest_snapshot(mixed_rows, db_path=db_path), len(mixed_rows))

            latest = fetch_latest_open_interest_snapshot(
                "NIFTY50",
                current_time=datetime(2026, 4, 12, 9, 20, 30),
                db_path=db_path,
            )
            self.assertEqual(
                [(row["timestamp"], row["strike_price"], row["option_type"]) for row in latest],
                [
                    ("2026-04-12 09:20:00", 22500.0, "CE"),
                    ("2026-04-12 09:20:00", 22500.0, "PE"),
                    ("2026-04-12 09:20:00", 22600.0, "CE"),
                    ("2026-04-12 09:20:00", 22600.0, "PE"),
                ],
            )

            history = fetch_open_interest_data("NIFTY50", db_path=db_path).reset_index()
            self.assertEqual(
                [
                    (row["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), row["strike_price"], row["option_type"])
                    for _, row in history.iterrows()
                ],
                [
                    ("2026-04-12 09:15:00", 22500.0, "CE"),
                    ("2026-04-12 09:15:00", 22500.0, "PE"),
                    ("2026-04-12 09:15:00", 22600.0, "CE"),
                    ("2026-04-12 09:15:00", 22600.0, "PE"),
                    ("2026-04-12 09:20:00", 22500.0, "CE"),
                    ("2026-04-12 09:20:00", 22500.0, "PE"),
                    ("2026-04-12 09:20:00", 22600.0, "CE"),
                    ("2026-04-12 09:20:00", 22600.0, "PE"),
                ],
            )

            provider = HistoricalDataProvider(db_path=":memory:")
            provider._data_loaded = True
            provider._open_interest_data = pd.DataFrame(list(reversed(mixed_rows)))
            provider._open_interest_data["timestamp"] = pd.to_datetime(provider._open_interest_data["timestamp"])
            provider_snapshot = provider.fetch_open_interest_snapshot(datetime(2026, 4, 12, 9, 20, 30))
            provider_history = provider.fetch_open_interest_history(datetime(2026, 4, 12, 9, 20, 30))

            self.assertEqual(
                [(row["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), row["strike_price"], row["option_type"]) for row in provider_snapshot],
                [
                    ("2026-04-12 09:20:00", 22500, "CE"),
                    ("2026-04-12 09:20:00", 22500, "PE"),
                    ("2026-04-12 09:20:00", 22600, "CE"),
                    ("2026-04-12 09:20:00", 22600, "PE"),
                ],
            )
            self.assertEqual(
                [
                    (row["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), row["strike_price"], row["option_type"])
                    for _, row in provider_history.iterrows()
                ],
                [
                    ("2026-04-12 09:15:00", 22500, "CE"),
                    ("2026-04-12 09:15:00", 22500, "PE"),
                    ("2026-04-12 09:15:00", 22600, "CE"),
                    ("2026-04-12 09:15:00", 22600, "PE"),
                    ("2026-04-12 09:20:00", 22500, "CE"),
                    ("2026-04-12 09:20:00", 22500, "PE"),
                    ("2026-04-12 09:20:00", 22600, "CE"),
                    ("2026-04-12 09:20:00", 22600, "PE"),
                ],
            )

    def test_open_interest_schema_migrates_quote_timestamp_to_nullable_vwap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "oi_old.db")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("""
                    CREATE TABLE option_open_interest (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        exchange TEXT NOT NULL,
                        tradingsymbol TEXT NOT NULL,
                        instrument_token INTEGER,
                        expiry_date TEXT NOT NULL,
                        strike_price REAL NOT NULL,
                        option_type TEXT NOT NULL,
                        spot_price REAL,
                        last_price REAL,
                        open_interest INTEGER,
                        oi_day_high INTEGER,
                        oi_day_low INTEGER,
                        quote_timestamp TEXT,
                        last_trade_time TEXT,
                        volume INTEGER
                    );
                """)
                conn.execute("""
                    INSERT INTO option_open_interest (
                        timestamp, symbol, exchange, tradingsymbol, instrument_token,
                        expiry_date, strike_price, option_type, spot_price, last_price,
                        open_interest, oi_day_high, oi_day_low, quote_timestamp,
                        last_trade_time, volume
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "2026-04-12 09:16:00", "NIFTY50", "NFO", "NIFTY16APR22500CE", 12345,
                    "2026-04-16", 22500, "CE", 22580.0, 101.0,
                    100, 120, 80, "2026-04-12 09:16:01",
                    "2026-04-12 09:16:00", 50,
                ))
                conn.commit()

                create_open_interest_table(conn)

                columns = {row[1] for row in conn.execute("PRAGMA table_info(option_open_interest)").fetchall()}
                self.assertIn("vwap", columns)
                self.assertNotIn("quote_timestamp", columns)

                row = conn.execute("""
                    SELECT tradingsymbol, open_interest, vwap
                    FROM option_open_interest
                """).fetchone()
                self.assertEqual(row[0], "NIFTY16APR22500CE")
                self.assertEqual(row[1], 100)
                self.assertIsNone(row[2])

                indexes = {row[1] for row in conn.execute("PRAGMA index_list(option_open_interest)").fetchall()}
                self.assertTrue({
                    "ux_option_oi_timestamp_symbol_tradingsymbol",
                    "idx_option_oi_symbol_timestamp",
                    "idx_option_oi_contract_replay",
                }.issubset(indexes))
            finally:
                conn.close()

            self.assertEqual(store_open_interest_snapshot([{
                "timestamp": datetime(2026, 4, 12, 9, 16, 0),
                "symbol": "NIFTY50",
                "exchange": "NFO",
                "tradingsymbol": "NIFTY16APR22500CE",
                "instrument_token": 12345,
                "expiry_date": "2026-04-16",
                "strike_price": 22500,
                "option_type": "CE",
                "spot_price": 22580.0,
                "last_price": 102.5,
                "open_interest": 125,
                "averagePrice": 100.75,
                "last_trade_time": datetime(2026, 4, 12, 9, 16, 0),
                "volume": 60,
            }], db_path=db_path), 1)

            latest = fetch_latest_open_interest_snapshot("NIFTY50", db_path=db_path)
            self.assertEqual(len(latest), 1)
            self.assertEqual(latest[0]["open_interest"], 125)
            self.assertEqual(float(latest[0]["vwap"]), 100.75)

    def test_backtesting_provider_open_interest_snapshot_is_point_in_time(self):
        provider = HistoricalDataProvider(db_path=":memory:")
        provider._data_loaded = True
        provider._open_interest_data = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-04-12 09:16:00"),
                    "tradingsymbol": "NIFTY16APR22500CE",
                    "strike_price": 22500,
                    "option_type": "CE",
                    "open_interest": 100,
                },
                {
                    "timestamp": pd.Timestamp("2026-04-12 09:16:00"),
                    "tradingsymbol": "NIFTY16APR22500PE",
                    "strike_price": 22500,
                    "option_type": "PE",
                    "open_interest": 200,
                },
                {
                    "timestamp": pd.Timestamp("2026-04-12 09:18:00"),
                    "tradingsymbol": "NIFTY16APR22600CE",
                    "strike_price": 22600,
                    "option_type": "CE",
                    "open_interest": 999,
                },
            ]
        )

        snapshot = provider.fetch_open_interest_snapshot(datetime(2026, 4, 12, 9, 17, 0))

        self.assertEqual(len(snapshot), 2)
        self.assertEqual({row["strike_price"] for row in snapshot}, {22500})
        self.assertNotIn(999, {row["open_interest"] for row in snapshot})

    def test_rebuild_open_interest_5m_derives_bucket_vwap_and_carry_forward(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "oi_5m.db")
            rows = [
                {
                    "timestamp": datetime(2026, 4, 12, 9, 16, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500CE",
                    "instrument_token": 12345,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "CE",
                    "spot_price": 22580.0,
                    "last_price": 100.0,
                    "open_interest": 100,
                    "oi_day_high": 120,
                    "oi_day_low": 80,
                    "vwap": 11.0,
                    "last_trade_time": datetime(2026, 4, 12, 9, 16, 0),
                    "volume": 100,
                },
                {
                    "timestamp": datetime(2026, 4, 12, 9, 17, 30),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500CE",
                    "instrument_token": 12345,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "CE",
                    "spot_price": 22582.0,
                    "last_price": 105.0,
                    "open_interest": 105,
                    "oi_day_high": 125,
                    "oi_day_low": 80,
                    "vwap": 12.0,
                    "last_trade_time": datetime(2026, 4, 12, 9, 17, 30),
                    "volume": 150,
                },
                {
                    "timestamp": datetime(2026, 4, 12, 9, 19, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500CE",
                    "instrument_token": 12345,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "CE",
                    "spot_price": 22584.0,
                    "last_price": 95.0,
                    "open_interest": 110,
                    "oi_day_high": 130,
                    "oi_day_low": 80,
                    "vwap": 13.0,
                    "last_trade_time": datetime(2026, 4, 12, 9, 19, 0),
                    "volume": 200,
                },
                {
                    "timestamp": datetime(2026, 4, 12, 9, 26, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500CE",
                    "instrument_token": 12345,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "CE",
                    "spot_price": 22590.0,
                    "last_price": 110.0,
                    "open_interest": 115,
                    "oi_day_high": 135,
                    "oi_day_low": 80,
                    "vwap": 14.0,
                    "last_trade_time": datetime(2026, 4, 12, 9, 26, 0),
                    "volume": 240,
                },
            ]

            self.assertEqual(store_open_interest_snapshot(rows, db_path=db_path), 4)
            rebuilt = rebuild_open_interest_5m_for_day("NIFTY50", date(2026, 4, 12), db_path=db_path)
            self.assertEqual(rebuilt, 3)

            derived = fetch_open_interest_5m_data("NIFTY50", db_path=db_path)
            self.assertEqual(list(derived.index.strftime("%Y-%m-%d %H:%M:%S")), [
                "2026-04-12 09:15:00",
                "2026-04-12 09:20:00",
                "2026-04-12 09:25:00",
            ])

            first_bucket = derived.iloc[0]
            self.assertAlmostEqual(float(first_bucket["vwap"]), 100.0)
            self.assertEqual(int(first_bucket["bucket_volume"]), 100)
            self.assertEqual(int(first_bucket["is_carry_forward"]), 0)
            self.assertEqual(first_bucket["source_snapshot_count"], 3)

            second_bucket = derived.iloc[1]
            self.assertAlmostEqual(float(second_bucket["vwap"]), 100.0)
            self.assertEqual(int(second_bucket["bucket_volume"]), 0)
            self.assertEqual(int(second_bucket["is_carry_forward"]), 1)

            third_bucket = derived.iloc[2]
            self.assertAlmostEqual(float(third_bucket["vwap"]), 110.0)
            self.assertEqual(int(third_bucket["bucket_volume"]), 40)
            self.assertEqual(int(third_bucket["is_carry_forward"]), 0)

    def test_rebuild_open_interest_5m_resets_volume_baseline_on_day_boundary_and_anomaly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "oi_5m_reset.db")
            rows = [
                {
                    "timestamp": datetime(2026, 4, 12, 15, 29, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500PE",
                    "instrument_token": 22345,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "PE",
                    "spot_price": 22550.0,
                    "last_price": 200.0,
                    "open_interest": 100,
                    "oi_day_high": 150,
                    "oi_day_low": 70,
                    "last_trade_time": datetime(2026, 4, 12, 15, 29, 0),
                    "volume": 500,
                },
                {
                    "timestamp": datetime(2026, 4, 13, 9, 16, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500PE",
                    "instrument_token": 22345,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "PE",
                    "spot_price": 22540.0,
                    "last_price": 210.0,
                    "open_interest": 105,
                    "oi_day_high": 155,
                    "oi_day_low": 70,
                    "last_trade_time": datetime(2026, 4, 13, 9, 16, 0),
                    "volume": 10,
                },
                {
                    "timestamp": datetime(2026, 4, 13, 9, 17, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500PE",
                    "instrument_token": 22345,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "PE",
                    "spot_price": 22538.0,
                    "last_price": 220.0,
                    "open_interest": 108,
                    "oi_day_high": 158,
                    "oi_day_low": 70,
                    "last_trade_time": datetime(2026, 4, 13, 9, 17, 0),
                    "volume": 30,
                },
                {
                    "timestamp": datetime(2026, 4, 13, 9, 26, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500PE",
                    "instrument_token": 22345,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "PE",
                    "spot_price": 22532.0,
                    "last_price": 230.0,
                    "open_interest": 110,
                    "oi_day_high": 160,
                    "oi_day_low": 70,
                    "last_trade_time": datetime(2026, 4, 13, 9, 26, 0),
                    "volume": 5,
                },
                {
                    "timestamp": datetime(2026, 4, 13, 9, 27, 0),
                    "symbol": "NIFTY50",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY16APR22500PE",
                    "instrument_token": 22345,
                    "expiry_date": "2026-04-16",
                    "strike_price": 22500,
                    "option_type": "PE",
                    "spot_price": 22530.0,
                    "last_price": 240.0,
                    "open_interest": 112,
                    "oi_day_high": 162,
                    "oi_day_low": 70,
                    "last_trade_time": datetime(2026, 4, 13, 9, 27, 0),
                    "volume": 25,
                },
            ]

            self.assertEqual(store_open_interest_snapshot(rows, db_path=db_path), 5)
            rebuild_open_interest_5m_for_day("NIFTY50", date(2026, 4, 12), db_path=db_path)
            rebuild_open_interest_5m_for_day("NIFTY50", date(2026, 4, 13), db_path=db_path)

            day_two = fetch_open_interest_5m_data(
                "NIFTY50",
                start="2026-04-13 09:15:00",
                end="2026-04-13 09:30:00",
                db_path=db_path,
            )
            self.assertEqual(list(day_two.index.strftime("%Y-%m-%d %H:%M:%S")), [
                "2026-04-13 09:15:00",
                "2026-04-13 09:20:00",
                "2026-04-13 09:25:00",
            ])
            self.assertEqual(int(day_two.iloc[0]["bucket_volume"]), 20)
            self.assertAlmostEqual(float(day_two.iloc[0]["vwap"]), 220.0)
            self.assertEqual(int(day_two.iloc[2]["bucket_volume"]), 20)
            self.assertAlmostEqual(float(day_two.iloc[2]["vwap"]), 240.0)

    def test_fetch_latest_open_interest_5m_snapshot_and_provider_are_bucket_aligned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "oi_5m_snapshot.db")
            conn = sqlite3.connect(db_path)
            try:
                create_open_interest_5m_table(conn)
                conn.executemany("""
                    INSERT INTO option_open_interest_5m (
                        timestamp, symbol, exchange, tradingsymbol, instrument_token,
                        expiry_date, strike_price, option_type, spot_price, last_price,
                        open_interest, oi_day_high, oi_day_low, vwap, bucket_volume,
                        is_carry_forward, source_start_timestamp, source_end_timestamp,
                        source_snapshot_count, last_trade_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    (
                        "2026-04-12 09:15:00", "NIFTY50", "NFO", "NIFTY16APR22500CE", 12345,
                        "2026-04-16", 22500, "CE", 22580.0, 101.0,
                        100, 120, 80, 100.0, 50, 0,
                        "2026-04-12 09:16:00", "2026-04-12 09:19:00", 3, "2026-04-12 09:19:00",
                    ),
                    (
                        "2026-04-12 09:20:00", "NIFTY50", "NFO", "NIFTY16APR22500CE", 12345,
                        "2026-04-16", 22500, "CE", 22582.0, 102.0,
                        110, 125, 80, 101.5, 10, 0,
                        "2026-04-12 09:21:00", "2026-04-12 09:21:00", 1, "2026-04-12 09:21:00",
                    ),
                ])
                conn.commit()
            finally:
                conn.close()

            latest = fetch_latest_open_interest_5m_snapshot(
                "NIFTY50",
                current_time=datetime(2026, 4, 12, 9, 24, 59),
                db_path=db_path,
            )
            self.assertEqual(len(latest), 1)
            self.assertEqual(latest[0]["timestamp"], "2026-04-12 09:20:00")

            provider = HistoricalDataProvider(db_path=":memory:")
            provider._data_loaded = True
            provider._open_interest_5m_data = pd.DataFrame([
                {
                    "timestamp": pd.Timestamp("2026-04-12 09:15:00"),
                    "tradingsymbol": "NIFTY16APR22500CE",
                    "strike_price": 22500,
                    "option_type": "CE",
                    "open_interest": 100,
                    "vwap": 100.0,
                },
                {
                    "timestamp": pd.Timestamp("2026-04-12 09:20:00"),
                    "tradingsymbol": "NIFTY16APR22500CE",
                    "strike_price": 22500,
                    "option_type": "CE",
                    "open_interest": 110,
                    "vwap": 101.5,
                },
                {
                    "timestamp": pd.Timestamp("2026-04-12 09:25:00"),
                    "tradingsymbol": "NIFTY16APR22600CE",
                    "strike_price": 22600,
                    "option_type": "CE",
                    "open_interest": 999,
                    "vwap": 999.0,
                },
            ])

            snapshot = provider.fetch_open_interest_5m_snapshot(datetime(2026, 4, 12, 9, 24, 59))
            history = provider.fetch_open_interest_5m_history(datetime(2026, 4, 12, 9, 24, 59))

            self.assertEqual(len(snapshot), 1)
            self.assertEqual(snapshot[0]["strike_price"], 22500)
            self.assertEqual(float(snapshot[0]["vwap"]), 101.5)
            self.assertEqual(len(history), 2)
            self.assertNotIn(999, set(history["open_interest"]))

    def test_open_interest_5m_readers_pair_ce_and_pe_by_bucket_even_with_mixed_insert_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "oi_5m_order.db")
            conn = sqlite3.connect(db_path)
            try:
                create_open_interest_5m_table(conn)
                conn.executemany("""
                    INSERT INTO option_open_interest_5m (
                        timestamp, symbol, exchange, tradingsymbol, instrument_token,
                        expiry_date, strike_price, option_type, spot_price, last_price,
                        open_interest, oi_day_high, oi_day_low, vwap, bucket_volume,
                        is_carry_forward, source_start_timestamp, source_end_timestamp,
                        source_snapshot_count, last_trade_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    ("2026-04-12 09:20:00", "NIFTY50", "NFO", "NIFTY16APR22600PE", 2004, "2026-04-16", 22600, "PE", 22580.0, 204.0, 204, 220, 180, 204.0, 40, 0, None, None, 1, None),
                    ("2026-04-12 09:15:00", "NIFTY50", "NFO", "NIFTY16APR22600PE", 2002, "2026-04-16", 22600, "PE", 22580.0, 202.0, 202, 220, 180, 202.0, 20, 0, None, None, 1, None),
                    ("2026-04-12 09:15:00", "NIFTY50", "NFO", "NIFTY16APR22500CE", 1001, "2026-04-16", 22500, "CE", 22580.0, 101.0, 101, 120, 80, 101.0, 10, 0, None, None, 1, None),
                    ("2026-04-12 09:20:00", "NIFTY50", "NFO", "NIFTY16APR22500CE", 1003, "2026-04-16", 22500, "CE", 22580.0, 103.0, 103, 120, 80, 103.0, 30, 0, None, None, 1, None),
                    ("2026-04-12 09:15:00", "NIFTY50", "NFO", "NIFTY16APR22500PE", 1002, "2026-04-16", 22500, "PE", 22580.0, 102.0, 102, 120, 80, 102.0, 15, 0, None, None, 1, None),
                    ("2026-04-12 09:20:00", "NIFTY50", "NFO", "NIFTY16APR22500PE", 1004, "2026-04-16", 22500, "PE", 22580.0, 104.0, 104, 120, 80, 104.0, 35, 0, None, None, 1, None),
                    ("2026-04-12 09:15:00", "NIFTY50", "NFO", "NIFTY16APR22600CE", 2001, "2026-04-16", 22600, "CE", 22580.0, 201.0, 201, 220, 180, 201.0, 18, 0, None, None, 1, None),
                    ("2026-04-12 09:20:00", "NIFTY50", "NFO", "NIFTY16APR22600CE", 2003, "2026-04-16", 22600, "CE", 22580.0, 203.0, 203, 220, 180, 203.0, 38, 0, None, None, 1, None),
                ])
                conn.commit()
            finally:
                conn.close()

            latest = fetch_latest_open_interest_5m_snapshot(
                "NIFTY50",
                current_time=datetime(2026, 4, 12, 9, 24, 59),
                db_path=db_path,
            )
            self.assertEqual(
                [(row["timestamp"], row["strike_price"], row["option_type"]) for row in latest],
                [
                    ("2026-04-12 09:20:00", 22500.0, "CE"),
                    ("2026-04-12 09:20:00", 22500.0, "PE"),
                    ("2026-04-12 09:20:00", 22600.0, "CE"),
                    ("2026-04-12 09:20:00", 22600.0, "PE"),
                ],
            )

            history = fetch_open_interest_5m_data("NIFTY50", db_path=db_path).reset_index()
            self.assertEqual(
                [
                    (row["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), row["strike_price"], row["option_type"])
                    for _, row in history.iterrows()
                ],
                [
                    ("2026-04-12 09:15:00", 22500.0, "CE"),
                    ("2026-04-12 09:15:00", 22500.0, "PE"),
                    ("2026-04-12 09:15:00", 22600.0, "CE"),
                    ("2026-04-12 09:15:00", 22600.0, "PE"),
                    ("2026-04-12 09:20:00", 22500.0, "CE"),
                    ("2026-04-12 09:20:00", 22500.0, "PE"),
                    ("2026-04-12 09:20:00", 22600.0, "CE"),
                    ("2026-04-12 09:20:00", 22600.0, "PE"),
                ],
            )

            provider = HistoricalDataProvider(db_path=":memory:")
            provider._data_loaded = True
            provider._open_interest_5m_data = pd.DataFrame([
                {"timestamp": pd.Timestamp("2026-04-12 09:20:00"), "tradingsymbol": "NIFTY16APR22600PE", "strike_price": 22600, "option_type": "PE", "open_interest": 204, "vwap": 204.0},
                {"timestamp": pd.Timestamp("2026-04-12 09:15:00"), "tradingsymbol": "NIFTY16APR22600PE", "strike_price": 22600, "option_type": "PE", "open_interest": 202, "vwap": 202.0},
                {"timestamp": pd.Timestamp("2026-04-12 09:15:00"), "tradingsymbol": "NIFTY16APR22500CE", "strike_price": 22500, "option_type": "CE", "open_interest": 101, "vwap": 101.0},
                {"timestamp": pd.Timestamp("2026-04-12 09:20:00"), "tradingsymbol": "NIFTY16APR22500CE", "strike_price": 22500, "option_type": "CE", "open_interest": 103, "vwap": 103.0},
                {"timestamp": pd.Timestamp("2026-04-12 09:15:00"), "tradingsymbol": "NIFTY16APR22500PE", "strike_price": 22500, "option_type": "PE", "open_interest": 102, "vwap": 102.0},
                {"timestamp": pd.Timestamp("2026-04-12 09:20:00"), "tradingsymbol": "NIFTY16APR22500PE", "strike_price": 22500, "option_type": "PE", "open_interest": 104, "vwap": 104.0},
                {"timestamp": pd.Timestamp("2026-04-12 09:15:00"), "tradingsymbol": "NIFTY16APR22600CE", "strike_price": 22600, "option_type": "CE", "open_interest": 201, "vwap": 201.0},
                {"timestamp": pd.Timestamp("2026-04-12 09:20:00"), "tradingsymbol": "NIFTY16APR22600CE", "strike_price": 22600, "option_type": "CE", "open_interest": 203, "vwap": 203.0},
            ])

            provider_snapshot = provider.fetch_open_interest_5m_snapshot(datetime(2026, 4, 12, 9, 24, 59))
            provider_history = provider.fetch_open_interest_5m_history(datetime(2026, 4, 12, 9, 24, 59))

            self.assertEqual(
                [(row["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), row["strike_price"], row["option_type"]) for row in provider_snapshot],
                [
                    ("2026-04-12 09:20:00", 22500, "CE"),
                    ("2026-04-12 09:20:00", 22500, "PE"),
                    ("2026-04-12 09:20:00", 22600, "CE"),
                    ("2026-04-12 09:20:00", 22600, "PE"),
                ],
            )
            self.assertEqual(
                [
                    (row["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), row["strike_price"], row["option_type"])
                    for _, row in provider_history.iterrows()
                ],
                [
                    ("2026-04-12 09:15:00", 22500, "CE"),
                    ("2026-04-12 09:15:00", 22500, "PE"),
                    ("2026-04-12 09:15:00", 22600, "CE"),
                    ("2026-04-12 09:15:00", 22600, "PE"),
                    ("2026-04-12 09:20:00", 22500, "CE"),
                    ("2026-04-12 09:20:00", 22500, "PE"),
                    ("2026-04-12 09:20:00", 22600, "CE"),
                    ("2026-04-12 09:20:00", 22600, "PE"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
