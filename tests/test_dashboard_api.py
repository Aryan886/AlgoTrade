import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import TradingEngine
from server.api import create_app


class DashboardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.temp_dir.name, "trading_bot.db")
        shutil.copy2("db/trading_bot.db", self.db_path)
        self.app = create_app(TradingEngine(), db_path=self.db_path)
        self.client_ctx = TestClient(self.app)
        self.client = self.client_ctx.__enter__()

    def tearDown(self) -> None:
        self.client_ctx.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_demo_login_allows_only_fixed_credentials(self):
        ok = self.client.post(
            "/api/session/login",
            json={"email": "demo@algotrade.local", "password": "demo123"},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["ok"])
        self.assertEqual(ok.json()["user"]["role"], "Presenter")

        bad = self.client.post(
            "/api/session/login",
            json={"email": "wrong@example.com", "password": "nope"},
        )
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(bad.json()["ok"])

    def test_strategy_start_stop_persists_across_requests(self):
        started = self.client.post("/api/strategies/donchian-options/start")
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["status"], "RUNNING")

        listing = self.client.get("/api/strategies")
        self.assertEqual(listing.status_code, 200)
        matching = next(item for item in listing.json() if item["strategyId"] == "donchian-options")
        self.assertEqual(matching["status"], "RUNNING")

        stopped = self.client.post("/api/strategies/donchian-options/stop")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json()["status"], "STOPPED")

    def test_dashboard_overview_returns_required_sections(self):
        response = self.client.get("/api/dashboard/overview")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("engineStatus", payload)
        self.assertIn("marketOverview", payload)
        self.assertIn("pnlSummary", payload)
        self.assertIn("recentTrades", payload)
        self.assertIn("strategyStatuses", payload)
        self.assertEqual(len(payload["strategyStatuses"]), 3)
        self.assertGreater(len(payload["marketOverview"]["intradayChart"]), 0)

    def test_reports_aggregate_daily_and_monthly_pnl(self):
        daily = self.client.get("/api/reports/pnl", params={"groupBy": "daily"})
        monthly = self.client.get("/api/reports/pnl", params={"groupBy": "monthly"})

        self.assertEqual(daily.status_code, 200)
        self.assertEqual(monthly.status_code, 200)
        self.assertGreater(len(daily.json()), 0)
        self.assertGreater(len(monthly.json()), 0)
        self.assertIn("tradeCount", daily.json()[0])
        self.assertIn("winRate", monthly.json()[0])

    def test_backtests_reject_non_nifty_and_invalid_date_order(self):
        wrong_strategy = self.client.post(
            "/api/backtests/run",
            json={"strategyId": "sma-spread", "startDate": "2026-03-26", "endDate": "2026-03-27"},
        )
        self.assertEqual(wrong_strategy.status_code, 400)

        invalid_date_order = self.client.post(
            "/api/backtests/run",
            json={"strategyId": "nifty-options", "startDate": "2026-03-30", "endDate": "2026-03-20"},
        )
        self.assertEqual(invalid_date_order.status_code, 400)

    def test_backtests_allow_longer_supported_windows(self):
        longer_window = self.client.post(
            "/api/backtests/run",
            json={"strategyId": "nifty-options", "startDate": "2026-03-20", "endDate": "2026-03-27"},
        )
        self.assertEqual(longer_window.status_code, 200, longer_window.text)
        payload = longer_window.json()
        self.assertIn("summary", payload)
        self.assertEqual(payload["meta"]["startDate"], "2026-03-20")
        self.assertEqual(payload["meta"]["endDate"], "2026-03-27")

    def test_backtest_run_and_export_html(self):
        run = self.client.post(
            "/api/backtests/run",
            json={"strategyId": "nifty-options", "startDate": "2026-03-26", "endDate": "2026-03-27"},
        )
        self.assertEqual(run.status_code, 200, run.text)
        payload = run.json()
        self.assertIn("summary", payload)
        self.assertIn("kpis", payload)
        self.assertIn("equityCurve", payload)
        self.assertIn("slAnalysis", payload)
        self.assertIn("dataQuality", payload)
        self.assertIn("trades", payload)

        export = self.client.post(
            "/api/backtests/export-html",
            json={"strategyId": "nifty-options", "useLatestRun": True},
        )
        self.assertEqual(export.status_code, 200, export.text)
        export_payload = export.json()
        self.assertTrue(export_payload["ok"])
        output_path = Path(export_payload["outputPath"])
        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.suffix.lower(), ".html")
        output_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
