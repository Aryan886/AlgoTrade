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
from backtesting.exceptions import BacktestDataCoverageError, StrictBacktestDataError
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
        requested_effective_end = effective_end
        effective_end, truncation_messages = self._truncate_effective_end_for_supporting_data(
            effective_start,
            effective_end,
        )

        if effective_end < effective_start:
            joined = "\n".join(truncation_messages) if truncation_messages else "Supporting data does not cover any runnable window."
            raise BacktestDataCoverageError(joined)

        self._report_and_validate_data_coverage(effective_start, effective_end)

        if effective_end < requested_effective_end:
            print(
                "Truncating backtest end to the latest fully supported point-in-time: "
                f"{effective_end}"
            )
            for message in truncation_messages:
                print(f"  - {message}")

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

    def _report_and_validate_data_coverage(self, effective_start: datetime, effective_end: datetime) -> None:
        coverage = self.data_provider.get_backtest_data_coverage()
        if not coverage:
            print("Supporting data coverage: unavailable")
            return

        print("Supporting data coverage:")
        ordered_names = ["market_data_1m", "vix_data", "delta_cache", "option_data"]
        issues = []
        required_windows = self._required_coverage_windows(effective_start, effective_end)

        for table_name in ordered_names:
            info = coverage.get(table_name, {})
            rows = int(info.get("rows") or 0)
            start = info.get("start")
            end = info.get("end")
            required_window = required_windows.get(table_name)
            if required_window is None:
                status, warning_message, issue_message = "OPTIONAL", None, None
                required_display = "optional"
            else:
                required_start, required_end = required_window
                status, warning_message, issue_message = self._evaluate_coverage_status(
                    table_name,
                    start,
                    end,
                    required_start,
                    required_end,
                )
                required_display = f"{required_start} -> {required_end}"
            print(
                f"  - {table_name}: rows={rows}, available={self._format_range(start, end)}, "
                f"required={required_display}, status={status}"
            )
            if warning_message:
                print(f"WARNING: {warning_message}")
            if issue_message:
                issues.append(issue_message)

        if issues:
            raise BacktestDataCoverageError("\n".join(issues))

    def _truncate_effective_end_for_supporting_data(
        self,
        effective_start: datetime,
        effective_end: datetime,
    ) -> tuple[datetime, list[str]]:
        coverage = self.data_provider.get_backtest_data_coverage() or {}
        fill_headroom = self.bot.TARGET_FILL_LATENCY + timedelta(minutes=1)

        candidates: list[tuple[str, datetime, str]] = []

        market_end = coverage.get("market_data_1m", {}).get("end")
        if market_end is not None:
            candidates.append((
                "market_data_1m",
                market_end,
                "1m candles are only loaded through this timestamp.",
            ))

        vix_end = coverage.get("vix_data", {}).get("end")
        if vix_end is not None:
            candidates.append((
                "vix_data",
                vix_end,
                "fresh VIX snapshots are unavailable after this timestamp.",
            ))

        delta_end = coverage.get("delta_cache", {}).get("end")
        if delta_end is not None:
            candidates.append((
                "delta_cache",
                delta_end - fill_headroom,
                f"delta_cache needs post-signal fill headroom through {fill_headroom}.",
            ))

        if not candidates:
            return effective_end, []

        adjusted_end = min([effective_end] + [candidate_end for _, candidate_end, _ in candidates])
        if adjusted_end >= effective_end:
            return effective_end, []

        messages = []
        for table_name, candidate_end, detail in candidates:
            if candidate_end <= adjusted_end:
                messages.append(
                    f"{table_name} limits execution to {candidate_end} because {detail}"
                )

        return adjusted_end, messages

    def _evaluate_coverage_status(
        self,
        table_name: str,
        available_start: Optional[datetime],
        available_end: Optional[datetime],
        required_start: datetime,
        required_end: datetime,
    ) -> tuple[str, Optional[str], Optional[str]]:
        if available_start is None or available_end is None:
            return "MISSING", None, self._coverage_issue_message(
                table_name,
                available_start,
                available_end,
                required_start,
                required_end,
            )

        if available_end < required_end:
            return "MISSING", None, self._coverage_issue_message(
                table_name,
                available_start,
                available_end,
                required_start,
                required_end,
            )

        if table_name in {"vix_data", "delta_cache"}:
            if available_start > required_end:
                return "MISSING", None, self._coverage_issue_message(
                    table_name,
                    available_start,
                    available_end,
                    required_start,
                    required_end,
                )
            if available_start > required_start:
                return "WARNING", self._coverage_warning_message(
                    table_name,
                    available_start,
                    required_start,
                ), None
            return "OK", None, None

        if available_start > required_start:
            return "MISSING", None, self._coverage_issue_message(
                table_name,
                available_start,
                available_end,
                required_start,
                required_end,
            )

        return "OK", None, None

    @staticmethod
    def _format_range(start: Optional[datetime], end: Optional[datetime]) -> str:
        if start is None or end is None:
            return "no data"
        return f"{start} -> {end}"

    def _required_coverage_windows(
        self,
        effective_start: datetime,
        effective_end: datetime,
    ) -> dict[str, tuple[datetime, datetime]]:
        entry_cutoff_dt = datetime.combine(effective_end.date(), self.bot.ENTRY_CUTOFF_TIME)
        entry_required_end = min(effective_end, entry_cutoff_dt)
        fill_headroom = self.bot.TARGET_FILL_LATENCY + timedelta(minutes=1)
        fill_required_end = min(effective_end, entry_required_end + fill_headroom)
        session_open_dt = datetime.combine(effective_start.date(), self.config.trading_start)
        earliest_entry_signal = max(effective_start, session_open_dt + timedelta(minutes=1))
        fill_required_start = min(fill_required_end, earliest_entry_signal + self.bot.TARGET_FILL_LATENCY)

        return {
            "market_data_1m": (effective_start, effective_end),
            "vix_data": (effective_start, max(effective_start, entry_required_end)),
            "delta_cache": (fill_required_start, max(fill_required_start, fill_required_end)),
        }

    def _coverage_warning_message(
        self,
        table_name: str,
        available_start: datetime,
        required_start: datetime,
    ) -> str:
        if table_name == "vix_data":
            return (
                f"{table_name} coverage starts at {available_start}, after the preferred start {required_start}. "
                "Backtest will proceed; early entry checks may log 'VIX regime unavailable' until VIX data becomes available."
            )
        return (
            f"{table_name} coverage starts at {available_start}, after the preferred start {required_start}. "
            "Backtest will proceed; early entries may be skipped until supporting option snapshots become available."
        )

    def _coverage_issue_message(
        self,
        table_name: str,
        available_start: Optional[datetime],
        available_end: Optional[datetime],
        required_start: datetime,
        required_end: datetime,
    ) -> str:
        available_range = self._format_range(available_start, available_end)
        if available_start is not None and available_start > required_end:
            base = (
                f"{table_name} does not become available until {available_start}, which is after the required backtest window "
                f"{required_start} -> {required_end}. Available: {available_range}."
            )
        else:
            base = (
                f"{table_name} does not fully cover the required backtest window "
                f"{required_start} -> {required_end}. Available: {available_range}."
            )

        if table_name == "vix_data":
            return (
                f"{base} The strategy hard-blocks entries when VIX regime cannot be determined, "
                "so this coverage gap would otherwise show repeated "
                "'VIX data missing; cannot determine VIX regime.' warnings and produce zero trades."
            )
        if table_name == "delta_cache":
            return (
                f"{base} Strict contract validation, fill pricing, and option snapshots depend on delta_cache, "
                "so entries or exits can fail without it."
            )
        return base


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
