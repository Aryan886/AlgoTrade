import os
import sys
import tempfile
import unittest
from datetime import date, datetime

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.data_provider import HistoricalDataProvider
from utils.db_func import (
    fetch_latest_open_interest_snapshot,
    fetch_open_interest_data,
    store_open_interest_snapshot,
)
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
        self.assertTrue({"open_interest", "oi_day_high", "oi_day_low"}.issubset(rows[0].keys()))

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
                "quote_timestamp": datetime(2026, 4, 12, 9, 16, 1),
                "last_trade_time": datetime(2026, 4, 12, 9, 16, 0),
                "volume": 50,
            }

            self.assertEqual(store_open_interest_snapshot([base_row], db_path=db_path), 1)
            updated = dict(base_row, open_interest=125, last_price=102.5)
            self.assertEqual(store_open_interest_snapshot([updated], db_path=db_path), 1)

            latest = fetch_latest_open_interest_snapshot("NIFTY50", db_path=db_path)
            history = fetch_open_interest_data("NIFTY50", db_path=db_path)

            self.assertEqual(len(latest), 1)
            self.assertEqual(latest[0]["open_interest"], 125)
            self.assertEqual(float(latest[0]["last_price"]), 102.5)
            self.assertEqual(len(history), 1)

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


if __name__ == "__main__":
    unittest.main()
