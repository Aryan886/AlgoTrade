"""
BacktestableStrategy - Strategy wrapper for backtesting.

This class overrides data fetching methods to use HistoricalDataProvider
instead of live database queries.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from core.strat_nifty import NiftyOptionsStrategy
from backtesting.data_provider import HistoricalDataProvider


class BacktestableStrategy(NiftyOptionsStrategy):
    """
    Subclass of NiftyOptionsStrategy that uses HistoricalDataProvider for data.

    This ensures the strategy only sees data up to the current simulated time,
    preventing look-ahead bias in backtesting.
    """

    def __init__(
        self,
        data_provider: HistoricalDataProvider,
        current_time_fn: Callable[[], datetime],
        symbol: str = "NIFTY50",
        state_file: Optional[str] = None,
    ) -> None:
        """
        Args:
            data_provider: HistoricalDataProvider instance with loaded data
            current_time_fn: Callable that returns the current simulated time
            symbol: Trading symbol (default: NIFTY50)
            state_file: Path to state file (None for in-memory state)
        """
        self._data_provider = data_provider
        self._current_time_fn = current_time_fn

        # Initialize parent with a dummy state file
        # We'll override state management for backtesting
        super().__init__(symbol=symbol, state_file=state_file or "")

        # Override to use in-memory state for backtest isolation
        if state_file is None:
            self.state = self._default_state()

        # Use a null logger to reduce noise during backtesting
        self.logger = logging.getLogger("backtest.strategy")
        self.trade_logger = self.logger
        self.position_logger = self.logger

    def _fetch_market_data(self, interval: str, limit: Optional[int]) -> pd.DataFrame:
        """Override to use historical data provider with time-gating."""
        current_time = self._current_time_fn()
        return self._data_provider.fetch_market_data(
            current_time=current_time,
            interval=interval,
            limit=limit or 300,
        )

    def _fetch_vix_data(self) -> pd.DataFrame:
        """Override to use historical VIX data."""
        current_time = self._current_time_fn()
        return self._data_provider.fetch_vix_data(current_time)

    def _fetch_options_data(self) -> List[Dict[str, Any]]:
        """Override to use historical options data."""
        current_time = self._current_time_fn()
        return self._data_provider.fetch_delta_data(current_time)

    def _now(self) -> datetime:
        return self._current_time_fn()

    def save_state(self) -> None:
        """Override to prevent file I/O during backtesting."""
        # In-memory state only; no persistence
        pass

    def _load_state(self) -> Dict[str, Any]:
        """Override to always start with fresh state."""
        return self._default_state()

    def reset_state(self) -> None:
        """Reset strategy state to default values."""
        self.state = self._default_state()
