import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from functools import partial
from unittest.mock import Mock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import db_func, open_interest_fetcher, vix_fetcher
from utils.db_func import fetch_latest_delta_data, fetch_latest_open_interest_snapshot, fetch_vix_data
from utils.db_setup import create_tables
from utils.iv import ProductionIVCalculator
from utils.market_data_automation import MarketDataAutomation

SPOT = 25010.0
SPOT_KEY = "NSE:NIFTY 50"


class _FakeKite:
    """Kite stand-in with a two-expiry NIFTY chain; records every quote request."""

    def __init__(self):
        self.quote_calls = []
        self.fail_chain_quotes = False
        self.nearest_expiry = date.today() + timedelta(days=6)
        self.far_expiry = date.today() + timedelta(days=13)
        self._pricer = ProductionIVCalculator()

    def instruments(self, exchange):
        rows = []
        for expiry in (self.nearest_expiry, self.far_expiry):
            for strike in range(23000, 27050, 50):
                for option_type in ("CE", "PE"):
                    rows.append({
                        "name": "NIFTY",
                        "instrument_type": option_type,
                        "expiry": expiry,
                        "strike": float(strike),
                        "tradingsymbol": f"NIFTY{expiry:%y%m%d}{strike}{option_type}",
                        "instrument_token": len(rows) + 1,
                    })
        return rows

    def quote(self, symbols):
        self.quote_calls.append(symbols)
        if isinstance(symbols, str):
            return {symbols: {"last_price": SPOT}}
        if self.fail_chain_quotes:
            raise RuntimeError("Too many requests")
        return {symbol: self._option_quote(symbol) for symbol in symbols}

    def _option_quote(self, symbol):
        strike, option_type = float(symbol[-7:-2]), symbol[-2:]
        expiry = self.far_expiry if f"{self.far_expiry:%y%m%d}" in symbol else self.nearest_expiry
        years = (expiry - date.today()).days / 365.0
        price = round(self._pricer.black_scholes_price(SPOT, strike, years, 0.065, 0.01, 0.15, option_type), 2)
        price = max(price, 0.05)
        return {
            "last_price": price,
            "oi": 5000,
            "oi_day_high": 6000,
            "oi_day_low": 4000,
            "average_price": price,
            "volume": 100,
            "depth": {"buy": [{"price": price - 0.05}], "sell": [{"price": price + 0.05}]},
        }

    @property
    def spot_calls(self):
        return [call for call in self.quote_calls if call == SPOT_KEY]

    @property
    def chain_calls(self):
        return [call for call in self.quote_calls if not isinstance(call, str)]


class SharedOptionChainTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.db_path = os.path.join(temp_dir.name, "test.db")
        create_tables(self.db_path)
        # The live delta_cache carries two columns create_tables does not add yet.
        conn = sqlite3.connect(self.db_path)
        conn.execute("ALTER TABLE delta_cache ADD COLUMN ltp REAL")
        conn.execute("ALTER TABLE delta_cache ADD COLUMN tradingsymbol TEXT")
        conn.commit()
        conn.close()
        self.kite = _FakeKite()

        patches = [
            patch("broker.zerodha_client.kite_from_saved_token", return_value=self.kite),
            patch("utils.vix_fetcher.kite_from_saved_token", return_value=self.kite),
            patch("utils.open_interest_fetcher.kite_from_saved_token", return_value=self.kite),
            patch("utils.vix_fetcher.time.sleep"),
            # Point the three scheduled jobs at the temporary database.
            patch(
                "utils.market_data_automation.calculate_and_store_high_accuracy_delta",
                partial(db_func.calculate_and_store_high_accuracy_delta, db_path=self.db_path),
            ),
            patch(
                "utils.market_data_automation.fetch_and_store_open_interest",
                partial(open_interest_fetcher.fetch_and_store_open_interest, db_path=self.db_path),
            ),
            patch(
                "utils.market_data_automation.calculate_and_store_vix",
                partial(vix_fetcher.calculate_and_store_vix, db_path=self.db_path),
            ),
            patch("utils.market_data_automation.PaperTraderDonchian", return_value=Mock()),
            patch("utils.market_data_automation.PaperTraderSMA", return_value=Mock()),
            patch("utils.market_data_automation.EquityPaperTrader", return_value=Mock()),
            patch("utils.market_data_automation.NiftyPaperBot", return_value=Mock()),
            patch("utils.market_data_automation.OIExpiryPaperBot", return_value=Mock()),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        self.automation = MarketDataAutomation()
        self.automation.is_market_open = Mock(return_value=True)

    def _run_option_jobs(self):
        self.automation.run_delta_job()
        self.automation.fetch_open_interest_data()
        self.automation.run_vix_job()

    def test_one_spot_quote_and_one_chain_request_cover_delta_oi_and_vix(self):
        self._run_option_jobs()

        self.assertEqual(len(self.kite.spot_calls), 1)
        self.assertEqual(len(self.kite.chain_calls), 1)
        self.assertEqual(len(self.kite.quote_calls), 2)

    def test_all_three_jobs_store_their_output_from_the_shared_chain(self):
        self._run_option_jobs()

        nearest = self.kite.nearest_expiry.isoformat()

        deltas = fetch_latest_delta_data("NIFTY50", db_path=self.db_path)
        self.assertTrue(deltas)
        self.assertEqual({str(row["expiry"])[:10] for row in deltas}, {nearest})

        oi_rows = fetch_latest_open_interest_snapshot("NIFTY50", db_path=self.db_path)
        self.assertEqual(len(oi_rows), 26)  # ATM +/- 300 in steps of 50, CE and PE
        self.assertEqual({row["expiry_date"] for row in oi_rows}, {nearest})
        self.assertEqual({row["open_interest"] for row in oi_rows}, {5000})
        self.assertEqual({row["oi_day_high"] for row in oi_rows}, {6000})

        vix = fetch_vix_data("NIFTY50", db_path=self.db_path)
        self.assertEqual(len(vix), 1)
        self.assertGreater(float(vix["vix_value"].iloc[0]), 0)

    def test_chain_is_requested_only_for_the_nearest_expiry(self):
        self._run_option_jobs()

        requested = [key for call in self.kite.chain_calls for key in call]
        self.assertTrue(requested)
        far_marker = f"{self.kite.far_expiry:%y%m%d}"
        self.assertFalse([key for key in requested if far_marker in key])
        self.assertEqual(len(requested), len(set(requested)))

    def test_next_minute_fetches_fresh_quotes(self):
        self._run_option_jobs()
        next_minute = datetime.now() + timedelta(minutes=1)

        with patch("utils.live_quotes.datetime") as clock:
            clock.now.return_value = next_minute
            self.automation.last_fetch_times["oi"] = None
            self.automation.fetch_open_interest_data()

        self.assertEqual(len(self.kite.spot_calls), 2)
        self.assertEqual(len(self.kite.chain_calls), 2)

    def test_failed_chain_request_is_retried_by_the_next_job(self):
        self.kite.fail_chain_quotes = True
        self.automation.run_delta_job()
        self.kite.fail_chain_quotes = False

        self.automation.fetch_open_interest_data()

        oi_rows = fetch_latest_open_interest_snapshot("NIFTY50", db_path=self.db_path)
        self.assertEqual(len(oi_rows), 26)


if __name__ == "__main__":
    unittest.main()
