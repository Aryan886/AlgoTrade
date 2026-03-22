import os
import sys
import tempfile
import unittest
import logging
from datetime import datetime
from unittest.mock import Mock, patch

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bot_nifty import NiftyPaperBot


def make_bot_df(index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0] * len(index),
            "high": [101.0] * len(index),
            "low": [99.0] * len(index),
            "close": [100.0] * len(index),
        },
        index=index,
    )


class NiftyPaperBotTests(unittest.TestCase):
    @staticmethod
    def _logger_tuple():
        logger = logging.getLogger("test.nifty.bot")
        return (logger, logger, logger, logger, logger, logger)

    def test_run_once_does_not_open_new_trade_while_any_lot_is_open(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            position_file = os.path.join(temp_dir, "active_position_nifty.json")
            state_file = os.path.join(temp_dir, "nifty_strategy_state.json")
            with patch("core.bot_nifty.setup_paper_trading_logger", return_value=self._logger_tuple()), \
                 patch("core.bot_nifty.NiftyOptionsStrategy") as strategy_cls:
                strategy_mock = Mock()
                strategy_cls.return_value = strategy_mock
                bot = NiftyPaperBot(
                    symbol="NIFTY50",
                    position_file=position_file,
                    state_file=state_file,
                    dry_run=False,
                )

            bot.position = {
                "symbol": "NIFTY50",
                "lots": {
                    "lot1": {
                        "status": "OPEN",
                        "timeframe": "1m",
                        "sl_current_level": 120.0,
                        "last_trail_ts": None,
                        "last_exit_check_ts": None,
                        "legs": [],
                    },
                    "lot2": {
                        "status": "CLOSED",
                        "timeframe": "5m",
                        "sl_current_level": 120.0,
                        "last_trail_ts": None,
                        "last_exit_check_ts": None,
                        "legs": [],
                    },
                },
                "meta": {},
            }

            df_1m = make_bot_df(pd.date_range("2026-03-10 09:15:00", periods=2, freq="1min"))
            df_5m = make_bot_df(pd.date_range("2026-03-10 09:15:00", periods=2, freq="5min"))

            def fetch_market_data_side_effect(symbol: str, interval: str, limit: int = 300):
                return df_1m.copy() if interval == "1m" else df_5m.copy()

            bot.strategy.maybe_session_reset = Mock()
            bot.strategy.evaluate_for_entry = Mock()

            with patch.object(bot, "_now", return_value=datetime(2026, 3, 10, 14, 0, 0)), \
                 patch("core.bot_nifty.fetch_market_data", side_effect=fetch_market_data_side_effect):
                bot.run_once()

            bot.strategy.evaluate_for_entry.assert_not_called()
            self.assertEqual(bot.position["lots"]["lot1"]["status"], "OPEN")

    def test_close_lot_uses_fallback_quote_when_latest_snapshot_misses_symbol(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            position_file = os.path.join(temp_dir, "active_position_nifty.json")
            state_file = os.path.join(temp_dir, "nifty_strategy_state.json")
            with patch("core.bot_nifty.setup_paper_trading_logger", return_value=self._logger_tuple()), \
                 patch("core.bot_nifty.NiftyOptionsStrategy") as strategy_cls:
                strategy_cls.return_value = Mock()
                bot = NiftyPaperBot(
                    symbol="NIFTY50",
                    position_file=position_file,
                    state_file=state_file,
                    dry_run=False,
                )

        bot.position = {
            "symbol": "NIFTY50",
            "lots": {
                "lot1": {
                    "status": "OPEN",
                    "timeframe": "1m",
                    "legs": [
                        {
                            "tradingsymbol": "NIFTYTESTPE",
                            "side": "BUY",
                            "option_type": "PE",
                            "strike_price": 23800,
                            "expiry": "2026-03-17",
                            "entry_price": 10.0,
                            "last_price": 10.0,
                        }
                    ],
                }
            },
            "meta": {},
        }

        bot._fetch_options_data = Mock(return_value=[])
        bot._lookup_held_leg_price = Mock(return_value=12.0)
        bot.dry_run = True

        bot._close_lot("lot1", exit_time="2026-03-10 10:00:00")

        lot = bot.position["lots"]["lot1"]
        self.assertEqual(lot["status"], "CLOSED")
        self.assertEqual(lot["legs"][0]["exit_price"], 12.0)
        self.assertEqual(lot["pnl"], 2.0)

    def test_run_once_force_closes_open_lots_at_3pm_and_skips_new_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            position_file = os.path.join(temp_dir, "active_position_nifty.json")
            state_file = os.path.join(temp_dir, "nifty_strategy_state.json")
            with patch("core.bot_nifty.setup_paper_trading_logger", return_value=self._logger_tuple()), \
                 patch("core.bot_nifty.NiftyOptionsStrategy") as strategy_cls:
                strategy_mock = Mock()
                strategy_cls.return_value = strategy_mock
                bot = NiftyPaperBot(
                    symbol="NIFTY50",
                    position_file=position_file,
                    state_file=state_file,
                    dry_run=False,
                )

        bot.position = {
            "symbol": "NIFTY50",
            "lots": {
                "lot1": {
                    "status": "OPEN",
                    "timeframe": "1m",
                    "legs": [
                        {
                            "tradingsymbol": "NIFTYTESTCE",
                            "side": "BUY",
                            "option_type": "CE",
                            "strike_price": 23500,
                            "expiry": "2026-03-26",
                            "entry_price": 10.0,
                            "last_price": 10.0,
                        }
                    ],
                },
                "lot2": {
                    "status": "OPEN",
                    "timeframe": "5m",
                    "legs": [
                        {
                            "tradingsymbol": "NIFTYTESTPE",
                            "side": "SELL",
                            "option_type": "PE",
                            "strike_price": 23400,
                            "expiry": "2026-03-26",
                            "entry_price": 12.0,
                            "last_price": 12.0,
                        }
                    ],
                },
            },
            "meta": {},
        }

        bot.strategy.maybe_session_reset = Mock()
        bot.strategy.evaluate_for_entry = Mock()
        bot._fetch_options_data = Mock(
            return_value=[
                {"tradingsymbol": "NIFTYTESTCE", "ltp": 11.0},
                {"tradingsymbol": "NIFTYTESTPE", "ltp": 10.0},
            ]
        )
        bot.dry_run = True

        with patch.object(bot, "_now", return_value=datetime(2026, 3, 10, 15, 0, 0)):
            bot.run_once()

        bot.strategy.maybe_session_reset.assert_called_once_with(now=datetime(2026, 3, 10, 15, 0, 0))
        bot.strategy.evaluate_for_entry.assert_not_called()
        self.assertEqual(bot.position["lots"]["lot1"]["status"], "CLOSED")
        self.assertEqual(bot.position["lots"]["lot1"]["legs"][0]["exit_price"], 11.0)
        self.assertEqual(bot.position["lots"]["lot2"]["status"], "CLOSED")
        self.assertEqual(bot.position["lots"]["lot2"]["legs"][0]["exit_price"], 10.0)


if __name__ == "__main__":
    unittest.main()
