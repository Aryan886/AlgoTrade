"""
BacktestableBot - Bot wrapper for backtesting.

This class overrides data fetching methods to use HistoricalDataProvider
and logs trades to TradeLog for metrics calculation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Literal, Optional

import pandas as pd

from core.bot_nifty import (
    NiftyPaperBot,
    LotSpec,
    _to_iso,
    _mark_to_market_price_map,
    _leg_pnl,
    _get_latest_sh,
    _latest_ts,
    _highest_high_last_n,
)
from backtesting.data_provider import HistoricalDataProvider
from backtesting.exceptions import StrictBacktestDataError
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

    TARGET_FILL_LATENCY = timedelta(seconds=5)

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

    def _next_fill_snapshot(self, signal_time: datetime, phase: str) -> tuple[datetime, List[Dict[str, Any]], Dict[str, float]]:
        """Fetch the first options snapshot at or after the target fill latency."""
        target_fill_time = signal_time + self.TARGET_FILL_LATENCY
        fill_time, options_data = self._data_provider.fetch_next_delta_snapshot(target_fill_time)
        if fill_time is None or not options_data:
            raise StrictBacktestDataError(
                f"Missing post-signal options snapshot for {phase} after target fill time {target_fill_time:%Y-%m-%d %H:%M:%S}"
            )
        return fill_time, options_data, _mark_to_market_price_map(options_data)

    def _strict_leg_prices(
        self,
        legs: List[Dict[str, Any]],
        price_map: Dict[str, float],
        fill_time: datetime,
        phase: str,
    ) -> Dict[str, float]:
        """Resolve strict per-leg prices from a single fill snapshot."""
        prices: Dict[str, float] = {}
        missing: List[str] = []

        for leg in legs:
            symbol = leg.get("tradingsymbol") or ""
            price = price_map.get(symbol)
            if price is None or pd.isna(price):
                missing.append(symbol or "<unknown>")
                continue
            prices[symbol] = float(price)

        if missing:
            raise StrictBacktestDataError(
                f"Missing {phase} price(s) at {fill_time:%Y-%m-%d %H:%M:%S} for: {', '.join(missing)}"
            )
        return prices

    def _open_two_lots(self, intent: Dict[str, Any]) -> None:
        """Override to log trade to TradeLog after opening."""
        if self._entry_cutoff_reached():
            self.logger.info("Skipping new Nifty entry because the 3:00 pm cutoff has been reached.")
            return

        if not self.all_lots_flat():
            self.logger.info("Skipping new Nifty entry because an existing position is still open.")
            return

        signal_time = self._current_time_fn()
        lots_obj: Dict[str, Any] = self.position.setdefault("lots", {})
        legs = intent.get("legs") or []
        if not legs:
            return

        df_1m = self._get_df("1m", limit=200)
        df_5m = self._get_df("5m", limit=200)
        if df_1m.empty or df_5m.empty:
            self.logger.warning("Cannot open lots: missing 1m/5m market data.")
            return

        try:
            entry_close = float(df_1m.iloc[-1]["close"])
        except Exception:
            self.logger.warning("Cannot open lots: missing 1m close for entry price reference.")
            return

        lot1_sl = _highest_high_last_n(df_1m, 7)
        lot2_sl = _highest_high_last_n(df_5m, 7)
        if lot1_sl is None or lot2_sl is None:
            self.logger.warning("Cannot open lots: insufficient candles for initial SL (need 7).")
            return

        try:
            fill_time, _, price_map = self._next_fill_snapshot(signal_time, "entry")
            strict_prices = self._strict_leg_prices(legs, price_map, fill_time, "entry")
        except StrictBacktestDataError as exc:
            message = str(exc)
            self.logger.warning(message)
            self.trade_log.record_skipped_entry(signal_time, message)
            self._debug_logger.log_entry_evaluation_blocked(
                signal_time,
                intent.get("_gate_checks", []),
                message,
            )
            return

        def normalize_leg(l: Dict[str, Any]) -> Dict[str, Any]:
            sym = l.get("tradingsymbol")
            side = (l.get("action") or "").upper()
            option_type = (l.get("option_type") or "").upper() or None
            strike_price = l.get("strike_price")
            expiry = l.get("expiry")
            entry_price = strict_prices.get(sym, 0.0)
            return {
                "tradingsymbol": sym,
                "side": side,
                "option_type": option_type,
                "strike_price": int(strike_price) if strike_price is not None else None,
                "expiry": expiry,
                "entry_price": entry_price,
                "last_price": entry_price,
                "exit_price": None,
                "exit_time": None,
            }

        norm_legs = [normalize_leg(l) for l in legs]

        entry_total = 0.0
        for leg in norm_legs:
            price = float(leg.get("entry_price") or 0.0)
            action = (leg.get("side") or "").upper()
            if action == "BUY":
                entry_total -= price * NIFTY_LOT_SIZE
            else:
                entry_total += price * NIFTY_LOT_SIZE

        for spec in self.lots:
            sl_init = lot1_sl if spec.lot_id == "lot1" else lot2_sl
            lot_legs = [dict(leg) for leg in norm_legs]
            lots_obj[spec.lot_id] = {
                "lot_id": spec.lot_id,
                "timeframe": spec.timeframe,
                "status": "OPEN",
                "opened_at": _to_iso(fill_time),
                "closed_at": None,
                "entry_close": entry_close,
                "sl_init_level": float(sl_init),
                "sl_current_level": float(sl_init),
                "last_trail_ts": None,
                "last_exit_check_ts": None,
                "legs": lot_legs,
                "meta": {
                    "position_type": intent.get("position_type"),
                    "reason": intent.get("reason"),
                    "strikes": intent.get("strikes"),
                    "spot": intent.get("spot"),
                    "entry_signal_time": _to_iso(signal_time),
                    "entry_fill_time": _to_iso(fill_time),
                },
            }

        self.position["meta"] = {"last_entry_intent_ts": intent.get("timestamp")}

        msg = f"OPENED NIFTY POSITION: 2 lots, {len(norm_legs)} legs, type={intent.get('position_type')} reason={intent.get('reason')}"
        self.logger.info(msg)
        self.trade_logger.info(msg)
        for leg in norm_legs:
            self.trade_logger.info(f"  {leg['side']} {leg['tradingsymbol']} @ {leg['entry_price']}")

        self.save_position()

        for lot_id in ["lot1", "lot2"]:
            lot = (self.position.get("lots") or {}).get(lot_id)
            if lot and lot.get("status") == "OPEN":
                trade_id = str(uuid.uuid4())[:8]
                self._active_trade_ids[lot_id] = trade_id

                trade = Trade(
                    trade_id=trade_id,
                    entry_time=fill_time,
                    entry_signal_time=signal_time,
                    entry_fill_time=fill_time,
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
                        timestamp=fill_time,
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
            timestamp=fill_time,
            section=reason.get("section", ""),
            subcategory=reason.get("subcat"),
            position_type=intent.get("position_type", ""),
            gate_checks=intent.get("_gate_checks", []),
            spot_price=float(intent.get("spot") or 0.0),
            legs=norm_legs,
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
        if will_exit and min_hold_ok:
            self._pending_sl_exit = self._pending_sl_exit if hasattr(self, '_pending_sl_exit') else set()
            self._pending_sl_exit.add(lot_id)

        # Call parent implementation
        super()._exit_check_for_lot(lot_id, timeframe, df_tf)

    def _close_lot(self, lot_id: str, exit_time: str) -> None:
        """Override to log trade exit to TradeLog."""
        lot = (self.position.get("lots") or {}).get(lot_id)
        if not lot or lot.get("status") != "OPEN":
            return

        exit_signal_time = self._current_time_fn()
        try:
            exit_fill_time, _, price_map = self._next_fill_snapshot(exit_signal_time, "exit")
            strict_prices = self._strict_leg_prices(lot.get("legs") or [], price_map, exit_fill_time, "exit")
        except StrictBacktestDataError as exc:
            self.trade_log.record_data_quality_warning(str(exc))
            raise
        exit_fill_time_iso = _to_iso(exit_fill_time)

        # Calculate P&L
        pnl = 0.0
        exit_total = 0.0
        legs_pnl = []  # For debug logging
        for leg in lot.get("legs") or []:
            symbol = leg.get("tradingsymbol") or ""
            side = (leg.get("side") or "BUY").upper()
            entry = float(leg.get("entry_price") or 0.0)
            current = strict_prices[symbol]
            leg["exit_price"] = current
            leg["exit_time"] = exit_fill_time_iso

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
        lot["closed_at"] = exit_fill_time_iso
        lot["pnl"] = pnl
        lot.setdefault("meta", {})
        lot["meta"]["exit_signal_time"] = _to_iso(exit_signal_time)
        lot["meta"]["exit_fill_time"] = exit_fill_time_iso

        # Log to TradeLog
        trade_id = self._active_trade_ids.get(lot_id)
        if trade_id:
            # Determine exit reason
            exit_reason = "SL_HIT" if lot_id in self._pending_sl_exit else "TIME_EXIT"
            self._pending_sl_exit.discard(lot_id)

            # Record final SL state before exit
            final_sl = float(lot.get("sl_current_level") or 0.0)
            self.trade_log.record_sl_adjustment(
                trade_id=trade_id,
                timestamp=exit_fill_time,
                old_sl=final_sl,
                new_sl=final_sl,
                sh_value=None,
                close_price=0.0,  # Not available here
                trigger="EXIT",
            )

            self.trade_log.record_exit(
                trade_id=trade_id,
                exit_time=exit_fill_time,
                exit_price_total=exit_total,
                pnl=pnl,
                exit_reason=exit_reason,
                exit_signal_time=exit_signal_time,
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
                timestamp=exit_fill_time,
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

    def reset_for_backtest_day(self) -> None:
        """Reset per-session strategy state at the start of a simulated trading day."""
        self.strategy.reset_state()

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
