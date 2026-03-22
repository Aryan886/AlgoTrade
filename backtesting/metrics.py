"""
Trade logging and performance metrics calculation for backtesting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd

NIFTY_LOT_SIZE = 50


@dataclass
class SLAdjustment:
    """Single stop loss adjustment event."""
    timestamp: datetime
    old_sl: float
    new_sl: float
    sh_value: Optional[float]
    close_price: float
    trigger: Literal["INIT", "TRAIL", "EXIT"]


@dataclass
class Trade:
    """Represents a single trade (entry to exit)."""
    trade_id: str
    entry_time: datetime
    exit_time: Optional[datetime] = None
    lot_id: str = ""
    position_type: str = ""  # "A" or "B"
    legs: List[Dict[str, Any]] = field(default_factory=list)
    entry_price_total: float = 0.0  # Net premium paid/received
    exit_price_total: Optional[float] = None
    pnl: Optional[float] = None
    reason: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    # Stop loss tracking
    sl_initial: float = 0.0
    sl_final: float = 0.0
    sl_history: List[SLAdjustment] = field(default_factory=list)
    exit_reason: str = ""  # "SL_HIT" or "TIME_EXIT" or ""

    @property
    def sl_adjustment_count(self) -> int:
        """Count of actual SL changes (excluding init and unchanged events)."""
        return len([h for h in self.sl_history if h.trigger == "TRAIL" and h.old_sl != h.new_sl])

    @property
    def total_sl_movement(self) -> float:
        """Total points SL moved from initial to final (positive = tightened)."""
        return self.sl_initial - self.sl_final if self.sl_initial else 0.0

    @property
    def sl_movement_pct(self) -> float:
        """Percentage SL moved from initial."""
        return (self.total_sl_movement / self.sl_initial * 100) if self.sl_initial else 0.0


class TradeLog:
    """Records all trades with timestamps, prices, and P&L."""

    def __init__(self) -> None:
        self.trades: List[Trade] = []
        self._equity_curve: List[Tuple[datetime, float]] = []
        self._initial_capital: float = 100000.0
        self._realized_pnl: float = 0.0

    def record_entry(self, trade: Trade) -> None:
        """Record a new trade entry."""
        self.trades.append(trade)

    def record_exit(
        self,
        trade_id: str,
        exit_time: datetime,
        exit_price_total: float,
        pnl: float,
        exit_reason: str = "",
    ) -> None:
        """Update an existing trade with exit information."""
        for trade in self.trades:
            if trade.trade_id == trade_id:
                trade.exit_time = exit_time
                trade.exit_price_total = exit_price_total
                trade.pnl = pnl
                trade.exit_reason = exit_reason
                self._realized_pnl += pnl
                break

    def record_sl_adjustment(
        self,
        trade_id: str,
        timestamp: datetime,
        old_sl: float,
        new_sl: float,
        sh_value: Optional[float],
        close_price: float,
        trigger: Literal["INIT", "TRAIL", "EXIT"],
    ) -> None:
        """Record a stop loss adjustment event for a trade."""
        for trade in self.trades:
            if trade.trade_id == trade_id:
                trade.sl_history.append(SLAdjustment(
                    timestamp=timestamp,
                    old_sl=old_sl,
                    new_sl=new_sl,
                    sh_value=sh_value,
                    close_price=close_price,
                    trigger=trigger,
                ))
                # Update sl_final to track current SL level
                trade.sl_final = new_sl
                # Set initial SL on first event
                if trigger == "INIT":
                    trade.sl_initial = new_sl
                break

    def update_equity(self, timestamp: datetime, unrealized_pnl: float = 0.0) -> None:
        """Update the equity curve at a given timestamp."""
        equity = self._initial_capital + self._realized_pnl + unrealized_pnl
        self._equity_curve.append((timestamp, equity))

    def get_open_trades(self) -> List[Trade]:
        """Get all currently open trades."""
        return [t for t in self.trades if t.pnl is None]

    def get_closed_trades(self) -> List[Trade]:
        """Get all closed trades."""
        return [t for t in self.trades if t.pnl is not None]

    def to_dataframe(self) -> pd.DataFrame:
        """Convert trades to a DataFrame for analysis."""
        records = []
        for t in self.trades:
            records.append({
                "trade_id": t.trade_id,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "lot_id": t.lot_id,
                "position_type": t.position_type,
                "entry_price": t.entry_price_total,
                "exit_price": t.exit_price_total,
                "pnl": t.pnl,
                "section": t.reason.get("section"),
                "subcat": t.reason.get("subcat"),
                "sl_initial": t.sl_initial,
                "sl_final": t.sl_final,
                "sl_adjustments": t.sl_adjustment_count,
                "sl_movement_pct": t.sl_movement_pct,
                "exit_reason": t.exit_reason,
            })
        return pd.DataFrame(records)


@dataclass
class BacktestResult:
    """Contains all metrics and data from a backtest run."""
    total_pnl: float = 0.0
    num_trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    expectancy: float = 0.0
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Tuple[datetime, float]] = field(default_factory=list)

    def summary(self) -> str:
        """Return a formatted summary of backtest results."""
        return f"""
Backtest Results Summary
========================
Total P&L:       {self.total_pnl:,.2f}
Number of Trades: {self.num_trades}
Win Rate:        {self.win_rate:.2%}
Avg Win:         {self.avg_win:,.2f}
Avg Loss:        {self.avg_loss:,.2f}
Profit Factor:   {self.profit_factor:.2f}
Max Drawdown:    {self.max_drawdown:,.2f} ({self.max_drawdown_pct:.2%})
Sharpe Ratio:    {self.sharpe_ratio:.2f}
Expectancy:      {self.expectancy:,.2f}
"""


class MetricsCalculator:
    """Computes performance statistics from trade log."""

    def __init__(self, trade_log: TradeLog, risk_free_rate: float = 0.065) -> None:
        self.trade_log = trade_log
        self.risk_free_rate = risk_free_rate

    def compute(self) -> BacktestResult:
        """Compute all metrics from the trade log."""
        trades = self.trade_log.trades
        closed = [t for t in trades if t.pnl is not None]

        if not closed:
            return self._empty_result()

        # Basic metrics
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        total_pnl = sum(t.pnl for t in closed)

        win_rate = len(wins) / len(closed) if closed else 0.0
        avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0

        # Profit factor
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Max drawdown
        max_dd, max_dd_pct = self._calculate_drawdown()

        # Sharpe ratio
        sharpe = self._calculate_sharpe()

        # Expectancy
        loss_rate = 1.0 - win_rate
        expectancy = (win_rate * avg_win) - (loss_rate * abs(avg_loss))

        return BacktestResult(
            total_pnl=total_pnl,
            num_trades=len(closed),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            sharpe_ratio=sharpe,
            expectancy=expectancy,
            trades=trades,
            equity_curve=self.trade_log._equity_curve,
        )

    def _empty_result(self) -> BacktestResult:
        """Return an empty result when no trades."""
        return BacktestResult(
            trades=self.trade_log.trades,
            equity_curve=self.trade_log._equity_curve,
        )

    def _calculate_drawdown(self) -> Tuple[float, float]:
        """Calculate max drawdown from equity curve."""
        curve = self.trade_log._equity_curve
        if not curve:
            return 0.0, 0.0

        equities = [eq for _, eq in curve]
        if not equities:
            return 0.0, 0.0

        peak = equities[0]
        max_dd = 0.0
        max_dd_pct = 0.0

        for eq in equities:
            if eq > peak:
                peak = eq
            dd = peak - eq
            dd_pct = dd / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
            max_dd_pct = max(max_dd_pct, dd_pct)

        return max_dd, max_dd_pct

    def _calculate_sharpe(self) -> float:
        """Calculate annualized Sharpe ratio."""
        curve = self.trade_log._equity_curve
        if len(curve) < 2:
            return 0.0

        equities = pd.Series([eq for _, eq in curve])
        returns = equities.pct_change().dropna()

        if returns.empty or returns.std() == 0:
            return 0.0

        # Assume 1-minute data, ~375 trading minutes per day, ~252 trading days
        annualization = math.sqrt(375 * 252)
        excess_return = returns.mean() - (self.risk_free_rate / (375 * 252))

        return (excess_return / returns.std()) * annualization


def calculate_position_pnl(
    legs: List[Dict[str, Any]],
    entry_prices: Dict[str, float],
    exit_prices: Dict[str, float],
) -> float:
    """
    Calculate net P&L for a multi-leg position.

    For SELL legs: profit = entry_price - exit_price
    For BUY legs:  profit = exit_price - entry_price

    Multiply by lot size (NIFTY lot size = 50)
    """
    total_pnl = 0.0

    for leg in legs:
        symbol = leg.get("tradingsymbol")
        side = (leg.get("side") or leg.get("action") or "").upper()
        entry = entry_prices.get(symbol, 0.0)
        exit_price = exit_prices.get(symbol, 0.0)

        if side == "BUY":
            leg_pnl = (exit_price - entry) * NIFTY_LOT_SIZE
        else:  # SELL
            leg_pnl = (entry - exit_price) * NIFTY_LOT_SIZE

        total_pnl += leg_pnl

    return total_pnl
