import os
import sys
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.backtest_oi_bot import BacktestableOIBot
from backtesting.metrics import TradeLog, Trade


class BacktestableOIBotTests(unittest.TestCase):
    def _make_provider(self) -> Mock:
        provider = Mock()
        provider.fetch_market_data.return_value = None
        provider.fetch_option_snapshot.return_value = []
        provider.fetch_next_option_snapshot.return_value = (None, [])
        provider.fetch_option_price.return_value = None
        provider.fetch_open_interest_snapshot.return_value = []
        provider.fetch_open_interest_5m_snapshot.return_value = []
        return provider

    def _make_bot(self, provider: Mock, current_time: datetime) -> BacktestableOIBot:
        return BacktestableOIBot(
            data_provider=provider,
            current_time_fn=lambda: current_time,
            trade_log=TradeLog(),
            symbol="NIFTY50",
        )

    def test_run_once_opens_type_a_and_type_b_using_one_shared_fill_snapshot(self):
        signal_time = datetime(2026, 4, 28, 9, 30, 0)
        fill_time = datetime(2026, 4, 28, 9, 30, 8)
        provider = self._make_provider()
        provider.fetch_next_option_snapshot.return_value = (
            fill_time,
            [
                {"tradingsymbol": "A_BUY", "ltp": 50.0},
                {"tradingsymbol": "A_SELL", "ltp": 25.0},
                {"tradingsymbol": "B_BUY_CE", "ltp": 100.0},
                {"tradingsymbol": "B_BUY_PE", "ltp": 70.0},
                {"tradingsymbol": "B_SELL_PE", "ltp": 20.0},
            ],
        )
        provider.fetch_option_snapshot.return_value = []

        bot = self._make_bot(provider, signal_time)
        bot.strategy.is_tuesday = Mock(return_value=True)
        bot.strategy._hard_close_reached = Mock(return_value=False)
        bot.strategy.evaluate = Mock(return_value={
            "timestamp": "2026-04-28 09:30:00",
            "state": {
                "entry_price_below_1m_smas": True,
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
                    "reason": "entry_flag_and_oi",
                    "entry_price_below_1m_smas": True,
                    "entry_diff": 25.0,
                    "strike_bundle": {
                        "atm_strike": 24200,
                        "buy_ce": {"tradingsymbol": "A_BUY", "strike_price": 24200, "expiry": "2026-04-28"},
                        "sell_ce": {"tradingsymbol": "A_SELL", "strike_price": 24250, "expiry": "2026-04-28"},
                    },
                    "legs": [
                        {"action": "BUY", "tradingsymbol": "A_BUY", "option_type": "CE", "strike_price": 24200, "expiry": "2026-04-28", "last_price": 0.0},
                        {"action": "SELL", "tradingsymbol": "A_SELL", "option_type": "CE", "strike_price": 24250, "expiry": "2026-04-28", "last_price": 0.0},
                    ],
                },
                {
                    "type": "OPEN_TYPE_B_POSITION",
                    "reason": "trigger_and_oi",
                    "trigger_1": True,
                    "trigger_2": False,
                    "entry_price_below_1m_smas": True,
                    "sell_pe_entry_ltp": 20.0,
                    "selected_contracts": {
                        "sell_pe": {"tradingsymbol": "B_SELL_PE", "strike_price": 24100, "expiry": "2026-04-28"},
                    },
                    "legs": [
                        {"action": "BUY", "tradingsymbol": "B_BUY_CE", "option_type": "CE", "strike_price": 24000, "expiry": "2026-04-28", "last_price": 0.0},
                        {"action": "BUY", "tradingsymbol": "B_BUY_PE", "option_type": "PE", "strike_price": 24200, "expiry": "2026-04-28", "last_price": 0.0},
                        {"action": "SELL", "tradingsymbol": "B_SELL_PE", "option_type": "PE", "strike_price": 24100, "expiry": "2026-04-28", "last_price": 0.0},
                    ],
                },
            ],
            "status": {"type_a": {}, "type_b": {}},
        })

        bot.run_once()

        provider.fetch_next_option_snapshot.assert_called_once_with(signal_time.replace(second=5))
        self.assertEqual(bot.position["type_a"]["position_1"]["opened_at"], "2026-04-28 09:30:08")
        self.assertEqual(bot.position["type_b"]["position"]["opened_at"], "2026-04-28 09:30:08")
        self.assertEqual(len(bot.trade_log.trades), 2)
        self.assertEqual(bot.trade_log.trades[0].entry_fill_time, fill_time)
        self.assertEqual(bot.trade_log.trades[1].entry_fill_time, fill_time)

    def test_close_type_a_all_uses_one_shared_exit_snapshot(self):
        signal_time = datetime(2026, 4, 28, 9, 40, 0)
        fill_time = datetime(2026, 4, 28, 9, 40, 9)
        provider = self._make_provider()
        provider.fetch_next_option_snapshot.return_value = (
            fill_time,
            [
                {"tradingsymbol": "A1_BUY", "ltp": 60.0},
                {"tradingsymbol": "A1_SELL", "ltp": 30.0},
                {"tradingsymbol": "A2_BUY", "ltp": 45.0},
                {"tradingsymbol": "A2_SELL", "ltp": 18.0},
            ],
        )
        provider.fetch_option_snapshot.return_value = []

        bot = self._make_bot(provider, signal_time)
        bot.state["session"]["last_reset_date"] = "2026-04-28"
        bot.position["type_a"]["position_1"] = {
            "status": "OPEN",
            "trade_id": "A1-1",
            "branch_name": "type_a.position_1",
            "opened_at": "2026-04-28 09:30:08",
            "legs": [
                {"tradingsymbol": "A1_BUY", "side": "BUY", "option_type": "CE", "strike_price": 24200, "expiry": "2026-04-28", "entry_price": 50.0},
                {"tradingsymbol": "A1_SELL", "side": "SELL", "option_type": "CE", "strike_price": 24250, "expiry": "2026-04-28", "entry_price": 25.0},
            ],
        }
        bot.position["type_a"]["position_2"] = {
            "status": "OPEN",
            "trade_id": "A2-1",
            "branch_name": "type_a.position_2",
            "opened_at": "2026-04-28 09:35:08",
            "legs": [
                {"tradingsymbol": "A2_BUY", "side": "BUY", "option_type": "CE", "strike_price": 24200, "expiry": "2026-04-28", "entry_price": 40.0},
                {"tradingsymbol": "A2_SELL", "side": "SELL", "option_type": "CE", "strike_price": 24250, "expiry": "2026-04-28", "entry_price": 15.0},
            ],
        }
        bot.trade_log.record_entry(Trade(trade_id="A1-1", entry_time=signal_time, lot_id="type_a.position_1", position_type="TYPE_A"))
        bot.trade_log.record_entry(Trade(trade_id="A2-1", entry_time=signal_time, lot_id="type_a.position_2", position_type="TYPE_A"))
        bot.strategy.is_tuesday = Mock(return_value=True)
        bot.strategy._hard_close_reached = Mock(return_value=False)
        bot.strategy.evaluate = Mock(return_value={
            "timestamp": "2026-04-28 09:40:00",
            "state": bot.state,
            "actions": [{"type": "CLOSE_TYPE_A_ALL", "reason": "position_1_sl"}],
            "status": {"type_a": {}, "type_b": {}},
        })

        bot.run_once()

        provider.fetch_next_option_snapshot.assert_called_once_with(signal_time.replace(second=5))
        self.assertEqual(bot.position["type_a"]["position_1"]["status"], "FLAT")
        self.assertEqual(bot.position["type_a"]["position_2"]["status"], "FLAT")
        closed = bot.trade_log.get_closed_trades()
        self.assertEqual(len(closed), 2)
        self.assertTrue(all(trade.exit_fill_time == fill_time for trade in closed))

    def test_open_action_is_skipped_when_fill_snapshot_is_missing_required_leg(self):
        signal_time = datetime(2026, 4, 28, 9, 30, 0)
        fill_time = datetime(2026, 4, 28, 9, 30, 8)
        provider = self._make_provider()
        provider.fetch_next_option_snapshot.return_value = (
            fill_time,
            [{"tradingsymbol": "A_BUY", "ltp": 50.0}],
        )
        provider.fetch_option_snapshot.return_value = []

        bot = self._make_bot(provider, signal_time)
        bot.strategy.is_tuesday = Mock(return_value=True)
        bot.strategy._hard_close_reached = Mock(return_value=False)
        bot.strategy.evaluate = Mock(return_value={
            "timestamp": "2026-04-28 09:30:00",
            "state": bot.state,
            "actions": [
                {
                    "type": "OPEN_TYPE_A_POS1",
                    "reason": "entry_flag_and_oi",
                    "entry_diff": 25.0,
                    "legs": [
                        {"action": "BUY", "tradingsymbol": "A_BUY", "option_type": "CE", "strike_price": 24200, "expiry": "2026-04-28", "last_price": 0.0},
                        {"action": "SELL", "tradingsymbol": "A_SELL", "option_type": "CE", "strike_price": 24250, "expiry": "2026-04-28", "last_price": 0.0},
                    ],
                    "strike_bundle": {
                        "atm_strike": 24200,
                        "buy_ce": {"tradingsymbol": "A_BUY", "strike_price": 24200, "expiry": "2026-04-28"},
                        "sell_ce": {"tradingsymbol": "A_SELL", "strike_price": 24250, "expiry": "2026-04-28"},
                    },
                }
            ],
            "status": {"type_a": {}, "type_b": {}},
        })

        bot.run_once()

        self.assertEqual(bot.position["type_a"]["position_1"]["status"], "FLAT")
        self.assertEqual(bot.trade_log._skipped_entries, 1)
        self.assertEqual(len(bot.trade_log.trades), 0)

    def test_hard_close_uses_strict_exit_snapshot_without_file_io(self):
        signal_time = datetime(2026, 4, 28, 15, 20, 0)
        fill_time = datetime(2026, 4, 28, 15, 20, 8)
        provider = self._make_provider()
        provider.fetch_next_option_snapshot.return_value = (
            fill_time,
            [{"tradingsymbol": "B_SELL", "ltp": 10.0}],
        )

        bot = self._make_bot(provider, signal_time)
        bot.state["session"]["last_reset_date"] = "2026-04-28"
        bot.position["type_b"]["position"] = {
            "status": "OPEN",
            "trade_id": "B-1",
            "branch_name": "type_b.position",
            "opened_at": "2026-04-28 14:55:00",
            "legs": [
                {"tradingsymbol": "B_SELL", "side": "SELL", "option_type": "PE", "strike_price": 24100, "expiry": "2026-04-28", "entry_price": 20.0},
            ],
        }
        bot.trade_log.record_entry(Trade(trade_id="B-1", entry_time=datetime(2026, 4, 28, 14, 55, 0), lot_id="type_b.position", position_type="TYPE_B"))
        bot.strategy.is_tuesday = Mock(return_value=True)
        bot.strategy._hard_close_reached = Mock(return_value=True)

        with patch("builtins.open") as mocked_open:
            bot.run_once()

        mocked_open.assert_not_called()
        self.assertEqual(bot.position["type_b"]["position"]["status"], "FLAT")
        closed = bot.trade_log.get_closed_trades()
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].exit_fill_time, fill_time)


if __name__ == "__main__":
    unittest.main()
