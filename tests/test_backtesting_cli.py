import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.cli import export_results


class DummyTradeLog:
    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "trade_id": "t1",
                    "entry_time": "2026-03-20 09:20:00",
                    "exit_time": "2026-03-20 09:45:00",
                    "lot_id": "lot1",
                    "position_type": "A",
                    "entry_price": 100.0,
                    "exit_price": 120.0,
                    "pnl": 20.0,
                    "section": "section2",
                    "subcat": None,
                }
            ]
        )


class DummyRunner:
    def __init__(self) -> None:
        self.trade_log = DummyTradeLog()


def make_result() -> SimpleNamespace:
    return SimpleNamespace(
        total_pnl=20.0,
        num_trades=1,
        win_rate=1.0,
        avg_win=20.0,
        avg_loss=0.0,
        profit_factor=float("inf"),
        max_drawdown=0.0,
        max_drawdown_pct=0.0,
        sharpe_ratio=0.0,
        expectancy=20.0,
        equity_curve=[
            ("2026-03-20 09:20:00", 100000.0),
            ("2026-03-20 09:25:00", 100050.0),
            ("2026-03-20 09:30:00", 100020.0),
            ("2026-03-20 09:35:00", 100120.0),
        ],
    )


class BacktestingCliExportTests(unittest.TestCase):
    def test_export_results_writes_html_report(self):
        runner = DummyRunner()
        result = make_result()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = os.path.join(temp_dir, "report.html")
            saved = export_results(output, runner, result)

            self.assertTrue(os.path.exists(saved))
            with open(saved, "r", encoding="utf-8") as handle:
                html = handle.read()
            self.assertIn("Backtest Report", html)
            self.assertIn("Trade Log", html)
            self.assertIn("section2", html)
            self.assertIn("Equity Curve", html)
            self.assertIn("kpi-card", html)
            self.assertIn("trade-win", html)

    def test_export_results_writes_csv_when_requested(self):
        runner = DummyRunner()
        result = make_result()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = os.path.join(temp_dir, "report.csv")
            saved = export_results(output, runner, result)

            self.assertTrue(os.path.exists(saved))
            with open(saved, "r", encoding="utf-8") as handle:
                csv_text = handle.read()
            self.assertIn("trade_id", csv_text)
            self.assertIn("section2", csv_text)

    def test_export_results_does_not_overwrite_existing_html_report(self):
        runner = DummyRunner()
        result = make_result()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = os.path.join(temp_dir, "report.html")
            with open(output, "w", encoding="utf-8") as existing:
                existing.write("existing-report")

            saved = export_results(output, runner, result)

            self.assertNotEqual(os.path.abspath(saved), os.path.abspath(output))
            self.assertTrue(os.path.exists(saved))
            with open(output, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "existing-report")

    def test_export_results_does_not_overwrite_existing_csv_report(self):
        runner = DummyRunner()
        result = make_result()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = os.path.join(temp_dir, "report.csv")
            with open(output, "w", encoding="utf-8") as existing:
                existing.write("existing-report")

            saved = export_results(output, runner, result)

            self.assertNotEqual(os.path.abspath(saved), os.path.abspath(output))
            self.assertTrue(os.path.exists(saved))
            with open(output, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "existing-report")


if __name__ == "__main__":
    unittest.main()
