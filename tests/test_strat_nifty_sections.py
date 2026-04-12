import os
import sys
import tempfile
import unittest
import logging
from datetime import datetime
from unittest.mock import patch

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.strat_nifty import NiftyOptionsStrategy


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


def make_options_chain() -> list[dict]:
    return [
        {"strike_price": 100, "option_type": "CE", "ltp": 10.0, "tradingsymbol": "NIFTY_CE_100", "expiry": "2026-03-26"},
        {"strike_price": 300, "option_type": "PE", "ltp": 20.0, "tradingsymbol": "NIFTY_PE_300", "expiry": "2026-03-26"},
        {"strike_price": 300, "option_type": "CE", "ltp": 8.0, "tradingsymbol": "NIFTY_CE_300", "expiry": "2026-03-26"},
        {"strike_price": 200, "option_type": "PE", "ltp": 12.0, "tradingsymbol": "NIFTY_PE_200", "expiry": "2026-03-26"},
    ]


class NiftyStrategySectionTests(unittest.TestCase):
    @staticmethod
    def _logger_tuple():
        logger = logging.getLogger("test.nifty.strategy")
        return (logger, logger, logger, logger, logger, logger)

    def _strategy(self) -> NiftyOptionsStrategy:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        state_file = os.path.join(temp_dir.name, "nifty_strategy_state.json")
        with patch("core.strat_nifty.setup_paper_trading_logger", return_value=self._logger_tuple()):
            return NiftyOptionsStrategy(symbol="NIFTY50", state_file=state_file)

    def _base_frames(self) -> dict[str, pd.DataFrame]:
        index_1m = pd.date_range("2026-03-10 09:15:00", periods=10, freq="1min")
        closes_1m = [100.0] * 8 + [115.0, 100.0]
        highs_1m = [101.0] * 8 + [116.0, 101.0]
        lows_1m = [99.0] * 8 + [114.0, 99.0]
        df_1m = make_df(
            index_1m,
            closes=closes_1m,
            highs=highs_1m,
            lows=lows_1m,
            sma_20=110.0,
            sma_50=120.0,
            sma_200=130.0,
            sma_5_low=105.0,
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
            sma_20=130.0,
            sma_50=140.0,
            sma_200=150.0,
            donchian_mid=125.0,
        )

        return {"1m": df_1m, "5m": df_5m, "15m": df_15m, "1h": df_1h}

    def test_main_category_a_passes_when_latest_1m_close_is_below_all_1h_indicators(self):
        strategy = self._strategy()
        frames = self._base_frames()

        self.assertTrue(strategy.main_category_a_active(frames["1h"], frames["1m"]))

    def test_main_category_a_uses_latest_1m_close_instead_of_1h_close(self):
        strategy = self._strategy()
        frames = self._base_frames()
        latest_index = frames["1m"].index[-1]
        frames["1m"].loc[latest_index, ["open", "high", "low", "close"]] = [155.0, 156.0, 154.0, 155.0]

        self.assertFalse(strategy.main_category_a_active(frames["1h"], frames["1m"]))

    def test_section1_latches_higher_timeframe_indicator_when_1m_close_crosses_it(self):
        strategy = self._strategy()
        frames = self._base_frames()
        latest_1m_index = frames["1m"].index[-1]
        frames["1m"].loc[latest_1m_index, ["open", "high", "low", "close"]] = [91.0, 92.0, 90.0, 91.0]

        frames["5m"]["sma_20"] = 200.0
        frames["5m"]["donchian_mid"] = 205.0
        frames["5m"]["sma_50"] = 210.0
        frames["5m"]["sma_200"] = 220.0

        frames["15m"]["sma_20"] = 90.0
        frames["15m"]["donchian_mid"] = 205.0
        frames["15m"]["sma_50"] = 210.0
        frames["15m"]["sma_200"] = 220.0

        frames["1h"]["sma_20"] = 300.0
        frames["1h"]["donchian_mid"] = 305.0
        frames["1h"]["sma_50"] = 310.0
        frames["1h"]["sma_200"] = 320.0

        newly_latched = strategy._detect_touches_and_latch(frames["1m"], frames)

        self.assertEqual(newly_latched, ["h"])
        self.assertTrue(strategy.state["section1_flags"]["h"])
        self.assertEqual(
            strategy.state["section1_last_touch"]["h"],
            latest_1m_index.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def test_section1_does_not_latch_from_higher_timeframe_high_without_1m_close_touch(self):
        strategy = self._strategy()
        frames = self._base_frames()
        latest_1m_index = frames["1m"].index[-1]
        frames["1m"].loc[latest_1m_index, ["open", "high", "low", "close"]] = [89.0, 90.0, 88.0, 89.0]

        frames["5m"]["sma_20"] = 200.0
        frames["5m"]["donchian_mid"] = 205.0
        frames["5m"]["sma_50"] = 210.0
        frames["5m"]["sma_200"] = 220.0

        last_15m_index = frames["15m"].index[-1]
        frames["15m"].loc[last_15m_index, "high"] = 95.0
        frames["15m"]["sma_20"] = 90.0
        frames["15m"]["donchian_mid"] = 205.0
        frames["15m"]["sma_50"] = 210.0
        frames["15m"]["sma_200"] = 220.0

        frames["1h"]["sma_20"] = 300.0
        frames["1h"]["donchian_mid"] = 305.0
        frames["1h"]["sma_50"] = 310.0
        frames["1h"]["sma_200"] = 320.0

        newly_latched = strategy._detect_touches_and_latch(frames["1m"], frames)

        self.assertNotIn("h", newly_latched)
        self.assertFalse(strategy.state["section1_flags"]["h"])

    def test_section3_break_latches_from_1m_close_against_5m_indicator(self):
        strategy = self._strategy()
        frames = self._base_frames()
        latest_1m_index = frames["1m"].index[-1]
        frames["1m"].loc[latest_1m_index, ["open", "high", "low", "close"]] = [69.0, 70.0, 68.0, 69.0]
        latest_5m_index = frames["5m"].index[-1]
        frames["5m"].loc[latest_5m_index, ["open", "high", "low", "close"]] = [75.0, 76.0, 74.0, 75.0]
        frames["5m"]["sma_20"] = 70.0
        frames["5m"]["donchian_mid"] = 68.0

        triggered = strategy._update_section3_break_latch(frames["1m"], frames["5m"])

        self.assertTrue(triggered)
        self.assertTrue(strategy.state["section3"]["break_latched"])
        self.assertEqual(strategy.state["section3"]["break_reason"], "sma20")
        self.assertEqual(
            strategy.state["section3"]["break_time"],
            latest_1m_index.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def test_section3_retest_passes_when_1m_close_recovers_without_5m_high_touch(self):
        strategy = self._strategy()
        frames = self._base_frames()
        latest_1m_index = frames["1m"].index[-1]
        previous_1m_index = frames["1m"].index[-2]
        frames["1m"].loc[latest_1m_index, ["open", "high", "low", "close"]] = [71.0, 72.0, 70.0, 71.0]
        latest_5m_index = frames["5m"].index[-1]
        frames["5m"].loc[latest_5m_index, ["open", "high", "low", "close"]] = [68.0, 69.0, 67.0, 68.0]
        frames["5m"]["sma_20"] = 70.0
        frames["5m"]["donchian_mid"] = 72.0
        strategy.state["section3"] = {
            "break_latched": True,
            "break_reason": "sma20",
            "break_time": previous_1m_index.strftime("%Y-%m-%d %H:%M:%S"),
        }

        with patch.object(strategy, "_now", return_value=latest_1m_index.to_pydatetime()):
            retest_ok = strategy._section3_retest_ok(frames["1m"], frames["5m"])

        self.assertTrue(retest_ok)

    def test_section3_retest_does_not_use_5m_high_without_1m_close_recovery(self):
        strategy = self._strategy()
        frames = self._base_frames()
        latest_1m_index = frames["1m"].index[-1]
        previous_1m_index = frames["1m"].index[-2]
        frames["1m"].loc[latest_1m_index, ["open", "high", "low", "close"]] = [69.0, 70.0, 68.0, 69.0]
        latest_5m_index = frames["5m"].index[-1]
        frames["5m"].loc[latest_5m_index, ["open", "high", "low", "close"]] = [70.0, 72.0, 69.0, 70.0]
        frames["5m"]["sma_20"] = 70.0
        frames["5m"]["donchian_mid"] = 71.0
        strategy.state["section3"] = {
            "break_latched": True,
            "break_reason": "sma20",
            "break_time": previous_1m_index.strftime("%Y-%m-%d %H:%M:%S"),
        }

        with patch.object(strategy, "_now", return_value=latest_1m_index.to_pydatetime()):
            retest_ok = strategy._section3_retest_ok(frames["1m"], frames["5m"])

        self.assertFalse(retest_ok)

    def test_section2_entry_uses_closed_5m_rsi_filter(self):
        strategy = self._strategy()
        frames = self._base_frames()

        def get_df(interval: str, limit: int = 300) -> pd.DataFrame:
            return frames[interval].copy()

        with patch.object(strategy, "_now", return_value=datetime(2026, 3, 10, 14, 0, 0)), \
             patch.object(strategy, "_get_df", side_effect=get_df), \
             patch.object(strategy, "_detect_touches_and_latch", return_value=[]), \
             patch.object(strategy, "_vix_regime", return_value="A"), \
             patch("core.strat_nifty.calculate_and_store_high_accuracy_delta", return_value=None), \
             patch("core.strat_nifty.fetch_latest_delta_data", return_value=make_options_chain()):
            intent = strategy.evaluate_for_entry()

        self.assertIsNotNone(intent)
        self.assertEqual(intent["reason"]["section"], "section2")
        self.assertEqual(intent["position_type"], "A")
        self.assertGreater(intent["reason"]["rsi_5m"], 40.0)
        self.assertEqual(len(intent["legs"]), 3)

    def test_section2_skips_trade_when_previous_1m_candle_is_not_above_both_levels(self):
        strategy = self._strategy()
        frames = self._base_frames()
        previous_index = frames["1m"].index[-2]
        frames["1m"].loc[previous_index, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]

        def get_df(interval: str, limit: int = 300) -> pd.DataFrame:
            return frames[interval].copy()

        with patch.object(strategy, "_now", return_value=datetime(2026, 3, 10, 14, 0, 0)), \
             patch.object(strategy, "_get_df", side_effect=get_df), \
             patch.object(strategy, "_detect_touches_and_latch", return_value=[]), \
             patch.object(strategy, "_vix_regime", return_value="A"), \
             patch("core.strat_nifty.calculate_and_store_high_accuracy_delta", return_value=None), \
             patch("core.strat_nifty.fetch_latest_delta_data", return_value=make_options_chain()):
            intent = strategy.evaluate_for_entry()

        self.assertIsNone(intent)

    def test_section2_skips_trade_when_previous_1m_candle_is_above_only_one_level(self):
        strategy = self._strategy()
        frames = self._base_frames()
        previous_index = frames["1m"].index[-2]
        frames["1m"].loc[previous_index, ["open", "high", "low", "close"]] = [108.0, 109.0, 107.0, 108.0]

        def get_df(interval: str, limit: int = 300) -> pd.DataFrame:
            return frames[interval].copy()

        with patch.object(strategy, "_now", return_value=datetime(2026, 3, 10, 14, 0, 0)), \
             patch.object(strategy, "_get_df", side_effect=get_df), \
             patch.object(strategy, "_detect_touches_and_latch", return_value=[]), \
             patch.object(strategy, "_vix_regime", return_value="A"), \
             patch("core.strat_nifty.calculate_and_store_high_accuracy_delta", return_value=None), \
             patch("core.strat_nifty.fetch_latest_delta_data", return_value=make_options_chain()):
            intent = strategy.evaluate_for_entry()

        self.assertIsNone(intent)

    def test_section1_has_priority_over_section2(self):
        strategy = self._strategy()
        frames = self._base_frames()
        strategy.state["section1_flags"]["a"] = True

        def get_df(interval: str, limit: int = 300) -> pd.DataFrame:
            return frames[interval].copy()

        with patch.object(strategy, "_now", return_value=datetime(2026, 3, 10, 14, 0, 0)), \
             patch.object(strategy, "_get_df", side_effect=get_df), \
             patch.object(strategy, "_detect_touches_and_latch", return_value=[]), \
             patch.object(strategy, "_vix_regime", return_value="A"), \
             patch("core.strat_nifty.calculate_and_store_high_accuracy_delta", return_value=None), \
             patch("core.strat_nifty.fetch_latest_delta_data", return_value=make_options_chain()):
            intent = strategy.evaluate_for_entry()

        self.assertIsNotNone(intent)
        self.assertEqual(intent["reason"]["section"], "section1")
        self.assertEqual(intent["reason"]["subcat"], "a")

    def test_section2_has_priority_over_section3(self):
        strategy = self._strategy()
        frames = self._base_frames()
        frames["5m"].loc[frames["5m"].index[-1], "high"] = 70.0
        strategy.state["section3"] = {
            "break_latched": True,
            "break_reason": "sma20",
            "break_time": frames["5m"].index[-2].strftime("%Y-%m-%d %H:%M:%S"),
        }

        def get_df(interval: str, limit: int = 300) -> pd.DataFrame:
            return frames[interval].copy()

        with patch.object(strategy, "_now", return_value=datetime(2026, 3, 10, 14, 0, 0)), \
             patch.object(strategy, "_get_df", side_effect=get_df), \
             patch.object(strategy, "_detect_touches_and_latch", return_value=[]), \
             patch.object(strategy, "_vix_regime", return_value="A"), \
             patch("core.strat_nifty.calculate_and_store_high_accuracy_delta", return_value=None), \
             patch("core.strat_nifty.fetch_latest_delta_data", return_value=make_options_chain()):
            intent = strategy.evaluate_for_entry()

        self.assertIsNotNone(intent)
        self.assertEqual(intent["reason"]["section"], "section2")

    def test_evaluate_for_entry_returns_none_at_3pm_cutoff(self):
        strategy = self._strategy()
        frames = self._base_frames()

        def get_df(interval: str, limit: int = 300) -> pd.DataFrame:
            return frames[interval].copy()

        with patch.object(strategy, "_now", return_value=datetime(2026, 3, 10, 15, 0, 0)), \
             patch.object(strategy, "_get_df", side_effect=get_df), \
             patch.object(strategy, "_detect_touches_and_latch", return_value=[]), \
             patch.object(strategy, "_vix_regime", return_value="A"), \
             patch("core.strat_nifty.calculate_and_store_high_accuracy_delta", return_value=None), \
             patch("core.strat_nifty.fetch_latest_delta_data", return_value=make_options_chain()):
            intent = strategy.evaluate_for_entry()

        self.assertIsNone(intent)


if __name__ == "__main__":
    unittest.main()
