import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.backtest_bot import BacktestableBot
from backtesting.exceptions import StrictBacktestDataError
from backtesting.metrics import Trade, TradeLog


def make_market_df(start: str, periods: int, freq: str, base: float = 100.0) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq=freq)
    values = [base + idx for idx in range(periods)]
    return pd.DataFrame(
        {
            "open": values,
            "high": [value + 2.0 for value in values],
            "low": [value - 1.0 for value in values],
            "close": [value + 0.5 for value in values],
        },
        index=index,
    )


class BacktestableBotStrictFillTests(unittest.TestCase):
    def _make_bot(self, provider: Mock, current_time: datetime) -> BacktestableBot:
        trade_log = TradeLog()
        with patch("backtesting.backtest_bot.BacktestableStrategy") as strategy_cls:
            strategy_cls.return_value = Mock()
            bot = BacktestableBot(
                data_provider=provider,
                current_time_fn=lambda: current_time,
                trade_log=trade_log,
                symbol="NIFTY50",
            )
        return bot

    def test_open_two_lots_uses_first_post_signal_snapshot(self):
        signal_time = datetime(2026, 3, 20, 9, 20, 0)
        fill_time = datetime(2026, 3, 20, 9, 20, 31)
        provider = Mock()
        provider.fetch_market_data.side_effect = lambda current_time, interval, limit: (
            make_market_df("2026-03-20 09:13:00", 8, "1min", 100.0)
            if interval == "1m"
            else make_market_df("2026-03-20 08:45:00", 8, "5min", 200.0)
        )
        provider.fetch_next_delta_snapshot.return_value = (
            fill_time,
            [
                {"tradingsymbol": "OPTPE", "ltp": 101.0},
                {"tradingsymbol": "OPTCE", "ltp": 51.0},
            ],
        )

        bot = self._make_bot(provider, signal_time)
        intent = {
            "timestamp": "2026-03-20 09:20:00",
            "position_type": "A",
            "reason": {"section": "section1", "subcat": "a"},
            "spot": 22450.0,
            "strikes": {"pe_strike": 22400, "ce_strike_near": 22500, "pe_strike_upper": 22600},
            "legs": [
                {"tradingsymbol": "OPTPE", "action": "SELL", "option_type": "PE", "strike_price": 22400, "expiry": "2026-03-26", "last_price": 999.0},
                {"tradingsymbol": "OPTCE", "action": "BUY", "option_type": "CE", "strike_price": 22500, "expiry": "2026-03-26", "last_price": 999.0},
            ],
            "_gate_checks": [],
        }

        bot._open_two_lots(intent)

        lot1 = bot.position["lots"]["lot1"]
        provider.fetch_next_delta_snapshot.assert_called_once_with(signal_time + timedelta(seconds=5))
        self.assertEqual(lot1["opened_at"], "2026-03-20 09:20:31")
        self.assertEqual(lot1["legs"][0]["entry_price"], 101.0)
        self.assertEqual(lot1["legs"][1]["entry_price"], 51.0)
        self.assertEqual(bot.trade_log._skipped_entries, 0)
        self.assertEqual(len(bot.trade_log.trades), 2)
        self.assertEqual(bot.trade_log.trades[0].entry_signal_time, signal_time)
        self.assertEqual(bot.trade_log.trades[0].entry_fill_time, fill_time)

    def test_open_two_lots_skips_when_post_signal_snapshot_is_missing(self):
        signal_time = datetime(2026, 3, 20, 9, 20, 0)
        provider = Mock()
        provider.fetch_market_data.side_effect = lambda current_time, interval, limit: (
            make_market_df("2026-03-20 09:13:00", 8, "1min", 100.0)
            if interval == "1m"
            else make_market_df("2026-03-20 08:45:00", 8, "5min", 200.0)
        )
        provider.fetch_next_delta_snapshot.return_value = (None, [])

        bot = self._make_bot(provider, signal_time)
        intent = {
            "timestamp": "2026-03-20 09:20:00",
            "position_type": "A",
            "reason": {"section": "section1", "subcat": "a"},
            "spot": 22450.0,
            "strikes": {"pe_strike": 22400, "ce_strike_near": 22500, "pe_strike_upper": 22600},
            "legs": [
                {"tradingsymbol": "OPTPE", "action": "SELL", "option_type": "PE", "strike_price": 22400, "expiry": "2026-03-26", "last_price": 999.0},
            ],
            "_gate_checks": [],
        }

        bot._open_two_lots(intent)

    provider.fetch_next_delta_snapshot.assert_called_once_with(signal_time + timedelta(seconds=5))
        self.assertEqual(bot.position["lots"], {})
        self.assertEqual(bot.trade_log._skipped_entries, 1)
        self.assertEqual(len(bot.trade_log.trades), 0)

    def test_close_lot_uses_first_post_signal_snapshot(self):
        signal_time = datetime(2026, 3, 20, 9, 45, 0)
        fill_time = datetime(2026, 3, 20, 9, 45, 9)
        provider = Mock()
        provider.fetch_next_delta_snapshot.return_value = (
            fill_time,
            [{"tradingsymbol": "OPTPE", "ltp": 120.0}],
        )

        bot = self._make_bot(provider, signal_time)
        bot.position["lots"] = {
            "lot1": {
                "status": "OPEN",
                "timeframe": "1m",
                "opened_at": "2026-03-20 09:20:31",
                "sl_current_level": 110.0,
                "legs": [
                    {
                        "tradingsymbol": "OPTPE",
                        "side": "BUY",
                        "option_type": "PE",
                        "strike_price": 22400,
                        "expiry": "2026-03-26",
                        "entry_price": 100.0,
                    }
                ],
                "meta": {},
            }
        }
        bot._active_trade_ids["lot1"] = "trade1"
        bot.trade_log.record_entry(
            Trade(
                trade_id="trade1",
                entry_time=datetime(2026, 3, 20, 9, 20, 31),
                entry_signal_time=datetime(2026, 3, 20, 9, 20, 0),
                entry_fill_time=datetime(2026, 3, 20, 9, 20, 31),
                lot_id="lot1",
                position_type="A",
                entry_price_total=-5000.0,
            )
        )

        bot._close_lot("lot1", exit_time="2026-03-20 09:45:00")

        lot1 = bot.position["lots"]["lot1"]
        closed_trade = bot.trade_log.get_closed_trades()[0]
    provider.fetch_next_delta_snapshot.assert_called_once_with(signal_time + timedelta(seconds=5))
        self.assertEqual(lot1["closed_at"], "2026-03-20 09:45:09")
        self.assertEqual(lot1["legs"][0]["exit_price"], 120.0)
        self.assertEqual(closed_trade.exit_signal_time, signal_time)
        self.assertEqual(closed_trade.exit_fill_time, fill_time)
        self.assertEqual(closed_trade.pnl, 1000.0)

    def test_close_lot_raises_when_target_fill_snapshot_is_missing(self):
        signal_time = datetime(2026, 3, 20, 9, 45, 0)
        provider = Mock()
        provider.fetch_next_delta_snapshot.return_value = (None, [])

        bot = self._make_bot(provider, signal_time)
        bot.position["lots"] = {
            "lot1": {
                "status": "OPEN",
                "timeframe": "1m",
                "opened_at": "2026-03-20 09:20:31",
                "sl_current_level": 110.0,
                "legs": [
                    {
                        "tradingsymbol": "OPTPE",
                        "side": "BUY",
                        "option_type": "PE",
                        "strike_price": 22400,
                        "expiry": "2026-03-26",
                        "entry_price": 100.0,
                    }
                ],
                "meta": {},
            }
        }

        with self.assertRaises(StrictBacktestDataError):
            bot._close_lot("lot1", exit_time="2026-03-20 09:45:00")
        provider.fetch_next_delta_snapshot.assert_called_once_with(signal_time + timedelta(seconds=5))


if __name__ == "__main__":
    unittest.main()
