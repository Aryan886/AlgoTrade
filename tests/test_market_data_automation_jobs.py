import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schedule

from utils.market_data_automation import MarketDataAutomation


def _build_automation():
    with patch("utils.market_data_automation.PaperTraderDonchian", return_value=Mock()), \
         patch("utils.market_data_automation.PaperTraderSMA", return_value=Mock()), \
         patch("utils.market_data_automation.EquityPaperTrader", return_value=Mock()), \
         patch("utils.market_data_automation.NiftyPaperBot", return_value=Mock()), \
         patch("utils.market_data_automation.OIExpiryPaperBot", return_value=Mock()):
        return MarketDataAutomation()


class ScheduledJobsTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(schedule.clear)

    def _run_every_scheduled_job(self, automation):
        automation.setup_schedule()
        for job in schedule.get_jobs():
            job.job_func()

    def test_donchian_strategy_runs_once_per_cycle(self):
        automation = _build_automation()
        automation.is_market_open = Mock(return_value=True)
        automation.paper_trader.has_active_position.return_value = False
        automation.paper_trader.symbol = "NIFTY50"
        for name in dir(automation):
            if name.startswith("fetch_") or name in (
                "run_sma_paper_trading_cycle",
                "run_nifty_paper_trading_cycle",
            ):
                setattr(automation, name, Mock())

        with patch("utils.market_data_automation.donchian_ao_strategy", return_value=None) as strategy, \
             patch("utils.market_data_automation.calculate_and_store_vix"), \
             patch("utils.market_data_automation.calculate_and_store_high_accuracy_delta"):
            self._run_every_scheduled_job(automation)

        self.assertEqual(strategy.call_count, 1)

    def _run_option_jobs(self, market_open):
        """Run every scheduled job with candle/trading jobs stubbed out."""
        automation = _build_automation()
        automation.is_market_open = Mock(return_value=market_open)
        for name in dir(automation):
            if name.startswith("fetch_") or name.endswith("_paper_trading_cycle"):
                setattr(automation, name, Mock())

        with patch("utils.market_data_automation.calculate_and_store_vix", return_value=12.5) as vix, \
             patch(
                 "utils.market_data_automation.calculate_and_store_high_accuracy_delta",
                 return_value={"spot_price": 25000.0, "strike_band": []},
             ) as delta:
            self._run_every_scheduled_job(automation)
        return vix, delta

    def test_delta_and_vix_jobs_run_while_market_is_open(self):
        vix, delta = self._run_option_jobs(market_open=True)

        self.assertEqual(vix.call_count, 1)
        self.assertEqual(delta.call_count, 1)

    def test_delta_and_vix_jobs_are_skipped_when_market_is_closed(self):
        vix, delta = self._run_option_jobs(market_open=False)

        vix.assert_not_called()
        delta.assert_not_called()


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(schedule.clear)

    def _start(self, prepare):
        automation = _build_automation()
        automation.test_connection = Mock(return_value=True)
        with patch("utils.market_data_automation.prepare_database", prepare), \
             patch("utils.market_data_automation.threading.Thread") as thread:
            automation.start()
        return thread

    def test_start_prepares_the_database_before_the_scheduler_runs(self):
        prepare = Mock()

        thread = self._start(prepare)

        prepare.assert_called_once_with()
        thread.return_value.start.assert_called_once()

    def test_start_continues_when_database_preparation_fails(self):
        prepare = Mock(side_effect=RuntimeError("database is locked"))

        thread = self._start(prepare)

        thread.return_value.start.assert_called_once()


class _FakeKite:
    """Kite stand-in that records every call made to the broker."""

    SPOT = 25000.0

    def __init__(self, spot_available=True):
        self.spot_available = spot_available
        self.instrument_calls = []
        self.quote_calls = []

    def instruments(self, exchange):
        self.instrument_calls.append(exchange)
        rows = []
        for strike in range(24700, 25350, 50):
            for option_type in ("CE", "PE"):
                rows.append({
                    "name": "NIFTY",
                    "instrument_type": option_type,
                    "expiry": "2099-01-07",
                    "strike": strike,
                    "tradingsymbol": f"NIFTY99107{strike}{option_type}",
                    "instrument_token": strike,
                })
        return rows

    def quote(self, symbols):
        self.quote_calls.append(symbols)
        if isinstance(symbols, str):
            if not self.spot_available:
                return {}
            return {symbols: {"last_price": self.SPOT}}
        return {s: {"last_price": 100.0, "oi": 10, "volume": 5} for s in symbols}


class BrokerUsageTests(unittest.TestCase):
    def test_open_interest_job_downloads_instrument_list_once_across_runs(self):
        automation = _build_automation()
        automation.is_market_open = Mock(return_value=True)
        kite = _FakeKite()

        with patch("utils.open_interest_fetcher.kite_from_saved_token", return_value=kite), \
             patch("utils.open_interest_fetcher.store_open_interest_snapshot", return_value=26) as store, \
             patch("utils.open_interest_fetcher.rebuild_open_interest_5m_for_rows"):
            automation.fetch_open_interest_data()
            automation.last_fetch_times["oi"] = None  # let the next minute's run proceed
            automation.fetch_open_interest_data()

        self.assertEqual(store.call_count, 2)
        self.assertEqual(kite.instrument_calls, ["NFO"])

    def test_vix_job_computes_vix_once_per_run(self):
        automation = _build_automation()
        automation.is_market_open = Mock(return_value=True)
        kite = _FakeKite(spot_available=False)  # VIX run stops right after the spot lookup

        with patch("utils.vix_fetcher.kite_from_saved_token", return_value=kite):
            automation.run_vix_job()

        self.assertEqual(kite.quote_calls, ["NSE:NIFTY 50"])


if __name__ == "__main__":
    unittest.main()
