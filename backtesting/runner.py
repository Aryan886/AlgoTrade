"""
BacktestRunner - Main orchestrator for backtesting.

Advances a time cursor through historical data, calling bot.run_once() at each step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, time as dtime
from typing import Optional

from backtesting.data_provider import HistoricalDataProvider
from backtesting.backtest_bot import BacktestableBot
from backtesting.exceptions import StrictBacktestDataError
from backtesting.metrics import TradeLog, MetricsCalculator, BacktestResult
from backtesting.trade_logger import BacktestTradeLogger, NullTradeLogger


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""
    start_date: datetime
    end_date: datetime
    symbol: str = "NIFTY50"
    db_path: str = "db/trading_bot.db"
    step_interval: timedelta = field(default_factory=lambda: timedelta(minutes=1))
    trading_start: dtime = field(default_factory=lambda: dtime(9, 15))
    trading_end: dtime = field(default_factory=lambda: dtime(15, 30))
    equity_update_interval: int = 5  # Update equity curve every N minutes
    verbose: bool = False
    debug_log_path: Optional[str] = None  # Path for rich text debug log file


class BacktestRunner:
    """
    Main orchestrator for backtesting.

    Advances a time cursor minute-by-minute through historical data,
    calling bot.run_once() at each step.
    """

    def __init__(self, config: BacktestConfig) -> None:
        """
        Args:
            config: BacktestConfig with backtest parameters
        """
        self.config = config
        self._current_time: datetime = config.start_date
        self.trade_log = TradeLog()

        # Initialize debug trade logger
        if config.debug_log_path:
            self.trade_logger = BacktestTradeLogger(
                output_path=config.debug_log_path,
                enabled=True,
            )
        else:
            self.trade_logger = NullTradeLogger()

        # Initialize data provider
        self.data_provider = HistoricalDataProvider(
            db_path=config.db_path,
            symbol=config.symbol,
        )

        # Initialize bot with injected dependencies
        self.bot = BacktestableBot(
            data_provider=self.data_provider,
            current_time_fn=self.get_current_time,
            trade_log=self.trade_log,
            symbol=config.symbol,
            debug_logger=self.trade_logger,
        )

        self._steps_since_equity_update = 0

    def get_current_time(self) -> datetime:
        """Returns current simulated time."""
        return self._current_time

    def run(self) -> BacktestResult:
        """
        Execute the backtest.

        Returns:
            BacktestResult with all metrics and trade data
        """
        print(f"Loading data from {self.config.start_date} to {self.config.end_date}...")

        # Pre-load all historical data
        self.data_provider.load_all_data(
            self.config.start_date,
            self.config.end_date,
        )

        # Get available date range
        data_start, data_end = self.data_provider.get_available_date_range()
        if data_start is None:
            print("Error: No data available in the specified range.")
            return self._compute_results()

        print(f"Data available from {data_start} to {data_end}")

        # Adjust start/end if needed
        effective_start = max(self.config.start_date, data_start) if data_start else self.config.start_date
        effective_end = min(self.config.end_date, data_end) if data_end else self.config.end_date

        print(f"Running backtest from {effective_start} to {effective_end}...")

        # Get trading days
        trading_days = self.data_provider.get_trading_days(effective_start, effective_end)
        if not trading_days:
            print("Warning: No trading days found in the specified range.")
            return self._compute_results()

        print(f"Found {len(trading_days)} trading days")

        total_steps = 0
        for day in trading_days:
            day_steps = self._run_trading_day(day)
            total_steps += day_steps

            if self.config.verbose:
                print(f"  {day.date()}: {day_steps} steps, {len(self.trade_log.trades)} trades so far")

        print(f"Backtest complete: {total_steps} steps, {len(self.trade_log.trades)} trades")

        # Close debug logger
        self.trade_logger.close()

        # Compute final metrics
        return self._compute_results()

    def _run_trading_day(self, day: datetime) -> int:
        """
        Run simulation for a single trading day.

        Args:
            day: The trading day date

        Returns:
            Number of steps executed
        """
        # Reset strategy state for new session (mimics 09:15 reset)
        self.bot.strategy.reset_state()

        # Set up trading hours
        day_start = datetime.combine(day.date(), self.config.trading_start)
        day_end = datetime.combine(day.date(), self.config.trading_end)

        current = day_start
        steps = 0

        while current <= day_end:
            self._current_time = current

            # Run bot cycle
            try:
                self.bot.run_once()
            except StrictBacktestDataError:
                raise
            except Exception as e:
                if self.config.verbose:
                    print(f"    Error at {current}: {e}")

            # Update equity curve periodically
            self._steps_since_equity_update += 1
            if self._steps_since_equity_update >= self.config.equity_update_interval:
                unrealized = self.bot.get_unrealized_pnl()
                self.trade_log.update_equity(current, unrealized)
                self._steps_since_equity_update = 0

            current += self.config.step_interval
            steps += 1

        return steps

    def _is_trading_time(self, dt: datetime) -> bool:
        """Check if within trading hours."""
        t = dt.time()
        return self.config.trading_start <= t <= self.config.trading_end

    def _compute_results(self) -> BacktestResult:
        """Compute performance metrics from trade log."""
        calculator = MetricsCalculator(self.trade_log)
        return calculator.compute()


def run_backtest(
    start_date: datetime,
    end_date: datetime,
    db_path: str = "db/trading_bot.db",
    symbol: str = "NIFTY50",
    verbose: bool = False,
) -> BacktestResult:
    """
    Convenience function to run a backtest.

    Args:
        start_date: Start of backtest period
        end_date: End of backtest period
        db_path: Path to SQLite database
        symbol: Trading symbol
        verbose: Print progress details

    Returns:
        BacktestResult with all metrics and trade data
    """
    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        db_path=db_path,
        symbol=symbol,
        verbose=verbose,
    )

    runner = BacktestRunner(config)
    return runner.run()
