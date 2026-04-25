import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.market_data_automation import MarketDataAutomation


class MarketDataAutomationOITests(unittest.TestCase):
    def _build_automation(self):
        with patch("utils.market_data_automation.PaperTraderDonchian", return_value=Mock()), \
             patch("utils.market_data_automation.PaperTraderSMA", return_value=Mock()), \
             patch("utils.market_data_automation.EquityPaperTrader", return_value=Mock()), \
             patch("utils.market_data_automation.NiftyPaperBot", return_value=Mock()), \
             patch("utils.market_data_automation.OIExpiryPaperBot", return_value=Mock()):
            return MarketDataAutomation()

    def test_tuesday_routing_runs_oi_bot_and_skips_standard_nifty_bot(self):
        automation = self._build_automation()
        automation.is_market_open = Mock(return_value=True)
        automation.use_oi_nifty_strategy = Mock(return_value=True)
        automation.oi_nifty_trader.get_status_summary.return_value = {
            "type_a": {"position_1_status": "FLAT", "position_2_status": "FLAT"},
            "type_b": {"position_status": "FLAT"},
        }

        automation.run_nifty_paper_trading_cycle()

        automation.oi_nifty_trader.run_once.assert_called_once()
        automation.nifty_trader.run_once.assert_not_called()

    def test_non_tuesday_routing_runs_standard_nifty_bot_and_skips_oi_bot(self):
        automation = self._build_automation()
        automation.is_market_open = Mock(return_value=True)
        automation.use_oi_nifty_strategy = Mock(return_value=False)
        automation.nifty_trader.all_lots_flat.return_value = True

        automation.run_nifty_paper_trading_cycle()

        automation.nifty_trader.run_once.assert_called_once()
        automation.oi_nifty_trader.run_once.assert_not_called()

    def test_tuesday_market_closed_runs_oi_safety_cleanup_only(self):
        automation = self._build_automation()
        automation.is_market_open = Mock(return_value=False)
        automation.use_oi_nifty_strategy = Mock(return_value=True)

        automation.run_nifty_paper_trading_cycle()

        automation.oi_nifty_trader.maybe_run_tuesday_safety_cleanup.assert_called_once()
        automation.oi_nifty_trader.run_once.assert_not_called()
        automation.nifty_trader.run_once.assert_not_called()


if __name__ == "__main__":
    unittest.main()
