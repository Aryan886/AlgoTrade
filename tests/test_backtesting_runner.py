import os
import sys
import unittest
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import Mock
from contextlib import redirect_stdout

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.exceptions import BacktestDataCoverageError
from backtesting.runner import BacktestConfig, BacktestRunner


def make_coverage(start: datetime | None, end: datetime | None, rows: int = 1):
    return {"rows": rows, "start": start, "end": end}


class FakeCoverageProvider:
    def __init__(self, data_start, data_end, coverage, trading_days):
        self.data_start = data_start
        self.data_end = data_end
        self.coverage = coverage
        self.trading_days = trading_days

    def load_all_data(self, start_date, end_date):
        return None

    def get_available_date_range(self):
        return self.data_start, self.data_end

    def get_trading_days(self, start, end):
        return self.trading_days

    def get_backtest_data_coverage(self):
        return self.coverage


class BacktestRunnerCoverageTests(unittest.TestCase):
    def _make_runner(self, start_date: datetime, end_date: datetime, step_interval: timedelta | None = None) -> BacktestRunner:
        config = BacktestConfig(
            start_date=start_date,
            end_date=end_date,
            symbol="NIFTY50",
            db_path="db/trading_bot.db",
            verbose=False,
            step_interval=step_interval or timedelta(days=1),
        )
        return BacktestRunner(config)

    def test_run_fails_fast_with_actionable_error_when_vix_coverage_is_missing(self):
        start_date = datetime(2026, 3, 24, 9, 15)
        end_date = datetime(2026, 3, 24, 15, 30)
        runner = self._make_runner(start_date, end_date)
        coverage = {
            "market_data_1m": make_coverage(start_date, end_date, rows=100),
            "vix_data": make_coverage(None, None, rows=0),
            "delta_cache": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
            "option_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
        }
        fake_provider = FakeCoverageProvider(start_date, end_date, coverage, [datetime(2026, 3, 24)])
        runner.data_provider = fake_provider

        with self.assertRaises(BacktestDataCoverageError) as ctx:
            runner.run()

        message = str(ctx.exception)
        self.assertIn("vix_data does not fully cover", message)
        self.assertIn("produce zero trades", message)
        self.assertIn("VIX data missing; cannot determine VIX regime.", message)

    def test_run_warns_and_proceeds_when_vix_coverage_starts_after_requested_start(self):
        start_date = datetime(2026, 3, 24, 9, 15)
        end_date = datetime(2026, 3, 24, 15, 30)
        runner = self._make_runner(start_date, end_date)
        coverage = {
            "market_data_1m": make_coverage(start_date, end_date, rows=100),
            "vix_data": make_coverage(datetime(2026, 3, 24, 9, 26, 45), datetime(2026, 3, 24, 15, 0, 0), rows=100),
            "delta_cache": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
            "option_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
        }
        fake_provider = FakeCoverageProvider(start_date, end_date, coverage, [datetime(2026, 3, 24)])
        runner.data_provider = fake_provider
        runner.bot.run_once = Mock()
        runner.bot.get_unrealized_pnl = Mock(return_value=0.0)

        buffer = StringIO()
        with redirect_stdout(buffer):
            result = runner.run()

        output = buffer.getvalue()
        self.assertIn("status=WARNING", output)
        self.assertIn("VIX regime unavailable", output)
        self.assertEqual(result.num_trades, 0)
        runner.bot.run_once.assert_called_once()

    def test_run_proceeds_when_option_data_is_missing_but_delta_cache_covers_window(self):
        start_date = datetime(2026, 4, 2, 9, 15)
        end_date = datetime(2026, 4, 2, 15, 30)
        runner = self._make_runner(start_date, end_date)
        coverage = {
            "market_data_1m": make_coverage(start_date, end_date, rows=100),
            "vix_data": make_coverage(start_date, datetime(2026, 4, 2, 15, 0, 0), rows=100),
            "delta_cache": make_coverage(start_date, datetime(2026, 4, 2, 15, 1, 5), rows=100),
            "option_data": make_coverage(None, None, rows=0),
        }
        fake_provider = FakeCoverageProvider(start_date, end_date, coverage, [datetime(2026, 4, 2)])
        runner.data_provider = fake_provider
        runner.bot.run_once = Mock()
        runner.bot.get_unrealized_pnl = Mock(return_value=0.0)

        buffer = StringIO()
        with redirect_stdout(buffer):
            result = runner.run()

        output = buffer.getvalue()
        self.assertIn("option_data: rows=0", output)
        self.assertIn("required=optional, status=OPTIONAL", output)
        self.assertEqual(result.num_trades, 0)
        runner.bot.run_once.assert_called_once()

    def test_run_warns_and_proceeds_when_snapshot_tables_start_after_preferred_start(self):
        start_date = datetime(2026, 3, 24, 9, 15)
        end_date = datetime(2026, 3, 24, 15, 30)
        runner = self._make_runner(start_date, end_date)
        coverage = {
            "market_data_1m": make_coverage(start_date, end_date, rows=100),
            "vix_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 0, 0), rows=100),
            "delta_cache": make_coverage(datetime(2026, 3, 24, 9, 18, 14), datetime(2026, 3, 24, 15, 1, 5), rows=100),
            "option_data": make_coverage(datetime(2026, 3, 24, 9, 18, 14), datetime(2026, 3, 24, 15, 1, 5), rows=100),
        }
        fake_provider = FakeCoverageProvider(start_date, end_date, coverage, [datetime(2026, 3, 24)])
        runner.data_provider = fake_provider
        runner.bot.run_once = Mock()
        runner.bot.get_unrealized_pnl = Mock(return_value=0.0)

        buffer = StringIO()
        with redirect_stdout(buffer):
            result = runner.run()

        output = buffer.getvalue()
        self.assertIn("delta_cache: rows=100", output)
        self.assertIn("option_data: rows=100", output)
        self.assertIn("status=WARNING", output)
        self.assertIn("early entries may be skipped", output)
        self.assertEqual(result.num_trades, 0)
        runner.bot.run_once.assert_called_once()

    def test_run_prints_supporting_data_coverage_and_proceeds_when_all_required_tables_cover_window(self):
        start_date = datetime(2026, 4, 7, 9, 15)
        end_date = datetime(2026, 4, 7, 15, 30)
        runner = self._make_runner(start_date, end_date)
        coverage = {
            "market_data_1m": make_coverage(start_date, end_date, rows=100),
            "vix_data": make_coverage(start_date, datetime(2026, 4, 7, 15, 0, 0), rows=100),
            "delta_cache": make_coverage(start_date, datetime(2026, 4, 7, 15, 1, 5), rows=100),
            "option_data": make_coverage(start_date, datetime(2026, 4, 7, 15, 1, 5), rows=100),
        }
        fake_provider = FakeCoverageProvider(start_date, end_date, coverage, [datetime(2026, 4, 7)])
        runner.data_provider = fake_provider
        runner.bot.run_once = Mock()
        runner.bot.get_unrealized_pnl = Mock(return_value=0.0)

        buffer = StringIO()
        with redirect_stdout(buffer):
            result = runner.run()

        output = buffer.getvalue()
        self.assertIn("Supporting data coverage:", output)
        self.assertIn("vix_data", output)
        self.assertEqual(result.num_trades, 0)
        runner.bot.run_once.assert_called_once()

    def test_run_fails_fast_when_vix_starts_after_entry_window_ends(self):
        start_date = datetime(2026, 3, 24, 9, 15)
        end_date = datetime(2026, 3, 24, 15, 30)
        runner = self._make_runner(start_date, end_date)
        coverage = {
            "market_data_1m": make_coverage(start_date, end_date, rows=100),
            "vix_data": make_coverage(datetime(2026, 3, 24, 15, 5, 0), datetime(2026, 3, 24, 15, 10, 0), rows=100),
            "delta_cache": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
            "option_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
        }
        fake_provider = FakeCoverageProvider(start_date, end_date, coverage, [datetime(2026, 3, 24)])
        runner.data_provider = fake_provider

        with self.assertRaises(BacktestDataCoverageError) as ctx:
            runner.run()

        message = str(ctx.exception)
        self.assertIn("vix_data does not become available until 2026-03-24 15:05:00", message)

    def test_run_fails_fast_when_snapshot_table_starts_after_fill_window_ends(self):
        start_date = datetime(2026, 3, 24, 9, 15)
        end_date = datetime(2026, 3, 24, 15, 30)
        runner = self._make_runner(start_date, end_date)
        coverage = {
            "market_data_1m": make_coverage(start_date, end_date, rows=100),
            "vix_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 0, 0), rows=100),
            "delta_cache": make_coverage(datetime(2026, 3, 24, 15, 5, 0), datetime(2026, 3, 24, 15, 10, 0), rows=100),
            "option_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
        }
        fake_provider = FakeCoverageProvider(start_date, end_date, coverage, [datetime(2026, 3, 24)])
        runner.data_provider = fake_provider

        with self.assertRaises(BacktestDataCoverageError) as ctx:
            runner.run()

        message = str(ctx.exception)
        self.assertIn("delta_cache does not become available until 2026-03-24 15:05:00", message)

    def test_run_allows_vix_to_end_at_entry_cutoff(self):
        start_date = datetime(2026, 3, 24, 9, 15)
        end_date = datetime(2026, 3, 24, 15, 30)
        runner = self._make_runner(start_date, end_date)
        coverage = {
            "market_data_1m": make_coverage(start_date, end_date, rows=100),
            "vix_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 0, 0), rows=100),
            "delta_cache": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
            "option_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
        }
        fake_provider = FakeCoverageProvider(start_date, end_date, coverage, [datetime(2026, 3, 24)])
        runner.data_provider = fake_provider
        runner.bot.run_once = Mock()
        runner.bot.get_unrealized_pnl = Mock(return_value=0.0)

        result = runner.run()

        self.assertEqual(result.num_trades, 0)
        runner.bot.run_once.assert_called_once()

    def test_run_allows_snapshot_tables_to_end_after_fill_headroom(self):
        start_date = datetime(2026, 3, 24, 9, 15)
        end_date = datetime(2026, 3, 24, 15, 30)
        runner = self._make_runner(start_date, end_date)
        coverage = {
            "market_data_1m": make_coverage(start_date, end_date, rows=100),
            "vix_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 5, 0), rows=100),
            "delta_cache": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
            "option_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 1, 5), rows=100),
        }
        fake_provider = FakeCoverageProvider(start_date, end_date, coverage, [datetime(2026, 3, 24)])
        runner.data_provider = fake_provider
        runner.bot.run_once = Mock()
        runner.bot.get_unrealized_pnl = Mock(return_value=0.0)

        result = runner.run()

        self.assertEqual(result.num_trades, 0)
        runner.bot.run_once.assert_called_once()

    def test_run_truncates_to_latest_supported_tail_instead_of_failing_entire_window(self):
        start_date = datetime(2026, 3, 24, 9, 15)
        end_date = datetime(2026, 3, 24, 15, 30)
        runner = self._make_runner(start_date, end_date, step_interval=timedelta(minutes=30))
        coverage = {
            "market_data_1m": make_coverage(start_date, end_date, rows=100),
            "vix_data": make_coverage(start_date, datetime(2026, 3, 24, 15, 0, 0), rows=100),
            "delta_cache": make_coverage(start_date, datetime(2026, 3, 24, 14, 1, 5), rows=100),
            "option_data": make_coverage(start_date, datetime(2026, 3, 24, 14, 1, 5), rows=100),
        }
        fake_provider = FakeCoverageProvider(start_date, end_date, coverage, [datetime(2026, 3, 24)])
        runner.data_provider = fake_provider
        runner.bot.run_once = Mock()
        runner.bot.get_unrealized_pnl = Mock(return_value=0.0)

        buffer = StringIO()
        with redirect_stdout(buffer):
            result = runner.run()

        output = buffer.getvalue()
        self.assertIn("Truncating backtest end to the latest fully supported point-in-time", output)
        self.assertIn("delta_cache limits execution to 2026-03-24 14:00:00", output)
        self.assertNotIn("option_data limits execution", output)
        self.assertEqual(result.num_trades, 0)
        self.assertGreater(runner.bot.run_once.call_count, 0)
