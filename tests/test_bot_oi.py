import os
import sys
import tempfile
import unittest
import logging
from datetime import datetime, time as dtime
from unittest.mock import Mock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bot_oi import OIExpiryPaperBot


class OIExpiryPaperBotTests(unittest.TestCase):
    @staticmethod
    def _logger_tuple():
        logger = logging.getLogger("test.oi.bot")
        return (logger, logger, logger, logger, logger, logger)

    def _mock_strategy(self):
        strategy = Mock()
        strategy.SESSION_RESET_TIME = dtime(9, 15)
        strategy.HARD_CLOSE_TIME = dtime(15, 20)
        strategy.is_tuesday.return_value = True
        strategy._hard_close_reached.return_value = False
        return strategy

    def test_default_files_are_separate_from_existing_nifty_bot_files(self):
        strategy = self._mock_strategy()
        with patch("core.bot_oi.setup_paper_trading_logger", return_value=self._logger_tuple()):
            bot = OIExpiryPaperBot(strategy=strategy)
        self.assertEqual(bot.position_file, "active_position_oi.json")
        self.assertEqual(bot.state_file, "oi_strategy_state.json")

    def test_run_once_opens_type_a_and_type_b_in_same_cycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            position_file = os.path.join(temp_dir, "active_position_oi.json")
            state_file = os.path.join(temp_dir, "oi_strategy_state.json")
            strategy = self._mock_strategy()
            strategy.evaluate.return_value = {
                "timestamp": "2026-04-28 09:30:00",
                "state": {
                    "session": {"last_reset_date": "2026-04-28"},
                    "type_a": {
                        "entry_flag_on": True,
                        "position_1": {"is_open": False, "opened_at": None},
                        "position_2": {"is_open": False, "opened_at": None},
                        "position_1_entry_diff": None,
                        "position_2_entry_diff": None,
                        "position_2_trigger_consumed": False,
                        "position_2_rearmed": False,
                        "position_2_reentry_count": 0,
                        "flag_1": False,
                        "flag_2": False,
                        "strike_bundle": None,
                    },
                    "type_b": {
                        "trigger_1_seen": False,
                        "trigger_2_seen": False,
                        "position": {"is_open": False, "opened_at": None},
                        "sell_pe_entry_ltp": None,
                        "selected_contracts": None,
                    },
                },
                "actions": [
                    {
                        "type": "OPEN_TYPE_A_POS1",
                        "entry_diff": 25.0,
                        "strike_bundle": {
                            "atm_strike": 24200,
                            "buy_ce": {"tradingsymbol": "A_BUY", "strike_price": 24200, "expiry": "2026-04-28"},
                            "sell_ce": {"tradingsymbol": "A_SELL", "strike_price": 24250, "expiry": "2026-04-28"},
                        },
                        "legs": [
                            {"action": "BUY", "tradingsymbol": "A_BUY", "option_type": "CE", "strike_price": 24200, "expiry": "2026-04-28", "last_price": 50.0},
                            {"action": "SELL", "tradingsymbol": "A_SELL", "option_type": "CE", "strike_price": 24250, "expiry": "2026-04-28", "last_price": 25.0},
                        ],
                    },
                    {
                        "type": "OPEN_TYPE_B_POSITION",
                        "trigger_1": True,
                        "trigger_2": False,
                        "sell_pe_entry_ltp": 20.0,
                        "selected_contracts": {
                            "sell_pe": {"tradingsymbol": "B_SELL_PE", "strike_price": 24100, "expiry": "2026-04-28"},
                        },
                        "legs": [
                            {"action": "BUY", "tradingsymbol": "B_BUY_CE", "option_type": "CE", "strike_price": 24000, "expiry": "2026-04-28", "last_price": 100.0},
                            {"action": "BUY", "tradingsymbol": "B_BUY_PE", "option_type": "PE", "strike_price": 24200, "expiry": "2026-04-28", "last_price": 70.0},
                            {"action": "SELL", "tradingsymbol": "B_SELL_PE", "option_type": "PE", "strike_price": 24100, "expiry": "2026-04-28", "last_price": 20.0},
                        ],
                    },
                ],
            }

            with patch("core.bot_oi.setup_paper_trading_logger", return_value=self._logger_tuple()), \
                 patch("core.bot_oi.fetch_latest_option_snapshot", return_value=[]):
                bot = OIExpiryPaperBot(
                    position_file=position_file,
                    state_file=state_file,
                    strategy=strategy,
                )

                with patch.object(bot, "_now", return_value=datetime(2026, 4, 28, 9, 30, 0)):
                    bot.run_once()

            self.assertEqual(bot.position["type_a"]["position_1"]["status"], "OPEN")
            self.assertEqual(bot.position["type_b"]["position"]["status"], "OPEN")
            self.assertEqual(bot.state["type_b"]["sell_pe_entry_ltp"], 20.0)

    def test_type_a_position_2_reopen_persists_rearm_state_and_reentry_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            position_file = os.path.join(temp_dir, "active_position_oi.json")
            state_file = os.path.join(temp_dir, "oi_strategy_state.json")
            strategy = self._mock_strategy()

            with patch("core.bot_oi.setup_paper_trading_logger", return_value=self._logger_tuple()), \
                 patch("core.bot_oi.fetch_latest_option_snapshot", return_value=[]):
                bot = OIExpiryPaperBot(
                    position_file=position_file,
                    state_file=state_file,
                    strategy=strategy,
                )

            bot.state["type_a"]["position_1"] = {"is_open": True, "opened_at": "2026-04-28 09:25:00"}
            bot.state["type_a"]["position_1_entry_diff"] = 30.0
            bot.state["type_a"]["position_2_rearmed"] = True
            bot.position["type_a"]["position_1"] = {"status": "OPEN", "legs": []}

            bot._apply_action(
                {
                    "type": "OPEN_TYPE_A_POS2",
                    "reentry_kind": "reopen",
                    "entry_diff": 25.0,
                    "legs": [
                        {"action": "BUY", "tradingsymbol": "A_BUY", "option_type": "CE", "strike_price": 24200, "expiry": "2026-04-28", "last_price": 50.0},
                        {"action": "SELL", "tradingsymbol": "A_SELL", "option_type": "CE", "strike_price": 24250, "expiry": "2026-04-28", "last_price": 25.0},
                    ],
                    "strike_bundle": {
                        "atm_strike": 24200,
                        "buy_ce": {"tradingsymbol": "A_BUY", "strike_price": 24200, "expiry": "2026-04-28"},
                        "sell_ce": {"tradingsymbol": "A_SELL", "strike_price": 24250, "expiry": "2026-04-28"},
                    },
                },
                "2026-04-28 09:37:00",
            )

            self.assertTrue(bot.state["type_a"]["position_2_trigger_consumed"])
            self.assertFalse(bot.state["type_a"]["position_2_rearmed"])
            self.assertEqual(bot.state["type_a"]["position_2_reentry_count"], 1)

            bot._apply_action({"type": "CLOSE_TYPE_A_POS2", "reason": "position_2_target"}, "2026-04-28 09:42:00")

            self.assertTrue(bot.state["type_a"]["position_2_trigger_consumed"])
            self.assertFalse(bot.state["type_a"]["position_2_rearmed"])
            self.assertEqual(bot.state["type_a"]["position_2_reentry_count"], 1)

    def test_type_a_position_1_open_resets_position_2_reentry_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            position_file = os.path.join(temp_dir, "active_position_oi.json")
            state_file = os.path.join(temp_dir, "oi_strategy_state.json")
            strategy = self._mock_strategy()

            with patch("core.bot_oi.setup_paper_trading_logger", return_value=self._logger_tuple()), \
                 patch("core.bot_oi.fetch_latest_option_snapshot", return_value=[]):
                bot = OIExpiryPaperBot(
                    position_file=position_file,
                    state_file=state_file,
                    strategy=strategy,
                )

            bot.state["type_a"]["position_2_trigger_consumed"] = True
            bot.state["type_a"]["position_2_rearmed"] = True
            bot.state["type_a"]["position_2_reentry_count"] = 1

            bot._apply_action(
                {
                    "type": "OPEN_TYPE_A_POS1",
                    "entry_diff": 25.0,
                    "legs": [
                        {"action": "BUY", "tradingsymbol": "A_BUY", "option_type": "CE", "strike_price": 24200, "expiry": "2026-04-28", "last_price": 50.0},
                        {"action": "SELL", "tradingsymbol": "A_SELL", "option_type": "CE", "strike_price": 24250, "expiry": "2026-04-28", "last_price": 25.0},
                    ],
                    "strike_bundle": {
                        "atm_strike": 24200,
                        "buy_ce": {"tradingsymbol": "A_BUY", "strike_price": 24200, "expiry": "2026-04-28"},
                        "sell_ce": {"tradingsymbol": "A_SELL", "strike_price": 24250, "expiry": "2026-04-28"},
                    },
                },
                "2026-04-28 09:30:00",
            )

            self.assertFalse(bot.state["type_a"]["position_2_trigger_consumed"])
            self.assertFalse(bot.state["type_a"]["position_2_rearmed"])
            self.assertEqual(bot.state["type_a"]["position_2_reentry_count"], 0)

    def test_run_once_force_closes_at_1520_and_resets_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            position_file = os.path.join(temp_dir, "active_position_oi.json")
            state_file = os.path.join(temp_dir, "oi_strategy_state.json")
            strategy = self._mock_strategy()
            strategy._hard_close_reached.return_value = True

            with patch("core.bot_oi.setup_paper_trading_logger", return_value=self._logger_tuple()):
                bot = OIExpiryPaperBot(
                    position_file=position_file,
                    state_file=state_file,
                    strategy=strategy,
                )

            bot.position["type_a"]["position_1"] = {
                "status": "OPEN",
                "legs": [
                    {"tradingsymbol": "A_BUY", "side": "BUY", "option_type": "CE", "strike_price": 24200, "expiry": "2026-04-28", "entry_price": 50.0, "last_price": 50.0},
                ],
            }
            bot.position["type_b"]["position"] = {
                "status": "OPEN",
                "legs": [
                    {"tradingsymbol": "B_SELL", "side": "SELL", "option_type": "PE", "strike_price": 24100, "expiry": "2026-04-28", "entry_price": 20.0, "last_price": 20.0},
                ],
            }
            option_rows = [
                {"tradingsymbol": "A_BUY", "strike_price": 24200, "option_type": "CE", "expiry_date": "2026-04-28", "ltp": 60.0},
                {"tradingsymbol": "B_SELL", "strike_price": 24100, "option_type": "PE", "expiry_date": "2026-04-28", "ltp": 10.0},
            ]

            with patch("core.bot_oi.fetch_latest_option_snapshot", return_value=option_rows), \
                 patch.object(bot, "_now", return_value=datetime(2026, 4, 28, 15, 20, 0)):
                bot.run_once()

            self.assertEqual(bot.position["type_a"]["position_1"]["status"], "FLAT")
            self.assertEqual(bot.position["type_b"]["position"]["status"], "FLAT")
            self.assertEqual(bot.state["session"]["last_reset_date"], "2026-04-28")

    def test_tuesday_safety_cleanup_resets_stale_positions_from_previous_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            position_file = os.path.join(temp_dir, "active_position_oi.json")
            state_file = os.path.join(temp_dir, "oi_strategy_state.json")
            strategy = self._mock_strategy()

            with patch("core.bot_oi.setup_paper_trading_logger", return_value=self._logger_tuple()), \
                 patch("core.bot_oi.fetch_latest_option_snapshot", return_value=[]):
                bot = OIExpiryPaperBot(
                    position_file=position_file,
                    state_file=state_file,
                    strategy=strategy,
                )

            bot.state["session"]["last_reset_date"] = "2026-04-21"
            bot.position["type_a"]["position_1"] = {
                "status": "OPEN",
                "legs": [
                    {"tradingsymbol": "A_BUY", "side": "BUY", "option_type": "CE", "strike_price": 24200, "expiry": "2026-04-28", "entry_price": 50.0, "last_price": 50.0},
                ],
            }

            cleaned = bot.maybe_run_tuesday_safety_cleanup(now=datetime(2026, 4, 28, 9, 0, 0))

            self.assertTrue(cleaned)
            self.assertEqual(bot.position["type_a"]["position_1"]["status"], "FLAT")
            self.assertEqual(bot.state["session"]["last_reset_date"], "2026-04-28")

    def test_tuesday_safety_cleanup_resets_positions_after_hard_close(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            position_file = os.path.join(temp_dir, "active_position_oi.json")
            state_file = os.path.join(temp_dir, "oi_strategy_state.json")
            strategy = self._mock_strategy()

            with patch("core.bot_oi.setup_paper_trading_logger", return_value=self._logger_tuple()), \
                 patch("core.bot_oi.fetch_latest_option_snapshot", return_value=[]):
                bot = OIExpiryPaperBot(
                    position_file=position_file,
                    state_file=state_file,
                    strategy=strategy,
                )

            bot.state["session"]["last_reset_date"] = "2026-04-28"
            bot.position["type_b"]["position"] = {
                "status": "OPEN",
                "legs": [
                    {"tradingsymbol": "B_SELL", "side": "SELL", "option_type": "PE", "strike_price": 24100, "expiry": "2026-04-28", "entry_price": 20.0, "last_price": 20.0},
                ],
            }

            cleaned = bot.maybe_run_tuesday_safety_cleanup(now=datetime(2026, 4, 28, 15, 35, 0))

            self.assertTrue(cleaned)
            self.assertEqual(bot.position["type_b"]["position"]["status"], "FLAT")
            self.assertEqual(bot.state["session"]["last_reset_date"], "2026-04-28")


if __name__ == "__main__":
    unittest.main()
