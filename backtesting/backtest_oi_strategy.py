"""
Backtestable OI strategy adapter.

Provides point-in-time historical data access for OIExpiryStrategy while
keeping all live decision rules unchanged.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import core.strat_oi as strat_oi_module
from core.strat_oi import OIExpiryStrategy, build_default_oi_state

from backtesting.data_provider import HistoricalDataProvider


class BacktestableOIStrategy(OIExpiryStrategy):
    """Historical-data adapter for OIExpiryStrategy."""

    def __init__(
        self,
        data_provider: HistoricalDataProvider,
        current_time_fn: Callable[[], datetime],
        symbol: str = "NIFTY50",
    ) -> None:
        self._data_provider = data_provider
        self._current_time_fn = current_time_fn

        safe_logger = logging.getLogger("backtest.oi_strategy.init")
        original_logger_factory = strat_oi_module.setup_oi_logging
        strat_oi_module.setup_oi_logging = lambda: (
            safe_logger,
            safe_logger,
            safe_logger,
        )
        try:
            super().__init__(symbol=symbol)
        finally:
            strat_oi_module.setup_oi_logging = original_logger_factory

        self.logger = logging.getLogger("backtest.oi_strategy")
        self.state: Dict[str, Any] = build_default_oi_state()

    def _now(self) -> datetime:
        return self._current_time_fn()

    def _get_df(self, interval: str, limit: int = 300):
        return self._data_provider.fetch_market_data(
            current_time=self._current_time_fn(),
            interval=interval,
            limit=limit,
        )

    def _fetch_option_snapshot(self) -> List[Dict[str, Any]]:
        return self._data_provider.fetch_option_snapshot(self._current_time_fn())

    def _fetch_open_interest_snapshot(self, current_time=None) -> List[Dict[str, Any]]:
        resolved_time = current_time or self._current_time_fn()
        return self._data_provider.fetch_open_interest_snapshot(resolved_time)

    def _fetch_open_interest_5m_snapshot(self, current_time=None) -> List[Dict[str, Any]]:
        resolved_time = current_time or self._current_time_fn()
        return self._data_provider.fetch_open_interest_5m_snapshot(resolved_time)

    def reset_state(self) -> None:
        self.state = build_default_oi_state()
