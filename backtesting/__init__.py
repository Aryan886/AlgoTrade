"""
Backtesting module for NIFTY options strategy.

This module provides point-in-time historical data simulation to prevent look-ahead bias.
"""

from backtesting.data_provider import HistoricalDataProvider
from backtesting.metrics import Trade, TradeLog, MetricsCalculator, BacktestResult
from backtesting.runner import BacktestRunner, BacktestConfig

__all__ = [
    "HistoricalDataProvider",
    "Trade",
    "TradeLog",
    "MetricsCalculator",
    "BacktestResult",
    "BacktestRunner",
    "BacktestConfig",
]
