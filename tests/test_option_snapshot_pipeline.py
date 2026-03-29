import logging
import os
import sqlite3
import sys
import tempfile
import types
import unittest
import importlib
from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.backtest_strategy import BacktestableStrategy
from core.strat_nifty import NiftyOptionsStrategy
from utils.db_func import (
    build_nifty_strategy_required_contracts,
    calculate_and_store_high_accuracy_delta,
    fetch_cached_options_data,
    fetch_latest_delta_data,
    fetch_latest_option_snapshot,
    summarize_option_snapshot_coverage,
)


def make_df(index: pd.DatetimeIndex, closes, highs=None, lows=None, **extra_columns) -> pd.DataFrame:
    closes = list(closes)
    highs = list(highs) if highs is not None else [c + 1.0 for c in closes]
    lows = list(lows) if lows is not None else [c - 1.0 for c in closes]

    data = {
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
    }

    for name, values in extra_columns.items():
        if isinstance(values, (list, tuple, pd.Series)):
            data[name] = list(values)
        else:
            data[name] = [values] * len(index)

    return pd.DataFrame(data, index=index)


class _FakeKite:
    def __init__(self, spot_price: float):
        self.spot_price = spot_price

    def quote(self, symbol: str):
        return {symbol: {"last_price": self.spot_price}}


class OptionSnapshotPipelineTests(unittest.TestCase):
    SYMBOL = "NIFTY50"
    SPOT_PRICE = 22930.15

    @staticmethod
    def _logger_tuple():
        logger = logging.getLogger("test.option.snapshot")
        return (logger, logger, logger, logger, logger, logger)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "test_options.db")
        self._create_tables()
        self.latest_ts = datetime.now().replace(microsecond=0)
        self._insert_option_snapshots()

    def _create_tables(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE option_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strike_price INTEGER NOT NULL,
                option_type TEXT NOT NULL,
                ltp REAL,
                iv REAL,
                expiry_date TEXT NOT NULL,
                spot_price REAL,
                tradingsymbol TEXT NOT NULL,
                open_interest INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE delta_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                strike_price INTEGER NOT NULL,
                option_type TEXT NOT NULL,
                delta REAL,
                expiry_date TEXT NOT NULL,
                spot_price REAL,
                symbol TEXT NOT NULL,
                ltp REAL,
                tradingsymbol TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _insert_option_snapshots(self):
        conn = sqlite3.connect(self.db_path)
        latest_ts_str = self.latest_ts.strftime("%Y-%m-%d %H:%M:%S")
        older_ts_str = (self.latest_ts - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        expiries = ["2099-03-30", "2099-04-30"]

        latest_rows = []
        for strike in range(22450, 26550, 50):
            for option_type in ("CE", "PE"):
                for expiry in expiries:
                    latest_rows.append((
                        latest_ts_str,
                        self.SYMBOL,
                        strike,
                        option_type,
                        float(max(1, abs(self.SPOT_PRICE - strike) / 10.0 + (1 if option_type == "CE" else 2))),
                        0.22,
                        expiry,
                        self.SPOT_PRICE,
                        f"NIFTY{expiry.replace('-', '')}{strike}{option_type}",
                        1000 + strike,
                    ))

        older_rows = [
            (
                older_ts_str,
                self.SYMBOL,
                24000,
                "CE",
                100.0,
                0.2,
                "2099-03-30",
                self.SPOT_PRICE,
                "NIFTY_OLD_24000_CE",
                500,
            ),
            (
                older_ts_str,
                self.SYMBOL,
                24000,
                "PE",
                100.0,
                0.2,
                "2099-03-30",
                self.SPOT_PRICE,
                "NIFTY_OLD_24000_PE",
                500,
            ),
        ]

        conn.executemany("""
            INSERT INTO option_data (
                timestamp, symbol, strike_price, option_type,
                ltp, iv, expiry_date, spot_price, tradingsymbol, open_interest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, latest_rows + older_rows)
        conn.commit()
        conn.close()
        self.latest_snapshot_size = len(latest_rows)

    def _build_delta_cache(self):
        fake_module = types.ModuleType("broker.zerodha_client")
        fake_module.kite_from_saved_token = lambda: _FakeKite(self.SPOT_PRICE)

        with patch.dict(sys.modules, {"broker.zerodha_client": fake_module}):
            result = calculate_and_store_high_accuracy_delta(self.SYMBOL, db_path=self.db_path)

        self.assertIsNotNone(result)
        return result

    def _strategy(self) -> NiftyOptionsStrategy:
        state_file = os.path.join(self.temp_dir.name, "nifty_strategy_state.json")
        with patch("core.strat_nifty.setup_paper_trading_logger", return_value=self._logger_tuple()):
            return NiftyOptionsStrategy(symbol=self.SYMBOL, state_file=state_file)

    def _backtest_strategy(self) -> BacktestableStrategy:
        provider = unittest.mock.Mock()
        with patch("core.strat_nifty.setup_paper_trading_logger", return_value=self._logger_tuple()):
            return BacktestableStrategy(
                data_provider=provider,
                current_time_fn=lambda: datetime(2026, 3, 10, 14, 0, 0),
                symbol=self.SYMBOL,
                state_file=None,
            )

    def _base_frames(self) -> dict[str, pd.DataFrame]:
        index_1m = pd.date_range("2026-03-10 09:15:00", periods=10, freq="1min")
        df_1m = make_df(
            index_1m,
            closes=[self.SPOT_PRICE] * 10,
            highs=[self.SPOT_PRICE + 2.0] * 10,
            lows=[self.SPOT_PRICE - 2.0] * 10,
            sma_20=self.SPOT_PRICE + 40.0,
            sma_50=self.SPOT_PRICE + 80.0,
            sma_200=self.SPOT_PRICE + 120.0,
            sma_5_low=self.SPOT_PRICE + 10.0,
        )

        index_5m = pd.date_range("2026-03-10 09:15:00", periods=15, freq="5min")
        df_5m = make_df(
            index_5m,
            closes=[40.0 + i for i in range(15)],
            highs=[42.0 + i for i in range(15)],
            lows=[39.0 + i for i in range(15)],
            sma_20=70.0,
            sma_50=80.0,
            sma_200=90.0,
            donchian_mid=68.0,
        )

        index_15m = pd.date_range("2026-03-10 09:15:00", periods=2, freq="15min")
        df_15m = make_df(
            index_15m,
            closes=[80.0, 80.0],
            highs=[81.0, 81.0],
            lows=[79.0, 79.0],
            sma_20=90.0,
            sma_50=100.0,
            sma_200=110.0,
            donchian_mid=88.0,
        )

        index_1h = pd.date_range("2026-03-10 09:15:00", periods=2, freq="1h")
        df_1h = make_df(
            index_1h,
            closes=[50.0, 50.0],
            highs=[55.0, 55.0],
            lows=[49.0, 49.0],
            sma_20=100.0,
            sma_50=110.0,
            sma_200=120.0,
            donchian_mid=95.0,
        )

        return {"1m": df_1m, "5m": df_5m, "15m": df_15m, "1h": df_1h}

    def test_latest_option_snapshot_returns_full_latest_timestamp(self):
        legacy_rows = fetch_cached_options_data(self.SYMBOL, db_path=self.db_path)
        latest_rows = fetch_latest_option_snapshot(self.SYMBOL, db_path=self.db_path)

        self.assertEqual(len(legacy_rows), 300)
        self.assertEqual(len(latest_rows), self.latest_snapshot_size)
        self.assertIn(22900, {row["strike_price"] for row in latest_rows})
        self.assertEqual(
            {"strike_price", "option_type", "ltp", "iv", "expiry_date", "spot_price", "tradingsymbol", "open_interest"},
            set(latest_rows[0].keys()),
        )

    def test_option_snapshot_coverage_helper_flags_missing_required_contracts(self):
        latest_rows = fetch_latest_option_snapshot(self.SYMBOL, db_path=self.db_path)
        required = build_nifty_strategy_required_contracts(self.SPOT_PRICE, position_type="A")
        complete = summarize_option_snapshot_coverage(latest_rows, required_contracts=required)

        self.assertTrue(complete["complete"])
        self.assertEqual(complete["missing_required_contracts"], [])

        reduced_rows = [
            row for row in latest_rows
            if not (
                row["strike_price"] == 23200
                and row["option_type"] in {"CE", "PE"}
            )
        ]
        incomplete = summarize_option_snapshot_coverage(reduced_rows, required_contracts=required)

        self.assertFalse(incomplete["complete"])
        self.assertIn({"option_type": "PE", "strike_price": 23200}, incomplete["missing_required_contracts"])
        self.assertIn({"option_type": "CE", "strike_price": 23200}, incomplete["missing_required_contracts"])

    def test_delta_builder_uses_full_snapshot_and_keeps_expected_strike(self):
        self._build_delta_cache()

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT strike_price, option_type
            FROM delta_cache
            WHERE symbol = ? AND strike_price IN (22900, 23000)
            ORDER BY strike_price, option_type
        """, (self.SYMBOL,)).fetchall()
        conn.close()

        self.assertIn((22900, "PE"), rows)
        self.assertIn((23000, "PE"), rows)

    def test_fetch_latest_delta_data_preserves_shared_consumer_contract(self):
        self._build_delta_cache()

        latest_delta = fetch_latest_delta_data(self.SYMBOL, db_path=self.db_path)

        self.assertTrue(latest_delta)
        self.assertTrue({"strike_price", "option_type", "delta", "ltp", "tradingsymbol", "expiry"}.issubset(latest_delta[0].keys()))
        self.assertIn(22900, {row["strike_price"] for row in latest_delta})

    def test_delta_builder_returns_coverage_metadata(self):
        result = self._build_delta_cache()

        self.assertIn("coverage", result)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["missing_required_contracts"], [])

    def test_delta_builder_keeps_shared_behavior_when_latest_snapshot_is_incomplete(self):
        latest_rows = fetch_latest_option_snapshot(self.SYMBOL, db_path=self.db_path)

        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            DELETE FROM option_data
            WHERE symbol = ? AND timestamp = ? AND strike_price = ? AND option_type IN ('CE', 'PE')
        """, (self.SYMBOL, self.latest_ts.strftime("%Y-%m-%d %H:%M:%S"), 23200))
        conn.commit()
        conn.close()

        refreshed_rows = []
        for row in latest_rows:
            refreshed_rows.append({
                "strikePrice": row["strike_price"],
                "optionType": row["option_type"],
                "expiry": row["expiry_date"],
                "tradingsymbol": row["tradingsymbol"],
                "lastPrice": row["ltp"],
                "openInterest": row["open_interest"],
            })

        fake_module = types.ModuleType("broker.zerodha_client")
        fake_module.kite_from_saved_token = lambda: _FakeKite(self.SPOT_PRICE)

        with patch.dict(sys.modules, {"broker.zerodha_client": fake_module}), \
             patch("utils.vix_fetcher.fetch_live_option_chain", return_value=refreshed_rows) as fetch_chain:
            result = calculate_and_store_high_accuracy_delta(self.SYMBOL, db_path=self.db_path)

        self.assertIsNotNone(result)
        self.assertFalse(fetch_chain.called)
        self.assertFalse(result["coverage"]["complete"])

    def test_nifty_strategy_type_b_entry_succeeds_with_generated_delta_cache(self):
        self._build_delta_cache()
        strategy = self._strategy()
        frames = self._base_frames()

        def get_df(interval: str, limit: int = 300) -> pd.DataFrame:
            return frames[interval].copy()

        with patch.object(strategy, "_now", return_value=datetime(2026, 3, 10, 14, 0, 0)), \
             patch.object(strategy, "_get_df", side_effect=get_df), \
             patch.object(strategy, "_detect_touches_and_latch", return_value=[]), \
             patch.object(strategy, "_vix_regime", return_value="B"), \
             patch("core.strat_nifty.calculate_and_store_high_accuracy_delta", return_value=None), \
             patch("core.strat_nifty.fetch_latest_delta_data", side_effect=lambda symbol: fetch_latest_delta_data(symbol, db_path=self.db_path)):
            intent = strategy.evaluate_for_entry()

        self.assertIsNotNone(intent)
        self.assertEqual(intent["position_type"], "B")
        self.assertEqual(intent["strikes"]["pe_strike"], 22900)
        self.assertEqual(len(intent["legs"]), 2)
        self.assertEqual({leg["strike_price"] for leg in intent["legs"]}, {22900, 23000})

    def test_backtest_strategy_aborts_with_single_coverage_warning_when_exact_contract_missing(self):
        self._build_delta_cache()
        strategy = self._backtest_strategy()
        frames = self._base_frames()
        strategy.logger.warning = unittest.mock.Mock()

        latest_delta = [
            row for row in fetch_latest_delta_data(self.SYMBOL, db_path=self.db_path)
            if not (row["strike_price"] == 23000 and row["option_type"] == "PE")
        ]

        def get_df(interval: str, limit: int = 300) -> pd.DataFrame:
            return frames[interval].copy()

        with patch.object(strategy, "_now", return_value=datetime(2026, 3, 10, 14, 0, 0)), \
             patch.object(strategy, "_get_df", side_effect=get_df), \
             patch.object(strategy, "_detect_touches_and_latch", return_value=[]), \
             patch.object(strategy, "_vix_regime", return_value="B"), \
             patch.object(strategy, "_fetch_options_data", return_value=latest_delta):
            intent = strategy.evaluate_for_entry()

        self.assertIsNone(intent)
        self.assertTrue(strategy.logger.warning.called)
        warning_text = " ".join(str(arg) for arg in strategy.logger.warning.call_args[0])
        self.assertIn("Option snapshot incomplete", warning_text)
        self.assertNotIn("Option not found in cache", warning_text)

    def test_parallel_option_strategy_refresh_paths_still_accept_latest_delta_shape(self):
        self._build_delta_cache()
        latest_delta = fetch_latest_delta_data(self.SYMBOL, db_path=self.db_path)

        with patch("utils.utility.setup_paper_trading_logger", return_value=self._logger_tuple()):
            paper_trades = importlib.import_module("core.paper_trades")
            paper_trades = importlib.reload(paper_trades)
            paper_trder_sma = importlib.import_module("core.paper_trder_sma")
            paper_trder_sma = importlib.reload(paper_trder_sma)

        with patch.object(paper_trades, "setup_paper_trading_logger", return_value=self._logger_tuple()), \
             patch.object(paper_trades.os.path, "exists", return_value=False), \
             patch.object(paper_trades, "fetch_latest_delta_data", return_value=latest_delta):
            donchian = paper_trades.PaperTraderDonchian(self.SYMBOL)
            self.assertTrue(donchian.refresh_all_data())

        with patch.object(paper_trder_sma, "setup_paper_trading_logger", return_value=self._logger_tuple()), \
             patch.object(paper_trder_sma.os.path, "exists", return_value=False), \
             patch.object(paper_trder_sma, "fetch_latest_delta_data", return_value=latest_delta):
            sma = paper_trder_sma.PaperTraderSMA(self.SYMBOL)
            self.assertTrue(sma.refresh_all_data())


if __name__ == "__main__":
    unittest.main()
