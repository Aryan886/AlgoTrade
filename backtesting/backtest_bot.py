"""
BacktestableBot - Bot wrapper for backtesting.

This class overrides data fetching methods to use HistoricalDataProvider
and logs trades to TradeLog for metrics calculation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional

import pandas as pd

from core.bot_nifty import NiftyPaperBot, LotSpec, _to_iso, _mark_to_market_price_map, _leg_pnl, _get_latest_sh, _latest_ts
from backtesting.data_provider import HistoricalDataProvider
from backtesting.backtest_strategy import BacktestableStrategy
from backtesting.metrics import Trade, TradeLog, NIFTY_LOT_SIZE, SLAdjustment
from backtesting.trade_logger import (
    BacktestTradeLogger,
    NullTradeLogger,
    EntryContext,
    ExitCheckContext,
    ExitContext,
    SLTrailContext,
    GateCheckResult,
)


class BacktestableBot(NiftyPaperBot):
    """
    Subclass of NiftyPaperBot that uses HistoricalDataProvider for data.

    Key changes:
    - Uses injected data provider instead of live DB queries
    - Tracks trades in a TradeLog
    - Calculates P&L using historical options prices
    - No file I/O for position persistence
    """

    def __init__(
        self,
        data_provider: HistoricalDataProvider,
        current_time_fn: Callable[[], datetime],
        trade_log: TradeLog,
        symbol: str = "NIFTY50",
        debug_logger: Optional[BacktestTradeLogger] = None,
    ) -> None:
        """
        Args:
            data_provider: HistoricalDataProvider instance with loaded data
            current_time_fn: Callable that returns the current simulated time
            trade_log: TradeLog instance for recording trades
            symbol: Trading symbol (default: NIFTY50)
            debug_logger: Optional BacktestTradeLogger for rich debugging
        """
        self._data_provider = data_provider
        self._current_time_fn = current_time_fn
        self.trade_log = trade_log
        self._debug_logger = debug_logger or NullTradeLogger()

        # Don't call parent __init__ directly; set up manually
        self.symbol = symbol
        self.position_file = ""  # No file persistence
        self.dry_run = False
        self.position: Dict[str, Any] = {"symbol": symbol, "lots": {}, "meta": {}}

        # Create backtestable strategy
        self.strategy = BacktestableStrategy(
            data_provider=data_provider,
            current_time_fn=current_time_fn,
            symbol=symbol,
            state_file=None,  # In-memory state
            debug_logger=self._debug_logger,
        )

        self.lots = [
            LotSpec(lot_id="lot1", timeframe="1m", sl_lookback=7),
            LotSpec(lot_id="lot2", timeframe="5m", sl_lookback=7),
        ]

        # Use null loggers for backtesting
        self.logger = logging.getLogger("backtest.bot")
        self.trade_logger = self.logger
        self.position_logger = self.logger

        # Track active trade IDs for logging
        self._active_trade_ids: Dict[str, str] = {}
        # Track lots that will exit due to SL hit
        self._pending_sl_exit: set = set()

    def _get_df(self, interval: str, limit: int = 400) -> pd.DataFrame:
        """Override to use historical data provider with time-gating."""
        current_time = self._current_time_fn()
        return self._data_provider.fetch_market_data(
            current_time=current_time,
            interval=interval,
            limit=limit,
        )

    def _fetch_options_data(self) -> List[Dict[str, Any]]:
        """Override to use historical options data."""
        current_time = self._current_time_fn()
        return self._data_provider.fetch_delta_data(current_time)

    def _now(self) -> datetime:
        return self._current_time_fn()

    def save_position(self) -> None:
        """Override to prevent file I/O during backtesting."""
        pass

    def _load_position(self) -> Dict[str, Any]:
        """Override to always start with empty position."""
        return {"symbol": self.symbol, "lots": {}, "meta": {}}

    def _open_two_lots(self, intent: Dict[str, Any]) -> None:
        """Override to log trade to TradeLog after opening."""
        # Call parent implementation
        super()._open_two_lots(intent)

        # Log the trade entry
        current_time = self._current_time_fn()
        legs = intent.get("legs") or []

        # Calculate total entry premium
        entry_total = 0.0
        for leg in legs:
            price = leg.get("last_price") or 0.0
            action = (leg.get("action") or "").upper()
            if action == "BUY":
                entry_total -= price * NIFTY_LOT_SIZE
            else:
                entry_total += price * NIFTY_LOT_SIZE

        # Create trade records for each lot
        for lot_id in ["lot1", "lot2"]:
            lot = (self.position.get("lots") or {}).get(lot_id)
            if lot and lot.get("status") == "OPEN":
                trade_id = str(uuid.uuid4())[:8]
                self._active_trade_ids[lot_id] = trade_id

                trade = Trade(
                    trade_id=trade_id,
                    entry_time=current_time,
                    lot_id=lot_id,
                    position_type=intent.get("position_type", ""),
                    legs=lot.get("legs", []),
                    entry_price_total=entry_total,
                    reason=intent.get("reason", {}),
                    meta={
                        "spot": intent.get("spot"),
                        "strikes": intent.get("strikes"),
                    },
                )
                self.trade_log.record_entry(trade)

                # Record initial SL
                initial_sl = float(lot.get("sl_current_level") or lot.get("sl_init_level") or 0.0)
                if initial_sl:
                    self.trade_log.record_sl_adjustment(
                        trade_id=trade_id,
                        timestamp=current_time,
                        old_sl=0.0,
                        new_sl=initial_sl,
                        sh_value=None,
                        close_price=float(intent.get("spot") or 0.0),
                        trigger="INIT",
                    )

        # Log entry to debug logger
        lot1 = (self.position.get("lots") or {}).get("lot1", {})
        lot2 = (self.position.get("lots") or {}).get("lot2", {})
        reason = intent.get("reason", {})
        self._debug_logger.log_entry_triggered(EntryContext(
            timestamp=current_time,
            section=reason.get("section", ""),
            subcategory=reason.get("subcat"),
            position_type=intent.get("position_type", ""),
            gate_checks=intent.get("_gate_checks", []),
            spot_price=float(intent.get("spot") or 0.0),
            legs=legs,
            lot1_sl=float(lot1.get("sl_current_level") or 0.0),
            lot2_sl=float(lot2.get("sl_current_level") or 0.0),
        ))

    def _trail_sl_for_lot(self, lot_id: str, timeframe: Literal["1m", "5m"], df_tf: pd.DataFrame) -> None:
        """Override to capture SL trail events."""
        lot = (self.position.get("lots") or {}).get(lot_id)
        if not lot or lot.get("status") != "OPEN":
            return

        # Capture before state
        old_sl = float(lot.get("sl_current_level") or 0.0)

        # Get current close price and SH before parent call
        try:
            close_price = float(df_tf.iloc[-1]["close"])
        except (IndexError, KeyError):
            close_price = 0.0
        new_sh = _get_latest_sh(df_tf)

        # Call parent implementation
        super()._trail_sl_for_lot(lot_id, timeframe, df_tf)

        # Capture after state
        lot = (self.position.get("lots") or {}).get(lot_id)
        if not lot:
            return
        new_sl = float(lot.get("sl_current_level") or 0.0)

        # Record SL adjustment if there was a change
        trade_id = self._active_trade_ids.get(lot_id)
        if trade_id and old_sl != new_sl:
            current_time = self._current_time_fn()
            self.trade_log.record_sl_adjustment(
                trade_id=trade_id,
                timestamp=current_time,
                old_sl=old_sl,
                new_sl=new_sl,
                sh_value=float(new_sh) if new_sh is not None else None,
                close_price=close_price,
                trigger="TRAIL",
            )

            # Log to debug logger
            self._debug_logger.log_sl_trail(SLTrailContext(
                timestamp=current_time,
                lot_id=lot_id,
                timeframe=timeframe,
                old_sl=old_sl,
                new_sl=new_sl,
                sh_value=float(new_sh) if new_sh is not None else None,
                close_price=close_price,
            ))

    def _exit_check_for_lot(self, lot_id: str, timeframe: Literal["1m", "5m"], df_tf: pd.DataFrame) -> None:
        """Override to track SL hit exits."""
        lot = (self.position.get("lots") or {}).get(lot_id)
        if not lot or lot.get("status") != "OPEN":
            return

        # Get close price and current SL before check
        try:
            close = float(df_tf.iloc[-1]["close"])
        except (IndexError, KeyError):
            close = 0.0

        sl = float(lot.get("sl_current_level") or 0.0)
        will_exit = sl and close > sl

        # Check min hold period
        from core.bot_nifty import _parse_ts
        opened_at = _parse_ts(lot.get("opened_at"))
        current_time = self._current_time_fn()
        min_hold_ok = True
        if opened_at is not None:
            min_hold_ok = current_time >= (opened_at + pd.Timedelta(minutes=5))

        # Log exit check to debug logger
        self._debug_logger.log_exit_check(ExitCheckContext(
            timestamp=current_time,
            lot_id=lot_id,
            timeframe=timeframe,
            candle_close=close,
            sl_level=sl,
            will_exit=bool(will_exit and min_hold_ok),
            min_hold_ok=min_hold_ok,
        ))

        # Mark that next exit for this lot is due to SL hit
        if will_exit:
            self._pending_sl_exit = self._pending_sl_exit if hasattr(self, '_pending_sl_exit') else set()
            self._pending_sl_exit.add(lot_id)

        # Call parent implementation
        super()._exit_check_for_lot(lot_id, timeframe, df_tf)

    def _close_lot(self, lot_id: str, exit_time: str) -> None:
        """Override to log trade exit to TradeLog."""
        lot = (self.position.get("lots") or {}).get(lot_id)
        if not lot or lot.get("status") != "OPEN":
            return

        # Get exit prices
        options_data = self._fetch_options_data()
        price_map = _mark_to_market_price_map(options_data or [])

        # Calculate P&L
        pnl = 0.0
        exit_total = 0.0
        legs_pnl = []  # For debug logging
        for leg in lot.get("legs") or []:
            side = (leg.get("side") or "BUY").upper()
            entry = float(leg.get("entry_price") or 0.0)
            current = self._resolve_leg_current_price(leg, price_map)
            leg["exit_price"] = current
            leg["exit_time"] = exit_time

            leg_pnl = _leg_pnl(side, entry, current) * NIFTY_LOT_SIZE
            pnl += leg_pnl

            if side == "BUY":
                exit_total += current * NIFTY_LOT_SIZE
            else:
                exit_total -= current * NIFTY_LOT_SIZE

            # Track for debug logging
            legs_pnl.append({
                "action": side,
                "option_type": leg.get("option_type", ""),
                "strike_price": leg.get("strike_price", ""),
                "entry_price": entry,
                "exit_price": current,
                "pnl": leg_pnl,
            })

        # Update lot status
        lot["status"] = "CLOSED"
        lot["closed_at"] = exit_time
        lot["pnl"] = pnl

        # Log to TradeLog
        trade_id = self._active_trade_ids.get(lot_id)
        if trade_id:
            current_time = self._current_time_fn()
            # Determine exit reason
            exit_reason = "SL_HIT" if lot_id in self._pending_sl_exit else "TIME_EXIT"
            self._pending_sl_exit.discard(lot_id)

            # Record final SL state before exit
            final_sl = float(lot.get("sl_current_level") or 0.0)
            self.trade_log.record_sl_adjustment(
                trade_id=trade_id,
                timestamp=current_time,
                old_sl=final_sl,
                new_sl=final_sl,
                sh_value=None,
                close_price=0.0,  # Not available here
                trigger="EXIT",
            )

            self.trade_log.record_exit(
                trade_id=trade_id,
                exit_time=current_time,
                exit_price_total=exit_total,
                pnl=pnl,
                exit_reason=exit_reason,
            )

            # Log to debug logger
            from core.bot_nifty import _parse_ts
            opened_at = _parse_ts(lot.get("opened_at"))
            entry_time = opened_at.to_pydatetime() if opened_at else None

            # Get last close price for logging
            try:
                df_1m = self._get_df("1m", limit=1)
                candle_close = float(df_1m.iloc[-1]["close"]) if not df_1m.empty else 0.0
            except Exception:
                candle_close = 0.0

            self._debug_logger.log_exit_triggered(ExitContext(
                timestamp=current_time,
                lot_id=lot_id,
                exit_reason=exit_reason,
                sl_level=final_sl,
                candle_close=candle_close,
                pnl=pnl,
                entry_time=entry_time,
                legs_pnl=legs_pnl,
            ))

            del self._active_trade_ids[lot_id]

    def reset_position(self) -> None:
        """Reset position state for a new backtest."""
        self.position = {"symbol": self.symbol, "lots": {}, "meta": {}}
        self._active_trade_ids = {}
        self._pending_sl_exit = set()

    def get_unrealized_pnl(self) -> float:
        """Calculate unrealized P&L for open positions."""
        unrealized = 0.0
        options_data = self._fetch_options_data()
        price_map = _mark_to_market_price_map(options_data or [])

        for lot_id in ["lot1", "lot2"]:
            lot = (self.position.get("lots") or {}).get(lot_id)
            if lot and lot.get("status") == "OPEN":
                for leg in lot.get("legs") or []:
                    side = (leg.get("side") or "BUY").upper()
                    entry = float(leg.get("entry_price") or 0.0)
                    current = self._resolve_leg_current_price(leg, price_map)
                    leg_pnl = _leg_pnl(side, entry, current) * NIFTY_LOT_SIZE
                    unrealized += leg_pnl

        return unrealized

    def _lookup_held_leg_price(self, leg: Dict[str, Any]) -> Optional[float]:
        current_time = self._current_time_fn()
        return self._data_provider.fetch_option_price(
            current_time=current_time,
            tradingsymbol=leg.get("tradingsymbol") or "",
            strike_price=leg.get("strike_price"),
            option_type=leg.get("option_type") or "",
            expiry=leg.get("expiry") or "",
        )
