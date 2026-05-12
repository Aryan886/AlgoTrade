"""
Backtestable OI bot.

Wraps OIExpiryPaperBot with point-in-time data access, strict fill handling,
and TradeLog integration.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import core.bot_oi as bot_oi_module
from core.bot_oi import OIExpiryPaperBot, _safe_float, _to_iso, build_default_oi_position
from core.strat_oi import build_default_oi_state, normalize_oi_state

from backtesting.backtest_oi_strategy import BacktestableOIStrategy
from backtesting.data_provider import HistoricalDataProvider
from backtesting.metrics import Trade, TradeLog


class BacktestableOIBot(OIExpiryPaperBot):
    """Historical-data adapter for the OI expiry paper bot."""

    TARGET_FILL_LATENCY = timedelta(seconds=5)

    def __init__(
        self,
        data_provider: HistoricalDataProvider,
        current_time_fn: Callable[[], datetime],
        trade_log: TradeLog,
        symbol: str = "NIFTY50",
    ) -> None:
        self._data_provider = data_provider
        self._current_time_fn = current_time_fn
        self.trade_log = trade_log

        strategy = BacktestableOIStrategy(
            data_provider=data_provider,
            current_time_fn=current_time_fn,
            symbol=symbol,
        )

        safe_logger = logging.getLogger("backtest.oi_bot.init")
        original_logger_factory = bot_oi_module.setup_oi_logging
        bot_oi_module.setup_oi_logging = lambda: (
            safe_logger,
            safe_logger,
            safe_logger,
        )
        try:
            super().__init__(
                symbol=symbol,
                position_file="",
                state_file="",
                dry_run=False,
                strategy=strategy,
            )
        finally:
            bot_oi_module.setup_oi_logging = original_logger_factory

        self.logger = logging.getLogger("backtest.oi_bot")
        self.trade_logger = self.logger
        self.position_logger = self.logger

    def _now(self) -> datetime:
        return self._current_time_fn()

    def _load_state(self) -> Dict[str, Any]:
        return build_default_oi_state()

    def _load_position(self) -> Dict[str, Any]:
        return build_default_oi_position(symbol=self.symbol)

    def save_state(self) -> None:
        pass

    def save_position(self) -> None:
        pass

    def _fetch_option_snapshot(self) -> List[Dict[str, Any]]:
        return self._data_provider.fetch_option_snapshot(self._current_time_fn())

    def reset_for_backtest_day(self) -> None:
        day_start = self._current_time_fn()
        self.state = build_default_oi_state()
        self.state["session"]["last_reset_date"] = day_start.date().isoformat()
        self.position = build_default_oi_position(symbol=self.symbol)
        self.position["meta"]["selected_strategy"] = "standard_nifty"
        self.position["meta"]["last_action_timestamp"] = _to_iso(day_start)
        self.last_evaluation_status = {}
        if hasattr(self.strategy, "reset_state"):
            self.strategy.reset_state()

    def _lookup_option_price_point_in_time(self, leg: Dict[str, Any]) -> Optional[float]:
        current_time = self._current_time_fn()
        return self._data_provider.fetch_option_price(
            current_time=current_time,
            tradingsymbol=leg.get("tradingsymbol") or "",
            strike_price=leg.get("strike_price"),
            option_type=leg.get("option_type") or "",
            expiry=leg.get("expiry") or "",
        )

    def _resolve_leg_current_price(
        self,
        leg: Dict[str, Any],
        option_rows: Optional[List[Dict[str, Any]]] = None,
        *,
        warn_on_fallback: bool = False,
    ) -> float:
        symbol = str(leg.get("tradingsymbol") or "")
        if option_rows:
            for row in option_rows:
                if str(row.get("tradingsymbol") or "") != symbol:
                    continue
                try:
                    current_price = row.get("ltp")
                    if current_price is not None:
                        leg["last_price"] = float(current_price)
                        return float(current_price)
                except Exception:
                    break

        current_price = self._lookup_option_price_point_in_time(leg)
        if current_price is not None:
            leg["last_price"] = float(current_price)
            return float(current_price)

        if warn_on_fallback:
            self.logger.warning(
                "OI backtest price fallback trade_id=%s symbol=%s using_last_known=%s",
                leg.get("trade_id"),
                symbol,
                _safe_float(leg.get("last_price") or leg.get("entry_price")),
            )
        return float(leg.get("last_price") or leg.get("entry_price") or 0.0)

    def _next_fill_snapshot(
        self,
        signal_time: datetime,
        phase: str,
    ) -> Tuple[Optional[datetime], List[Dict[str, Any]], Dict[str, float]]:
        target_fill_time = signal_time + self.TARGET_FILL_LATENCY
        fill_time, option_rows = self._data_provider.fetch_next_option_snapshot(target_fill_time)
        if fill_time is None or not option_rows:
            self.logger.warning(
                "OI backtest missing %s fill snapshot after %s",
                phase,
                target_fill_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            return None, [], {}

        price_map: Dict[str, float] = {}
        for row in option_rows:
            symbol = str(row.get("tradingsymbol") or "")
            if not symbol:
                continue
            try:
                price_map[symbol] = float(row.get("ltp") or 0.0)
            except Exception:
                continue
        return fill_time, option_rows, price_map

    def _strict_leg_prices(
        self,
        legs: List[Dict[str, Any]],
        price_map: Dict[str, float],
    ) -> Tuple[Dict[str, float], List[str]]:
        prices: Dict[str, float] = {}
        missing: List[str] = []

        for leg in legs or []:
            symbol = str(leg.get("tradingsymbol") or "")
            price = price_map.get(symbol)
            if price is None:
                missing.append(symbol or "<unknown>")
                continue
            prices[symbol] = float(price)
        return prices, missing

    @staticmethod
    def _is_open_action(action_type: str) -> bool:
        return action_type in {"OPEN_TYPE_A_POS1", "OPEN_TYPE_A_POS2", "OPEN_TYPE_B_POSITION"}

    @staticmethod
    def _is_close_action(action_type: str) -> bool:
        return action_type in {
            "CLOSE_TYPE_A_ALL",
            "CLOSE_TYPE_A_POS1",
            "CLOSE_TYPE_A_POS2",
            "CLOSE_TYPE_B_POSITION",
        }

    def _family_for_action(self, action_type: str) -> str:
        return "TYPE_B" if action_type == "OPEN_TYPE_B_POSITION" else "TYPE_A"

    def _branch_targets_for_action(self, action_type: str) -> List[Dict[str, Any]]:
        if action_type == "CLOSE_TYPE_A_ALL":
            return [
                self.position["type_a"]["position_1"],
                self.position["type_a"]["position_2"],
            ]
        if action_type == "CLOSE_TYPE_A_POS1":
            return [self.position["type_a"]["position_1"]]
        if action_type == "CLOSE_TYPE_A_POS2":
            return [self.position["type_a"]["position_2"]]
        if action_type == "CLOSE_TYPE_B_POSITION":
            return [self.position["type_b"]["position"]]
        return []

    def _record_open_skip(self, signal_time: datetime, action_type: str, missing: List[str]) -> None:
        message = (
            f"{action_type} skipped: missing entry price(s) at strict fill snapshot for "
            f"{', '.join(missing)}"
        )
        self.trade_log.record_skipped_entry(signal_time, message)
        self.logger.warning(message)

    def _record_close_skip(self, signal_time: datetime, action_type: str, missing: List[str]) -> None:
        message = (
            f"{action_type} skipped: missing exit price(s) at strict fill snapshot for "
            f"{', '.join(missing)}"
        )
        self.trade_log.record_data_quality_warning(
            f"{signal_time:%Y-%m-%d %H:%M:%S} {message}"
        )
        self.logger.warning(message)

    def _record_branch_entry(
        self,
        branch: Dict[str, Any],
        family: str,
        reason: str,
        signal_time: datetime,
        fill_time: datetime,
    ) -> None:
        entry_total = 0.0
        for leg in branch.get("legs") or []:
            price = float(leg.get("entry_price") or 0.0)
            side = str(leg.get("side") or "BUY").upper()
            if side == "BUY":
                entry_total -= price
            else:
                entry_total += price

        trade = Trade(
            trade_id=str(branch.get("trade_id") or ""),
            entry_time=fill_time,
            entry_signal_time=signal_time,
            entry_fill_time=fill_time,
            lot_id=str(branch.get("branch_name") or ""),
            position_type=family,
            legs=copy.deepcopy(branch.get("legs") or []),
            entry_price_total=entry_total,
            reason={
                "reason": reason,
                "branch_name": branch.get("branch_name"),
            },
            meta={
                "opened_at": branch.get("opened_at"),
            },
        )
        self.trade_log.record_entry(trade)

    def _record_branch_exit(
        self,
        branch: Dict[str, Any],
        signal_time: datetime,
        fill_time: datetime,
        pnl: float,
    ) -> None:
        exit_total = 0.0
        for leg in branch.get("legs") or []:
            price = float(leg.get("exit_price") or 0.0)
            side = str(leg.get("side") or "BUY").upper()
            if side == "BUY":
                exit_total += price
            else:
                exit_total -= price

        self.trade_log.record_exit(
            trade_id=str(branch.get("trade_id") or ""),
            exit_time=fill_time,
            exit_price_total=exit_total,
            pnl=pnl,
            exit_reason=str(branch.get("close_reason") or ""),
            exit_signal_time=signal_time,
        )

    def _close_branch_strict(
        self,
        branch: Dict[str, Any],
        reason: str,
        signal_time: datetime,
        fill_time: datetime,
        strict_prices: Dict[str, float],
    ) -> None:
        if not self._branch_is_open(branch):
            return

        pnl = 0.0
        for leg in branch.get("legs") or []:
            symbol = str(leg.get("tradingsymbol") or "")
            current_price = float(strict_prices[symbol])
            leg["last_price"] = current_price
            leg["exit_price"] = current_price
            leg["exit_time"] = _to_iso(fill_time)
            entry_price = float(leg.get("entry_price") or 0.0)
            if str(leg.get("side") or "BUY").upper() == "BUY":
                pnl += current_price - entry_price
            else:
                pnl += entry_price - current_price

        branch["pnl"] = float(pnl)
        branch["status"] = "CLOSED"
        branch["closed_at"] = _to_iso(fill_time)
        branch["close_reason"] = reason
        self.trade_logger.info(
            "CLOSED OI branch=%s trade_id=%s reason=%s pnl=%.2f opened_at=%s closed_at=%s legs=%s",
            self._branch_label(branch),
            self._branch_trade_id(branch),
            reason,
            float(pnl),
            branch.get("opened_at"),
            _to_iso(fill_time),
            self._format_legs_for_log(branch.get("legs") or []),
        )
        self._record_branch_exit(branch, signal_time=signal_time, fill_time=fill_time, pnl=float(pnl))

    def _open_action_with_fill(
        self,
        action: Dict[str, Any],
        signal_time: datetime,
        fill_time: Optional[datetime],
        price_map: Dict[str, float],
    ) -> None:
        action_type = str(action.get("type") or "")
        if fill_time is None:
            self._record_open_skip(signal_time, action_type, ["<snapshot unavailable>"])
            return

        strict_prices, missing = self._strict_leg_prices(action.get("legs") or [], price_map)
        if missing:
            self._record_open_skip(signal_time, action_type, missing)
            return

        fill_iso = _to_iso(fill_time)
        action_copy = copy.deepcopy(action)
        for leg in action_copy.get("legs") or []:
            symbol = str(leg.get("tradingsymbol") or "")
            leg["last_price"] = float(strict_prices[symbol])

        if action_type == "OPEN_TYPE_A_POS1":
            self._open_type_a_position("position_1", action_copy, fill_iso)
            self._record_branch_entry(
                self.position["type_a"]["position_1"],
                family="TYPE_A",
                reason=str(action.get("reason") or ""),
                signal_time=signal_time,
                fill_time=fill_time,
            )
        elif action_type == "OPEN_TYPE_A_POS2":
            self._open_type_a_position("position_2", action_copy, fill_iso)
            self._record_branch_entry(
                self.position["type_a"]["position_2"],
                family="TYPE_A",
                reason=str(action.get("reason") or ""),
                signal_time=signal_time,
                fill_time=fill_time,
            )
        elif action_type == "OPEN_TYPE_B_POSITION":
            self._open_type_b_position(action_copy, fill_iso)
            self._record_branch_entry(
                self.position["type_b"]["position"],
                family="TYPE_B",
                reason=str(action.get("reason") or ""),
                signal_time=signal_time,
                fill_time=fill_time,
            )

        self.position["meta"]["last_action_timestamp"] = fill_iso

    def _close_action_with_fill(
        self,
        action: Dict[str, Any],
        signal_time: datetime,
        fill_time: Optional[datetime],
        price_map: Dict[str, float],
    ) -> None:
        action_type = str(action.get("type") or "")
        targets = [branch for branch in self._branch_targets_for_action(action_type) if self._branch_is_open(branch)]
        if not targets:
            self.position["meta"]["last_action_timestamp"] = _to_iso(fill_time or signal_time)
            if action_type == "CLOSE_TYPE_A_ALL":
                self._reset_type_a_state()
            elif action_type == "CLOSE_TYPE_B_POSITION":
                self._reset_type_b_state()
            return

        if fill_time is None:
            self._record_close_skip(signal_time, action_type, ["<snapshot unavailable>"])
            return

        missing: List[str] = []
        strict_prices: Dict[str, float] = {}
        for branch in targets:
            branch_prices, branch_missing = self._strict_leg_prices(branch.get("legs") or [], price_map)
            strict_prices.update(branch_prices)
            missing.extend(branch_missing)

        if missing:
            self._record_close_skip(signal_time, action_type, missing)
            return

        fill_iso = _to_iso(fill_time)
        reason = str(action.get("reason") or "")
        for branch in targets:
            self._close_branch_strict(
                branch,
                reason=reason,
                signal_time=signal_time,
                fill_time=fill_time,
                strict_prices=strict_prices,
            )

        if action_type == "CLOSE_TYPE_A_ALL":
            self._reset_type_a_state()
        elif action_type == "CLOSE_TYPE_A_POS1":
            self.state["type_a"]["position_1"] = {"is_open": False, "opened_at": None}
            self.state["type_a"]["position_1_entry_diff"] = None
            if not self._branch_is_open(self.position["type_a"]["position_2"]):
                self._reset_type_a_state()
        elif action_type == "CLOSE_TYPE_A_POS2":
            self.state["type_a"]["position_2"] = {"is_open": False, "opened_at": None}
            self.state["type_a"]["position_2_entry_diff"] = None
            self.state["type_a"]["flag_2"] = False
            self.state["type_a"]["flag_1"] = False
            if not self._branch_is_open(self.position["type_a"]["position_1"]):
                self._reset_type_a_state()
        elif action_type == "CLOSE_TYPE_B_POSITION":
            self._reset_type_b_state()

        self.position["meta"]["last_action_timestamp"] = fill_iso

    def _apply_backtest_actions(
        self,
        actions: List[Dict[str, Any]],
        evaluation_timestamp: str,
    ) -> None:
        if not actions:
            return

        signal_time = datetime.fromisoformat(str(evaluation_timestamp))
        has_open_actions = any(self._is_open_action(str(action.get("type") or "")) for action in actions)
        has_close_actions = any(self._is_close_action(str(action.get("type") or "")) for action in actions)

        open_fill_time: Optional[datetime] = None
        open_price_map: Dict[str, float] = {}
        close_fill_time: Optional[datetime] = None
        close_price_map: Dict[str, float] = {}

        if has_open_actions:
            open_fill_time, _open_rows, open_price_map = self._next_fill_snapshot(signal_time, "entry")
        if has_close_actions:
            close_fill_time, _close_rows, close_price_map = self._next_fill_snapshot(signal_time, "exit")

        for action in actions:
            action_type = str(action.get("type") or "")
            self.logger.info(
                "OI backtest applying action type=%s signal_ts=%s reason=%s",
                action_type,
                evaluation_timestamp,
                action.get("reason"),
            )
            if self._is_open_action(action_type):
                self._open_action_with_fill(action, signal_time, open_fill_time, open_price_map)
            elif self._is_close_action(action_type):
                self._close_action_with_fill(action, signal_time, close_fill_time, close_price_map)

    def _strict_square_off_all(self, signal_time: datetime, reason: str) -> None:
        targets = [
            branch
            for branch in (
                self.position["type_a"]["position_1"],
                self.position["type_a"]["position_2"],
                self.position["type_b"]["position"],
            )
            if self._branch_is_open(branch)
        ]
        if not targets:
            self.reset_for_backtest_day()
            return

        fill_time, _rows, price_map = self._next_fill_snapshot(signal_time, "hard_close")
        if fill_time is None:
            self.trade_log.record_data_quality_warning(
                f"{signal_time:%Y-%m-%d %H:%M:%S} hard_close skipped: missing strict exit snapshot"
            )
            self.logger.warning("OI backtest hard close skipped due to missing strict exit snapshot.")
            return

        missing: List[str] = []
        strict_prices: Dict[str, float] = {}
        for branch in targets:
            branch_prices, branch_missing = self._strict_leg_prices(branch.get("legs") or [], price_map)
            strict_prices.update(branch_prices)
            missing.extend(branch_missing)
        if missing:
            self.trade_log.record_data_quality_warning(
                f"{signal_time:%Y-%m-%d %H:%M:%S} hard_close skipped: missing strict exit price(s) for {', '.join(missing)}"
            )
            self.logger.warning(
                "OI backtest hard close skipped due to missing strict exit price(s): %s",
                ", ".join(missing),
            )
            return

        for branch in targets:
            self._close_branch_strict(
                branch,
                reason=reason,
                signal_time=signal_time,
                fill_time=fill_time,
                strict_prices=strict_prices,
            )

        self.reset_for_backtest_day()
        self.position["meta"]["last_action_timestamp"] = _to_iso(fill_time)

    def _force_square_off_all(self, now: Optional[datetime] = None) -> None:
        signal_time = now or self._current_time_fn()
        self._strict_square_off_all(signal_time, reason="hard_close")

    def run_once(self) -> None:
        now = self._now()
        self.maybe_session_reset(now=now)

        if not self.strategy.is_tuesday(now):
            self.position["meta"]["selected_strategy"] = "standard_nifty"
            self.position["meta"]["last_action_timestamp"] = _to_iso(now)
            return

        if self.strategy._hard_close_reached(now):
            self._strict_square_off_all(now, reason="hard_close")
            return

        evaluation = self.strategy.evaluate(self.state, self.position)
        self.last_evaluation_status = evaluation.get("status") or {}
        self.state = normalize_oi_state(evaluation.get("state"))
        self.position["meta"]["selected_strategy"] = "oi_expiry"
        self.position["meta"]["last_action_timestamp"] = evaluation.get("timestamp")

        self._apply_backtest_actions(
            list(evaluation.get("actions") or []),
            str(evaluation.get("timestamp") or _to_iso(now)),
        )

        option_rows = self._fetch_option_snapshot()
        self._log_position_monitor(
            evaluation_status=self.last_evaluation_status,
            option_rows=option_rows,
        )

    def get_unrealized_pnl(self) -> float:
        option_rows = self._fetch_option_snapshot()
        return self._refresh_branch_metrics(option_rows=option_rows)
