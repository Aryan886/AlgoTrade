import importlib
import unittest
from datetime import datetime
from unittest.mock import Mock, patch


def _logger_tuple(logger: Mock):
    return (logger, logger, logger, logger, logger, logger)


class PaperTradesManagementTests(unittest.TestCase):
    def _load_module(self):
        logger = Mock()
        with patch("utils.utility.setup_paper_trading_logger", return_value=_logger_tuple(logger)):
            module = importlib.import_module("core.paper_trades")
            module = importlib.reload(module)
        return module, logger

    def _build_position(self):
        return {
            "ce_symbol": "NIFTY2099043023900CE",
            "pe_symbol": "NIFTY2099043023800PE",
            "entry_prices": {"ce": 120.0, "CE": 120.0, "pe": 110.0, "PE": 110.0},
            "current_prices": {"ce": 100.0, "CE": 100.0, "pe": 90.0, "PE": 90.0},
            "ce_closed": False,
            "pe_closed": False,
            "ce_effectively_closed": False,
            "pe_effectively_closed": False,
            "replacement_cycles": 0,
            "waiting_for_replacement": False,
            "last_replacement_attempt": None,
        }

    def test_manage_existing_positions_continues_with_unchanged_fresh_quotes(self):
        module, logger = self._load_module()

        with patch.object(module, "setup_paper_trading_logger", return_value=_logger_tuple(logger)), \
             patch.object(module.os.path, "exists", return_value=False):
            trader = module.PaperTraderDonchian("NIFTY50")

        trader.position = self._build_position()
        snapshot_timestamp = datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        trader.latest_delta_snapshot = {
            "timestamp": snapshot_timestamp,
            "options_data": [
                {
                    "tradingsymbol": trader.position["ce_symbol"],
                    "ltp": 100.0,
                    "option_type": "CE",
                    "expiry": "2099-04-30",
                },
                {
                    "tradingsymbol": trader.position["pe_symbol"],
                    "ltp": 90.0,
                    "option_type": "PE",
                    "expiry": "2099-04-30",
                },
            ],
            "missing_required_contracts": [],
        }

        with patch.object(trader, "refresh_all_data", return_value=True), \
             patch.object(trader, "is_position_expired", return_value=False), \
             patch.object(trader, "should_close_for_expiry", return_value=False), \
             patch.object(trader, "should_check_risk", return_value=True), \
             patch.object(trader, "check_vix_breach", return_value=False), \
             patch.object(trader, "check_entry_criteria_violation", return_value=False), \
             patch.object(trader, "check_positon_adjustment") as check_adjustment:
            trader.manage_existing_positions()

        self.assertGreaterEqual(check_adjustment.call_count, 1)
        warning_text = " | ".join(str(call.args[0]) for call in logger.warning.call_args_list)
        self.assertNotIn("Skipping position management - stale/no price data", warning_text)

    def test_manage_existing_positions_skips_when_active_symbols_are_missing(self):
        module, logger = self._load_module()

        with patch.object(module, "setup_paper_trading_logger", return_value=_logger_tuple(logger)), \
             patch.object(module.os.path, "exists", return_value=False):
            trader = module.PaperTraderDonchian("NIFTY50")

        trader.position = self._build_position()
        snapshot_timestamp = datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        trader.latest_delta_snapshot = {
            "timestamp": snapshot_timestamp,
            "options_data": [
                {
                    "tradingsymbol": "NIFTY2099033022900CE",
                    "ltp": 55.0,
                    "option_type": "CE",
                    "expiry": "2099-03-30",
                }
            ],
            "missing_required_contracts": [
                {"tradingsymbol": trader.position["ce_symbol"], "option_type": "CE"},
                {"tradingsymbol": trader.position["pe_symbol"], "option_type": "PE"},
            ],
        }

        with patch.object(trader, "refresh_all_data", return_value=True), \
             patch.object(trader, "is_position_expired", return_value=False), \
             patch.object(trader, "should_close_for_expiry", return_value=False), \
             patch.object(trader, "should_check_risk", return_value=True), \
             patch.object(trader, "check_vix_breach", return_value=False), \
             patch.object(trader, "check_entry_criteria_violation", return_value=False), \
             patch.object(trader, "check_positon_adjustment") as check_adjustment:
            trader.manage_existing_positions()

        check_adjustment.assert_not_called()
        warning_text = " | ".join(str(call.args[0]) for call in logger.warning.call_args_list)
        self.assertIn("Latest delta snapshot still missing active contracts", warning_text)
        self.assertIn("Skipping position management - stale/no price data", warning_text)

    def test_refresh_and_signal_gate_still_work_without_active_position(self):
        module, logger = self._load_module()

        with patch.object(module, "setup_paper_trading_logger", return_value=_logger_tuple(logger)), \
             patch.object(module.os.path, "exists", return_value=False):
            trader = module.PaperTraderDonchian("NIFTY50")

        snapshot_timestamp = datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        with patch.object(module, "fetch_latest_delta_snapshot", return_value={
            "timestamp": snapshot_timestamp,
            "options_data": [{"tradingsymbol": "NIFTY2099033022900CE", "ltp": 55.0, "option_type": "CE", "expiry": "2099-03-30"}],
            "missing_required_contracts": [],
        }):
            self.assertTrue(trader.refresh_all_data())

        trader.position = None
        trader.last_signal_attempt = None
        self.assertTrue(trader.should_attempt_new_signal())


if __name__ == "__main__":
    unittest.main()
