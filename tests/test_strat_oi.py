import os
import sys
import tempfile
import unittest
import logging
from datetime import datetime
from unittest.mock import Mock, patch

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.strat_oi import OIExpiryStrategy, build_default_oi_state


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
        data[name] = list(values) if isinstance(values, (list, tuple, pd.Series)) else [values] * len(index)
    return pd.DataFrame(data, index=index)


def make_position() -> dict:
    return {
        "symbol": "NIFTY50",
        "type_a": {
            "position_1": {"status": "FLAT", "legs": [], "pnl": 0.0},
            "position_2": {"status": "FLAT", "legs": [], "pnl": 0.0},
        },
        "type_b": {
            "position": {"status": "FLAT", "legs": [], "pnl": 0.0},
        },
        "meta": {},
    }


class OIExpiryStrategyTests(unittest.TestCase):
    @staticmethod
    def _oi_loggers():
        return (Mock(name="oi_strategy_logger"), Mock(name="oi_trade_logger"), Mock(name="oi_position_logger"))

    def _strategy(self) -> OIExpiryStrategy:
        with patch("core.strat_oi.setup_oi_logging", return_value=self._oi_loggers()):
            return OIExpiryStrategy(symbol="NIFTY50")

    def _frames(self) -> dict[str, pd.DataFrame]:
        df_1m = make_df(
            pd.date_range("2026-04-28 09:20:00", periods=8, freq="1min"),
            closes=[24280, 24282, 24283, 24285, 24286, 24284, 24281, 24279],
            highs=[24290, 24292, 24293, 24295, 24296, 24294, 24291, 24289],
            lows=[24270] * 8,
            sh=[24350] * 8,
            sma_20=[24320] * 8,
            sma_50=[24340] * 8,
        )
        df_5m = make_df(
            pd.date_range("2026-04-28 09:15:00", periods=4, freq="5min"),
            closes=[24320, 24310, 24305, 24300],
            highs=[24325] * 4,
            lows=[24290] * 4,
            sma_20=[24290] * 4,
            sma_50=[24320] * 4,
            donchian_mid=[24300] * 4,
        )
        return {"1m": df_1m, "5m": df_5m}

    def _type_a_state(self) -> dict:
        state = build_default_oi_state()
        state["type_a"]["position_1"] = {"is_open": True, "opened_at": "2026-04-28 09:25:00"}
        state["type_a"]["position_1_entry_diff"] = 30.0
        state["type_a"]["strike_bundle"] = {
            "atm_strike": 24200,
            "buy_ce": {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "expiry": "2026-04-28"},
            "sell_ce": {"tradingsymbol": "NIFTYNEAR24250CE", "strike_price": 24250, "expiry": "2026-04-28"},
        }
        return state

    def _type_a_oi_rows(self) -> list[dict]:
        return [
            {"tradingsymbol": "NIFTYNEAR24200PE", "strike_price": 24200, "option_type": "PE", "expiry_date": "2026-04-28", "open_interest": 5000},
            {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 4000},
            {"tradingsymbol": "NIFTYNEAR24300CE", "strike_price": 24300, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 3900},
        ]

    def _evaluate_type_a(
        self,
        strategy: OIExpiryStrategy,
        state: dict,
        position: dict,
        option_rows: list[dict],
        now: datetime,
        frames: dict[str, pd.DataFrame] | None = None,
    ):
        frames = frames or self._frames()
        with patch.object(strategy, "_now", return_value=now), \
             patch.object(strategy, "_get_df", side_effect=lambda interval, limit=300: frames[interval].copy()), \
             patch.object(strategy, "_fetch_option_snapshot", return_value=option_rows), \
             patch.object(strategy, "_fetch_open_interest_snapshot", return_value=self._type_a_oi_rows()), \
             patch.object(strategy, "_fetch_open_interest_5m_snapshot", return_value=[]):
            return strategy.evaluate(state, position)

    def test_type_a_entry_uses_floor_atm_and_nearest_expiry_contracts(self):
        strategy = self._strategy()
        frames = self._frames()
        option_rows = [
            {"tradingsymbol": "NIFTYFAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-05-05", "ltp": 70.0},
            {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 72.0},
            {"tradingsymbol": "NIFTYNEAR24250CE", "strike_price": 24250, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 40.0},
        ]
        oi_rows = [
            {"tradingsymbol": "NIFTYNEAR24200PE", "strike_price": 24200, "option_type": "PE", "expiry_date": "2026-04-28", "open_interest": 5000},
            {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 4000},
            {"tradingsymbol": "NIFTYNEAR24300CE", "strike_price": 24300, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 3900},
            {"tradingsymbol": "NIFTYFAR24200PE", "strike_price": 24200, "option_type": "PE", "expiry_date": "2026-05-05", "open_interest": 9000},
        ]

        with patch.object(strategy, "_now", return_value=datetime(2026, 4, 28, 9, 27, 0)), \
             patch.object(strategy, "_get_df", side_effect=lambda interval, limit=300: frames[interval].copy()), \
             patch.object(strategy, "_fetch_option_snapshot", return_value=option_rows), \
             patch.object(strategy, "_fetch_open_interest_snapshot", return_value=oi_rows), \
             patch.object(strategy, "_fetch_open_interest_5m_snapshot", return_value=[]):
            result = strategy.evaluate(build_default_oi_state(), make_position())

        self.assertEqual(result["actions"][0]["type"], "OPEN_TYPE_A_POS1")
        self.assertTrue(result["state"]["entry_price_below_1m_smas"])
        self.assertTrue(result["status"]["entry_price_below_1m_smas"])
        self.assertTrue(result["status"]["entry_window_open"])
        self.assertTrue(result["status"]["data_ready"])
        self.assertEqual(result["status"]["spot"], 24279.0)
        self.assertEqual(result["status"]["atm_strike"], 24200)
        self.assertTrue(result["actions"][0]["entry_price_below_1m_smas"])
        self.assertEqual(result["actions"][0]["strike_bundle"]["atm_strike"], 24200)
        self.assertIn("OPEN_TYPE_A_POS1", result["status"]["type_a"]["pending_actions"])
        self.assertEqual(
            [leg["tradingsymbol"] for leg in result["actions"][0]["legs"]],
            ["NIFTYNEAR24200CE", "NIFTYNEAR24250CE"],
        )

    def test_type_a_position_1_entry_is_blocked_when_1m_close_is_not_below_both_smas(self):
        strategy = self._strategy()
        frames = self._frames()
        frames["1m"]["close"] = [24330] * len(frames["1m"])
        frames["1m"]["high"] = [24331] * len(frames["1m"])
        option_rows = [
            {"tradingsymbol": "NIFTYNEAR24300CE", "strike_price": 24300, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 72.0},
            {"tradingsymbol": "NIFTYNEAR24350CE", "strike_price": 24350, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 40.0},
        ]
        oi_rows = [
            {"tradingsymbol": "NIFTYNEAR24300PE", "strike_price": 24300, "option_type": "PE", "expiry_date": "2026-04-28", "open_interest": 5000},
            {"tradingsymbol": "NIFTYNEAR24300CE", "strike_price": 24300, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 4000},
            {"tradingsymbol": "NIFTYNEAR24400CE", "strike_price": 24400, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 3900},
        ]

        with patch.object(strategy, "_now", return_value=datetime(2026, 4, 28, 9, 27, 0)), \
             patch.object(strategy, "_get_df", side_effect=lambda interval, limit=300: frames[interval].copy()), \
             patch.object(strategy, "_fetch_option_snapshot", return_value=option_rows), \
             patch.object(strategy, "_fetch_open_interest_snapshot", return_value=oi_rows), \
             patch.object(strategy, "_fetch_open_interest_5m_snapshot", return_value=[]):
            result = strategy.evaluate(build_default_oi_state(), make_position())

        self.assertEqual(result["actions"], [])
        self.assertFalse(result["state"]["entry_price_below_1m_smas"])
        self.assertFalse(result["status"]["entry_price_below_1m_smas"])
        self.assertIn("entry_price_not_below_1m_smas", result["status"]["type_a"]["blockers"])

    def test_type_a_position_2_opens_after_five_point_diff_drop_and_oi_recheck(self):
        strategy = self._strategy()
        state = self._type_a_state()
        position = make_position()
        position["type_a"]["position_1"] = {
            "status": "OPEN",
            "legs": [],
        }
        frames = self._frames()
        frames["1m"]["close"] = [24345] * len(frames["1m"])
        frames["1m"]["high"] = [24346] * len(frames["1m"])
        option_rows = [
            {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 40.0},
            {"tradingsymbol": "NIFTYNEAR24250CE", "strike_price": 24250, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 15.0},
        ]
        result = self._evaluate_type_a(
            strategy,
            state,
            position,
            option_rows,
            now=datetime(2026, 4, 28, 9, 31, 0),
            frames=frames,
        )

        self.assertFalse(result["state"]["entry_price_below_1m_smas"])
        self.assertTrue(result["state"]["type_a"]["flag_1"])
        self.assertTrue(result["state"]["type_a"]["flag_2"])
        self.assertFalse(result["state"]["type_a"]["position_2_trigger_consumed"])
        self.assertEqual(result["actions"][0]["reentry_kind"], "initial")
        self.assertEqual(result["actions"][0]["type"], "OPEN_TYPE_A_POS2")
        self.assertEqual(result["status"]["type_a"]["current_diff"], 25.0)
        self.assertIn("OPEN_TYPE_A_POS2", result["status"]["type_a"]["pending_actions"])

    def test_type_a_position_2_does_not_reopen_without_recovery(self):
        strategy = self._strategy()
        state = self._type_a_state()
        state["type_a"]["position_2"] = {"is_open": False, "opened_at": None}
        state["type_a"]["position_2_trigger_consumed"] = True
        position = make_position()
        position["type_a"]["position_1"] = {"status": "OPEN", "legs": []}
        position["type_a"]["position_2"] = {"status": "CLOSED", "legs": []}
        option_rows = [
            {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 40.0},
            {"tradingsymbol": "NIFTYNEAR24250CE", "strike_price": 24250, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 15.0},
        ]

        result = self._evaluate_type_a(
            strategy,
            state,
            position,
            option_rows,
            now=datetime(2026, 4, 28, 9, 35, 0),
        )

        self.assertEqual(result["actions"], [])
        self.assertTrue(result["state"]["type_a"]["position_2_trigger_consumed"])
        self.assertFalse(result["state"]["type_a"]["position_2_rearmed"])

    def test_type_a_position_2_rearms_after_recovery_and_reopens_once(self):
        strategy = self._strategy()
        state = self._type_a_state()
        state["type_a"]["position_2_trigger_consumed"] = True
        position = make_position()
        position["type_a"]["position_1"] = {"status": "OPEN", "legs": []}
        position["type_a"]["position_2"] = {"status": "CLOSED", "legs": []}
        frames = self._frames()
        frames["1m"]["close"] = [24345] * len(frames["1m"])
        frames["1m"]["high"] = [24346] * len(frames["1m"])

        recovery_rows = [
            {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 45.0},
            {"tradingsymbol": "NIFTYNEAR24250CE", "strike_price": 24250, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 19.0},
        ]
        recovery = self._evaluate_type_a(
            strategy,
            state,
            position,
            recovery_rows,
            now=datetime(2026, 4, 28, 9, 36, 0),
            frames=frames,
        )

        self.assertEqual(recovery["actions"], [])
        self.assertFalse(recovery["state"]["type_a"]["position_2_trigger_consumed"])
        self.assertTrue(recovery["state"]["type_a"]["position_2_rearmed"])

        reopen_rows = [
            {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 40.0},
            {"tradingsymbol": "NIFTYNEAR24250CE", "strike_price": 24250, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 15.0},
        ]
        reopen = self._evaluate_type_a(
            strategy,
            recovery["state"],
            position,
            reopen_rows,
            now=datetime(2026, 4, 28, 9, 37, 0),
            frames=frames,
        )

        self.assertEqual(reopen["actions"][0]["type"], "OPEN_TYPE_A_POS2")
        self.assertEqual(reopen["actions"][0]["reentry_kind"], "reopen")

    def test_type_a_position_2_blocks_further_reentries_after_single_reopen(self):
        strategy = self._strategy()
        state = self._type_a_state()
        state["type_a"]["position_2_trigger_consumed"] = True
        state["type_a"]["position_2_reentry_count"] = 1
        position = make_position()
        position["type_a"]["position_1"] = {"status": "OPEN", "legs": []}
        position["type_a"]["position_2"] = {"status": "CLOSED", "legs": []}
        option_rows = [
            {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 45.0},
            {"tradingsymbol": "NIFTYNEAR24250CE", "strike_price": 24250, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 19.0},
        ]

        result = self._evaluate_type_a(
            strategy,
            state,
            position,
            option_rows,
            now=datetime(2026, 4, 28, 9, 38, 0),
        )

        self.assertEqual(result["actions"], [])
        self.assertTrue(result["state"]["type_a"]["position_2_trigger_consumed"])
        self.assertFalse(result["state"]["type_a"]["position_2_rearmed"])

    def test_type_a_position_1_target_closes_only_position_1_when_position_2_is_open(self):
        strategy = self._strategy()
        frames = self._frames()
        state = build_default_oi_state()
        state["type_a"]["position_1"] = {"is_open": True, "opened_at": "2026-04-28 09:25:00"}
        state["type_a"]["position_2"] = {"is_open": True, "opened_at": "2026-04-28 09:30:00"}
        state["type_a"]["position_1_entry_diff"] = 30.0
        state["type_a"]["position_2_entry_diff"] = 25.0
        state["type_a"]["strike_bundle"] = {
            "atm_strike": 24200,
            "buy_ce": {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "expiry": "2026-04-28"},
            "sell_ce": {"tradingsymbol": "NIFTYNEAR24250CE", "strike_price": 24250, "expiry": "2026-04-28"},
        }
        position = make_position()
        position["type_a"]["position_1"] = {"status": "OPEN", "legs": []}
        position["type_a"]["position_2"] = {"status": "OPEN", "legs": []}
        option_rows = [
            {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 61.0},
            {"tradingsymbol": "NIFTYNEAR24250CE", "strike_price": 24250, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 15.0},
        ]

        with patch.object(strategy, "_now", return_value=datetime(2026, 4, 28, 9, 40, 0)), \
             patch.object(strategy, "_get_df", side_effect=lambda interval, limit=300: frames[interval].copy()), \
             patch.object(strategy, "_fetch_option_snapshot", return_value=option_rows), \
             patch.object(strategy, "_fetch_open_interest_snapshot", return_value=[]), \
             patch.object(strategy, "_fetch_open_interest_5m_snapshot", return_value=[]):
            result = strategy.evaluate(state, position)

        self.assertEqual(result["actions"][0]["type"], "CLOSE_TYPE_A_POS1")
        self.assertEqual(result["actions"][0]["reason"], "position_1_target")

    def test_type_b_trigger_1_opens_position(self):
        strategy = self._strategy()
        frames = self._frames()
        frames["1m"]["close"] = [24310] * len(frames["1m"])
        frames["1m"]["high"] = [24311] * len(frames["1m"])
        frames["5m"]["sma_50"] = [24315] * len(frames["5m"])
        option_rows = [
            {"tradingsymbol": "NIFTYNEAR24100CE", "strike_price": 24100, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 100.0},
            {"tradingsymbol": "NIFTYNEAR24300PE", "strike_price": 24300, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 75.0},
            {"tradingsymbol": "NIFTYNEAR24200PE", "strike_price": 24200, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 28.0},
        ]
        oi_rows = [
            {"tradingsymbol": "NIFTYNEAR24300PE", "strike_price": 24300, "option_type": "PE", "expiry_date": "2026-04-28", "open_interest": 5000},
            {"tradingsymbol": "NIFTYNEAR24300CE", "strike_price": 24300, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 4000},
            {"tradingsymbol": "NIFTYNEAR24400CE", "strike_price": 24400, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 3900},
        ]

        with patch.object(strategy, "_now", return_value=datetime(2026, 4, 28, 9, 33, 0)), \
             patch.object(strategy, "_get_df", side_effect=lambda interval, limit=300: frames[interval].copy()), \
             patch.object(strategy, "_fetch_option_snapshot", return_value=option_rows), \
             patch.object(strategy, "_fetch_open_interest_snapshot", return_value=oi_rows), \
             patch.object(strategy, "_fetch_open_interest_5m_snapshot", return_value=[]):
            result = strategy.evaluate(build_default_oi_state(), make_position())

        self.assertEqual(result["actions"][0]["type"], "OPEN_TYPE_B_POSITION")
        self.assertTrue(result["actions"][0]["trigger_1"])
        self.assertTrue(result["actions"][0]["entry_price_below_1m_smas"])
        self.assertEqual(result["actions"][0]["selected_contracts"]["sell_pe"]["strike_price"], 24200)
        self.assertIn("OPEN_TYPE_B_POSITION", result["status"]["type_b"]["pending_actions"])

    def test_type_b_trigger_2_uses_latest_contract_ltp_against_oi_vwap(self):
        strategy = self._strategy()
        frames = self._frames()
        option_rows = [
            {"tradingsymbol": "NIFTYNEAR24000CE", "strike_price": 24000, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 12.0},
            {"tradingsymbol": "NIFTYNEAR24200PE", "strike_price": 24200, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 75.0},
            {"tradingsymbol": "NIFTYNEAR24100PE", "strike_price": 24100, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 30.0},
        ]
        oi_rows = [
            {"tradingsymbol": "NIFTYNEAR24200PE", "strike_price": 24200, "option_type": "PE", "expiry_date": "2026-04-28", "open_interest": 5000},
            {"tradingsymbol": "NIFTYNEAR24200CE", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 4000},
            {"tradingsymbol": "NIFTYNEAR24300CE", "strike_price": 24300, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 3900},
        ]
        oi_5m_rows = [
            {"tradingsymbol": "NIFTYNEAR24000CE", "strike_price": 24000, "option_type": "CE", "expiry_date": "2026-04-28", "vwap": 13.0},
        ]

        with patch.object(strategy, "_now", return_value=datetime(2026, 4, 28, 9, 34, 0)), \
             patch.object(strategy, "_get_df", side_effect=lambda interval, limit=300: frames[interval].copy()), \
             patch.object(strategy, "_fetch_option_snapshot", return_value=option_rows), \
             patch.object(strategy, "_fetch_open_interest_snapshot", return_value=oi_rows), \
             patch.object(strategy, "_fetch_open_interest_5m_snapshot", return_value=oi_5m_rows):
            result = strategy.evaluate(build_default_oi_state(), make_position())

        self.assertEqual(result["actions"][0]["type"], "OPEN_TYPE_B_POSITION")
        self.assertTrue(result["actions"][0]["trigger_2"])

    def test_type_b_entry_is_blocked_when_1m_close_is_not_below_both_smas(self):
        strategy = self._strategy()
        frames = self._frames()
        frames["1m"]["close"] = [24345] * len(frames["1m"])
        frames["1m"]["high"] = [24346] * len(frames["1m"])
        frames["5m"]["sma_50"] = [24350] * len(frames["5m"])
        option_rows = [
            {"tradingsymbol": "NIFTYNEAR24100CE", "strike_price": 24100, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 100.0},
            {"tradingsymbol": "NIFTYNEAR24300PE", "strike_price": 24300, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 75.0},
            {"tradingsymbol": "NIFTYNEAR24200PE", "strike_price": 24200, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 28.0},
        ]
        oi_rows = [
            {"tradingsymbol": "NIFTYNEAR24300PE", "strike_price": 24300, "option_type": "PE", "expiry_date": "2026-04-28", "open_interest": 5000},
            {"tradingsymbol": "NIFTYNEAR24300CE", "strike_price": 24300, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 4000},
            {"tradingsymbol": "NIFTYNEAR24400CE", "strike_price": 24400, "option_type": "CE", "expiry_date": "2026-04-28", "open_interest": 3900},
        ]

        with patch.object(strategy, "_now", return_value=datetime(2026, 4, 28, 9, 33, 0)), \
             patch.object(strategy, "_get_df", side_effect=lambda interval, limit=300: frames[interval].copy()), \
             patch.object(strategy, "_fetch_option_snapshot", return_value=option_rows), \
             patch.object(strategy, "_fetch_open_interest_snapshot", return_value=oi_rows), \
             patch.object(strategy, "_fetch_open_interest_5m_snapshot", return_value=[]):
            result = strategy.evaluate(build_default_oi_state(), make_position())

        self.assertEqual(result["actions"], [])
        self.assertFalse(result["state"]["entry_price_below_1m_smas"])
        self.assertFalse(result["status"]["entry_price_below_1m_smas"])
        self.assertIn("entry_price_not_below_1m_smas", result["status"]["type_b"]["blockers"])

    def test_type_b_exit_on_sell_pe_rise(self):
        strategy = self._strategy()
        frames = self._frames()
        state = build_default_oi_state()
        state["type_b"]["position"] = {"is_open": True, "opened_at": "2026-04-28 09:30:00"}
        state["type_b"]["sell_pe_entry_ltp"] = 20.0
        state["type_b"]["selected_contracts"] = {
            "sell_pe": {"tradingsymbol": "NIFTYNEAR24100PE", "strike_price": 24100, "expiry": "2026-04-28"},
        }
        position = make_position()
        position["type_b"]["position"] = {
            "status": "OPEN",
            "legs": [
                {"tradingsymbol": "NIFTYNEAR24100PE", "side": "SELL", "option_type": "PE", "strike_price": 24100, "expiry": "2026-04-28", "entry_price": 20.0, "last_price": 20.0},
            ],
        }
        option_rows = [
            {"tradingsymbol": "NIFTYNEAR24100PE", "strike_price": 24100, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 45.0},
        ]

        with patch.object(strategy, "_now", return_value=datetime(2026, 4, 28, 9, 41, 0)), \
             patch.object(strategy, "_get_df", side_effect=lambda interval, limit=300: frames[interval].copy()), \
             patch.object(strategy, "_fetch_option_snapshot", return_value=option_rows), \
             patch.object(strategy, "_fetch_open_interest_snapshot", return_value=[]), \
             patch.object(strategy, "_fetch_open_interest_5m_snapshot", return_value=[]):
            result = strategy.evaluate(state, position)

        self.assertEqual(result["actions"][0]["type"], "CLOSE_TYPE_B_POSITION")
        self.assertEqual(result["actions"][0]["reason"], "sell_pe_rise")

    def test_type_b_exit_on_sh_breach(self):
        strategy = self._strategy()
        frames = self._frames()
        frames["1m"]["high"] = [24400] * len(frames["1m"])
        frames["1m"]["sh"] = [24300] * len(frames["1m"])
        state = build_default_oi_state()
        state["type_b"]["position"] = {"is_open": True, "opened_at": "2026-04-28 09:30:00"}
        state["type_b"]["sell_pe_entry_ltp"] = 20.0
        state["type_b"]["selected_contracts"] = {
            "sell_pe": {"tradingsymbol": "NIFTYNEAR24100PE", "strike_price": 24100, "expiry": "2026-04-28"},
        }
        position = make_position()
        position["type_b"]["position"] = {
            "status": "OPEN",
            "legs": [
                {"tradingsymbol": "NIFTYNEAR24100PE", "side": "SELL", "option_type": "PE", "strike_price": 24100, "expiry": "2026-04-28", "entry_price": 20.0, "last_price": 20.0},
            ],
        }
        option_rows = [
            {"tradingsymbol": "NIFTYNEAR24100PE", "strike_price": 24100, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 25.0},
        ]

        with patch.object(strategy, "_now", return_value=datetime(2026, 4, 28, 9, 42, 0)), \
             patch.object(strategy, "_get_df", side_effect=lambda interval, limit=300: frames[interval].copy()), \
             patch.object(strategy, "_fetch_option_snapshot", return_value=option_rows), \
             patch.object(strategy, "_fetch_open_interest_snapshot", return_value=[]), \
             patch.object(strategy, "_fetch_open_interest_5m_snapshot", return_value=[]):
            result = strategy.evaluate(state, position)

        self.assertEqual(result["actions"][0]["reason"], "sh_breach")

    def test_type_b_exit_on_profit_target(self):
        strategy = self._strategy()
        frames = self._frames()
        state = build_default_oi_state()
        state["type_b"]["position"] = {"is_open": True, "opened_at": "2026-04-28 09:30:00"}
        state["type_b"]["sell_pe_entry_ltp"] = 2000.0
        state["type_b"]["selected_contracts"] = {
            "sell_pe": {"tradingsymbol": "NIFTYNEAR24100PE", "strike_price": 24100, "expiry": "2026-04-28"},
        }
        position = make_position()
        position["type_b"]["position"] = {
            "status": "OPEN",
            "legs": [
                {"tradingsymbol": "NIFTYBUYCE", "side": "BUY", "option_type": "CE", "strike_price": 24000, "expiry": "2026-04-28", "entry_price": 10.0, "last_price": 10.0},
                {"tradingsymbol": "NIFTYBUYPE", "side": "BUY", "option_type": "PE", "strike_price": 24200, "expiry": "2026-04-28", "entry_price": 10.0, "last_price": 10.0},
                {"tradingsymbol": "NIFTYNEAR24100PE", "side": "SELL", "option_type": "PE", "strike_price": 24100, "expiry": "2026-04-28", "entry_price": 2000.0, "last_price": 2000.0},
            ],
        }
        option_rows = [
            {"tradingsymbol": "NIFTYBUYCE", "strike_price": 24000, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 2010.0},
            {"tradingsymbol": "NIFTYBUYPE", "strike_price": 24200, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 2010.0},
            {"tradingsymbol": "NIFTYNEAR24100PE", "strike_price": 24100, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 1990.0},
        ]

        with patch.object(strategy, "_now", return_value=datetime(2026, 4, 28, 9, 43, 0)), \
             patch.object(strategy, "_get_df", side_effect=lambda interval, limit=300: frames[interval].copy()), \
             patch.object(strategy, "_fetch_option_snapshot", return_value=option_rows), \
             patch.object(strategy, "_fetch_open_interest_snapshot", return_value=[]), \
             patch.object(strategy, "_fetch_open_interest_5m_snapshot", return_value=[]):
            result = strategy.evaluate(state, position)

        self.assertEqual(result["actions"][0]["reason"], "profit_target")
        self.assertEqual(result["status"]["type_b"]["current_pnl"], 4010.0)


if __name__ == "__main__":
    unittest.main()
