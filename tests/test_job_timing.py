import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schedule

from utils.market_data_automation import MarketDataAutomation

LOGGER_NAME = "utils.market_data_automation"


def _build_automation():
    with patch("utils.market_data_automation.PaperTraderDonchian", return_value=Mock()), \
         patch("utils.market_data_automation.PaperTraderSMA", return_value=Mock()), \
         patch("utils.market_data_automation.EquityPaperTrader", return_value=Mock()), \
         patch("utils.market_data_automation.NiftyPaperBot", return_value=Mock()), \
         patch("utils.market_data_automation.OIExpiryPaperBot", return_value=Mock()):
        return MarketDataAutomation()


def _stub_every_job(automation):
    for name in dir(automation):
        if name.startswith("fetch_") or name.endswith("_paper_trading_cycle") or name in (
            "run_vix_job",
            "run_delta_job",
        ):
            setattr(automation, name, Mock())


def _make_every_job_due():
    for job in schedule.get_jobs():
        job.next_run = datetime.now() - timedelta(seconds=1)


class JobTimingTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(schedule.clear)

    def test_each_scheduled_job_logs_name_start_duration_and_success(self):
        automation = _build_automation()
        _stub_every_job(automation)
        automation.setup_schedule()

        with self.assertLogs(LOGGER_NAME, level="INFO") as logs:
            for job in schedule.get_jobs():
                job.job_func()

        job_lines = [line for line in logs.output if "JOB " in line]
        for name in (
            "fetch_1m_data",
            "fetch_5m_data",
            "fetch_15m_data",
            "fetch_1h_data",
            "run_vix_job",
            "run_delta_job",
            "fetch_open_interest_data",
            "run_paper_trading_cycle",
            "run_sma_paper_trading_cycle",
            "run_nifty_paper_trading_cycle",
        ):
            matching = [line for line in job_lines if f"name={name} " in line]
            self.assertEqual(len(matching), 1, f"expected one timing line for {name}")
            self.assertRegex(
                matching[0],
                r"start=\d{2}:\d{2}:\d{2} duration=\d+\.\d{3}s status=ok",
            )

    def test_failing_job_is_logged_as_failed_and_does_not_stop_later_jobs(self):
        automation = _build_automation()
        _stub_every_job(automation)
        automation.fetch_1m_data.side_effect = RuntimeError("kite timeout")
        automation.setup_schedule()

        with self.assertLogs(LOGGER_NAME, level="INFO") as logs:
            for job in schedule.get_jobs():
                job.job_func()

        failed = [line for line in logs.output if "name=fetch_1m_data " in line]
        self.assertEqual(len(failed), 1)
        self.assertIn("status=failed", failed[0])
        self.assertIn("kite timeout", failed[0])
        automation.run_nifty_paper_trading_cycle.assert_called_once()

    def test_cycle_logs_one_summary_with_lateness_and_job_count(self):
        automation = _build_automation()
        _stub_every_job(automation)
        automation.fetch_5m_data.side_effect = RuntimeError("boom")
        automation.setup_schedule()
        _make_every_job_due()

        with self.assertLogs(LOGGER_NAME, level="INFO") as logs:
            automation.run_cycle(started_at=datetime(2026, 11, 11, 10, 5, 7, 500000))

        summaries = [line for line in logs.output if "CYCLE " in line]
        self.assertEqual(len(summaries), 1)
        self.assertRegex(
            summaries[0],
            r"start=10:05:07 late=7\.5s duration=\d+\.\d{3}s jobs=10 failed=1",
        )

    def test_cycle_with_no_due_jobs_logs_no_summary(self):
        automation = _build_automation()
        _stub_every_job(automation)
        automation.setup_schedule()

        with self.assertNoLogs(LOGGER_NAME, level="INFO"):
            automation.run_cycle()


if __name__ == "__main__":
    unittest.main()
