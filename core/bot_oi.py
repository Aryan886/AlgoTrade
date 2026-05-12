from __future__ import annotations

import copy
import json
import os
import time
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional

import pandas as pd

from core.strat_oi import OIExpiryStrategy, build_default_oi_state, normalize_oi_state
from utils.db_func import fetch_current_exact_option_ltp, fetch_latest_option_snapshot
from utils.utility import setup_oi_logging

def _to_iso(ts: Any) -> str:
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return str(ts)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _trade_id(prefix: str, timestamp: str) -> str:
    compact = "".join(ch for ch in str(timestamp) if ch.isdigit())
    return f"{prefix}-{compact or 'unknown'}"


def build_default_oi_position(symbol: str = "NIFTY50") -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "type_a": {
            "position_1": {"status": "FLAT", "legs": [], "pnl": 0.0},
            "position_2": {"status": "FLAT", "legs": [], "pnl": 0.0},
        },
        "type_b": {
            "position": {"status": "FLAT", "legs": [], "pnl": 0.0},
        },
        "meta": {
            "selected_strategy": "standard_nifty",
            "last_action_timestamp": None,
        },
    }


class OIExpiryPaperBot:
    TUESDAY_STALE_CLEANUP_TIME = dt_time(9, 0)

    def __init__(
        self,
        symbol: str = "NIFTY50",
        position_file: str = "active_position_oi.json",
        state_file: str = "oi_strategy_state.json",
        dry_run: bool = False,
        strategy: Optional[OIExpiryStrategy] = None,
    ) -> None:
        self.symbol = symbol or "NIFTY50"
        self.position_file = position_file
        self.state_file = state_file
        self.dry_run = dry_run
        self.logger, self.trade_logger, self.position_logger = setup_oi_logging()
        self.strategy = strategy or OIExpiryStrategy(symbol=self.symbol)
        self.last_evaluation_status: Dict[str, Any] = {}
        self.state = self._load_state()
        self.position = self._load_position()

    def _now(self) -> datetime:
        return datetime.now()

    def _load_state(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_file):
            return build_default_oi_state()
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                return normalize_oi_state(json.load(handle))
        except Exception as exc:
            self.logger.warning("OI state load failed file=%s error=%s. Resetting state.", self.state_file, exc)
            return build_default_oi_state()

    def _load_position(self) -> Dict[str, Any]:
        if not os.path.exists(self.position_file):
            return build_default_oi_position(symbol=self.symbol)
        try:
            with open(self.position_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            self.logger.warning("OI position load failed file=%s error=%s. Resetting position.", self.position_file, exc)
            return build_default_oi_position(symbol=self.symbol)

        base = build_default_oi_position(symbol=self.symbol)
        data = data if isinstance(data, dict) else {}
        base["meta"].update(data.get("meta") or {})
        base["type_a"]["position_1"].update(((data.get("type_a") or {}).get("position_1") or {}))
        base["type_a"]["position_2"].update(((data.get("type_a") or {}).get("position_2") or {}))
        base["type_b"]["position"].update(((data.get("type_b") or {}).get("position") or {}))
        base["symbol"] = data.get("symbol") or self.symbol
        return base

    def save_state(self) -> None:
        if self.dry_run:
            return
        try:
            with open(self.state_file, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=2, default=str)
        except Exception as exc:
            self.logger.error("OI state save failed file=%s error=%s", self.state_file, exc)

    def save_position(self) -> None:
        if self.dry_run:
            return
        try:
            with open(self.position_file, "w", encoding="utf-8") as handle:
                json.dump(self.position, handle, indent=2, default=str)
        except Exception as exc:
            self.logger.error("OI position save failed file=%s error=%s", self.position_file, exc)

    def _reset_type_a_state(self) -> None:
        default_state = build_default_oi_state()
        self.state["type_a"] = copy.deepcopy(default_state["type_a"])
        self.position["type_a"]["position_1"] = copy.deepcopy(build_default_oi_position(self.symbol)["type_a"]["position_1"])
        self.position["type_a"]["position_2"] = copy.deepcopy(build_default_oi_position(self.symbol)["type_a"]["position_2"])

    def _reset_type_b_state(self) -> None:
        default_state = build_default_oi_state()
        self.state["type_b"] = copy.deepcopy(default_state["type_b"])
        self.position["type_b"]["position"] = copy.deepcopy(build_default_oi_position(self.symbol)["type_b"]["position"])

    def reset_all(self, now: Optional[datetime] = None) -> None:
        now = now or self._now()
        had_open_positions = self.has_open_positions()
        self.state = build_default_oi_state()
        self.state["session"]["last_reset_date"] = now.date().isoformat()
        self.position = build_default_oi_position(symbol=self.symbol)
        self.position["meta"]["last_action_timestamp"] = _to_iso(now)
        self.save_state()
        self.save_position()
        self.last_evaluation_status = {}
        self.logger.info(
            "OI reset completed ts=%s had_open_positions=%s",
            _to_iso(now),
            had_open_positions,
        )

    def maybe_session_reset(self, now: Optional[datetime] = None) -> bool:
        now = now or self._now()
        today = now.date().isoformat()
        last_reset_date = (self.state.get("session") or {}).get("last_reset_date")
        if last_reset_date == today:
            return False
        if now.time() < self.strategy.SESSION_RESET_TIME:
            return False
        self.logger.info(
            "OI session reset triggered ts=%s last_reset_date=%s",
            _to_iso(now),
            last_reset_date,
        )
        self.reset_all(now=now)
        return True

    def _branch_is_open(self, branch: Optional[Dict[str, Any]]) -> bool:
        return bool(isinstance(branch, dict) and branch.get("status") == "OPEN")

    def has_open_positions(self) -> bool:
        return any(
            self._branch_is_open(branch)
            for branch in (
                self.position["type_a"]["position_1"],
                self.position["type_a"]["position_2"],
                self.position["type_b"]["position"],
            )
        )

    def maybe_run_tuesday_safety_cleanup(self, now: Optional[datetime] = None) -> bool:
        now = now or self._now()
        if not self.strategy.is_tuesday(now):
            return False
        if not self.has_open_positions():
            return False

        today = now.date().isoformat()
        last_reset_date = (self.state.get("session") or {}).get("last_reset_date")
        stale_from_previous_session = (
            now.time() >= self.TUESDAY_STALE_CLEANUP_TIME and last_reset_date != today
        )
        post_close_cleanup = now.time() >= self.strategy.HARD_CLOSE_TIME

        if not (stale_from_previous_session or post_close_cleanup):
            return False

        cleanup_reason = (
            "post_close_tuesday_safety_cleanup"
            if post_close_cleanup
            else "stale_tuesday_safety_cleanup"
        )
        self.logger.warning(
            "Running OI Tuesday safety cleanup reason=%s ts=%s.",
            cleanup_reason,
            _to_iso(now),
        )
        self._force_square_off_all(now=now)
        return True

    def _normalize_leg(self, leg: Dict[str, Any]) -> Dict[str, Any]:
        entry_price = float(leg.get("last_price") or 0.0)
        return {
            "tradingsymbol": leg.get("tradingsymbol"),
            "side": str(leg.get("action") or "BUY").upper(),
            "option_type": str(leg.get("option_type") or "").upper(),
            "strike_price": int(leg.get("strike_price")) if leg.get("strike_price") is not None else None,
            "expiry": leg.get("expiry"),
            "entry_price": entry_price,
            "last_price": entry_price,
            "exit_price": None,
            "exit_time": None,
        }

    def _format_legs_for_log(self, legs: List[Dict[str, Any]]) -> str:
        parts = []
        for leg in legs or []:
            parts.append(
                f"{leg.get('side')}:{leg.get('tradingsymbol')}@{_safe_float(leg.get('entry_price') or leg.get('last_price')):.2f}"
            )
        return ",".join(parts) or "none"

    def _resolve_leg_current_price(
        self,
        leg: Dict[str, Any],
        option_rows: Optional[List[Dict[str, Any]]] = None,
        *,
        warn_on_fallback: bool = False,
    ) -> float:
        current_price = fetch_current_exact_option_ltp(
            symbol=self.symbol,
            tradingsymbol=leg.get("tradingsymbol"),
            strike_price=leg.get("strike_price"),
            option_type=leg.get("option_type"),
            expiry=leg.get("expiry"),
            option_rows=option_rows,
        )
        if current_price is not None:
            leg["last_price"] = float(current_price)
            return float(current_price)
        if warn_on_fallback:
            self.logger.warning(
                "OI price fallback trade_id=%s symbol=%s using_last_known=%s",
                leg.get("trade_id"),
                leg.get("tradingsymbol"),
                _safe_float(leg.get("last_price") or leg.get("entry_price")),
            )
        return float(leg.get("last_price") or leg.get("entry_price") or 0.0)

    def _branch_pnl(self, branch: Dict[str, Any], option_rows: Optional[List[Dict[str, Any]]] = None) -> float:
        pnl = 0.0
        for leg in branch.get("legs") or []:
            current_price = self._resolve_leg_current_price(leg, option_rows=option_rows)
            entry_price = float(leg.get("entry_price") or 0.0)
            if str(leg.get("side") or "BUY").upper() == "BUY":
                pnl += current_price - entry_price
            else:
                pnl += entry_price - current_price
        branch["pnl"] = float(pnl)
        return float(pnl)

    def _branch_trade_id(self, branch: Dict[str, Any]) -> str:
        return str(branch.get("trade_id") or "unknown")

    def _branch_label(self, branch: Dict[str, Any]) -> str:
        return str(branch.get("branch_name") or "unknown")

    def _close_branch(self, branch: Dict[str, Any], reason: str, exit_time: str, option_rows: Optional[List[Dict[str, Any]]] = None) -> None:
        if not self._branch_is_open(branch):
            return
        self._branch_pnl(branch, option_rows=option_rows)
        for leg in branch.get("legs") or []:
            leg["exit_price"] = self._resolve_leg_current_price(leg, option_rows=option_rows, warn_on_fallback=True)
            leg["exit_time"] = exit_time
        branch["status"] = "CLOSED"
        branch["closed_at"] = exit_time
        branch["close_reason"] = reason
        self.trade_logger.info(
            "CLOSED OI branch=%s trade_id=%s reason=%s pnl=%.2f opened_at=%s closed_at=%s legs=%s",
            self._branch_label(branch),
            self._branch_trade_id(branch),
            reason,
            _safe_float(branch.get("pnl")),
            branch.get("opened_at"),
            exit_time,
            self._format_legs_for_log(branch.get("legs") or []),
        )

    def _open_type_a_position(self, slot: str, action: Dict[str, Any], timestamp: str) -> None:
        branch = self.position["type_a"][slot]
        branch_name = "type_a.position_1" if slot == "position_1" else "type_a.position_2"
        trade_id = _trade_id("A1" if slot == "position_1" else "A2", timestamp)
        branch.update({
            "status": "OPEN",
            "opened_at": timestamp,
            "closed_at": None,
            "close_reason": None,
            "trade_id": trade_id,
            "branch_name": branch_name,
            "entry_diff": float(action.get("entry_diff") or 0.0),
            "current_diff": float(action.get("entry_diff") or 0.0),
            "pnl": 0.0,
            "legs": [self._normalize_leg(leg) for leg in (action.get("legs") or [])],
            "oi_contracts": action.get("oi_contracts"),
            "strike_bundle": action.get("strike_bundle"),
        })
        for leg in branch.get("legs") or []:
            leg["trade_id"] = trade_id
        self.state["type_a"]["strike_bundle"] = copy.deepcopy(action.get("strike_bundle"))
        if slot == "position_1":
            self.state["entry_price_below_1m_smas"] = bool(action.get("entry_price_below_1m_smas"))
            self.state["type_a"]["position_1"] = {"is_open": True, "opened_at": timestamp}
            self.state["type_a"]["position_1_entry_diff"] = float(action.get("entry_diff") or 0.0)
            self.state["type_a"]["position_2_trigger_consumed"] = False
            self.state["type_a"]["position_2_rearmed"] = False
            self.state["type_a"]["position_2_reentry_count"] = 0
            self.state["type_a"]["flag_1"] = False
            self.state["type_a"]["flag_2"] = False
        else:
            self.state["type_a"]["position_2"] = {"is_open": True, "opened_at": timestamp}
            self.state["type_a"]["position_2_entry_diff"] = float(action.get("entry_diff") or 0.0)
            self.state["type_a"]["position_2_trigger_consumed"] = True
            self.state["type_a"]["position_2_rearmed"] = False
            if str(action.get("reentry_kind") or "").lower() == "reopen":
                self.state["type_a"]["position_2_reentry_count"] = int(
                    self.state["type_a"].get("position_2_reentry_count") or 0
                ) + 1
            self.state["type_a"]["flag_2"] = True
            self.state["type_a"]["flag_1"] = False
        self.trade_logger.info(
            "OPENED OI branch=%s trade_id=%s reason=%s entry_diff=%.2f legs=%s",
            branch_name,
            trade_id,
            action.get("reason"),
            _safe_float(action.get("entry_diff")),
            self._format_legs_for_log(branch.get("legs") or []),
        )

    def _open_type_b_position(self, action: Dict[str, Any], timestamp: str) -> None:
        branch = self.position["type_b"]["position"]
        trade_id = _trade_id("B", timestamp)
        branch.update({
            "status": "OPEN",
            "opened_at": timestamp,
            "closed_at": None,
            "close_reason": None,
            "trade_id": trade_id,
            "branch_name": "type_b.position",
            "pnl": 0.0,
            "legs": [self._normalize_leg(leg) for leg in (action.get("legs") or [])],
            "trigger_1": bool(action.get("trigger_1")),
            "trigger_2": bool(action.get("trigger_2")),
            "selected_contracts": action.get("selected_contracts"),
            "sell_pe_entry_ltp": float(action.get("sell_pe_entry_ltp") or 0.0),
            "oi_contracts": action.get("oi_contracts"),
        })
        for leg in branch.get("legs") or []:
            leg["trade_id"] = trade_id
        self.state["type_b"]["position"] = {"is_open": True, "opened_at": timestamp}
        self.state["entry_price_below_1m_smas"] = bool(action.get("entry_price_below_1m_smas"))
        self.state["type_b"]["sell_pe_entry_ltp"] = float(action.get("sell_pe_entry_ltp") or 0.0)
        self.state["type_b"]["selected_contracts"] = copy.deepcopy(action.get("selected_contracts"))
        self.state["type_b"]["trigger_1_seen"] = bool(action.get("trigger_1"))
        self.state["type_b"]["trigger_2_seen"] = bool(action.get("trigger_2"))
        self.trade_logger.info(
            "OPENED OI branch=type_b.position trade_id=%s reason=%s trigger_1=%s trigger_2=%s sell_pe_entry_ltp=%.2f legs=%s",
            trade_id,
            action.get("reason"),
            bool(action.get("trigger_1")),
            bool(action.get("trigger_2")),
            _safe_float(action.get("sell_pe_entry_ltp")),
            self._format_legs_for_log(branch.get("legs") or []),
        )

    def _refresh_branch_metrics(self, option_rows: Optional[List[Dict[str, Any]]] = None) -> float:
        aggregate_open_pnl = 0.0
        for branch in (
            self.position["type_a"]["position_1"],
            self.position["type_a"]["position_2"],
            self.position["type_b"]["position"],
        ):
            if self._branch_is_open(branch):
                aggregate_open_pnl += self._branch_pnl(branch, option_rows=option_rows)
        self.position.setdefault("meta", {})["aggregate_open_pnl"] = float(aggregate_open_pnl)
        return float(aggregate_open_pnl)

    def _log_position_monitor(
        self,
        evaluation_status: Optional[Dict[str, Any]] = None,
        option_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if not self.has_open_positions():
            return
        aggregate_open_pnl = self._refresh_branch_metrics(option_rows=option_rows)
        status = evaluation_status or self.last_evaluation_status or {}
        type_a_status = status.get("type_a") or {}
        type_b_status = status.get("type_b") or {}
        a1 = self.position["type_a"]["position_1"]
        a2 = self.position["type_a"]["position_2"]
        b = self.position["type_b"]["position"]
        self.position_logger.info(
            "OI monitor ts=%s a1=%s trade_id=%s pnl=%.2f a2=%s trade_id=%s pnl=%.2f "
            "b=%s trade_id=%s pnl=%.2f agg_open_pnl=%.2f current_diff=%s triggers=%s/%s",
            _to_iso(self._now()),
            a1.get("status", "FLAT"),
            a1.get("trade_id"),
            _safe_float(a1.get("pnl")),
            a2.get("status", "FLAT"),
            a2.get("trade_id"),
            _safe_float(a2.get("pnl")),
            b.get("status", "FLAT"),
            b.get("trade_id"),
            _safe_float(b.get("pnl")),
            aggregate_open_pnl,
            type_a_status.get("current_diff"),
            type_b_status.get("trigger_1_seen"),
            type_b_status.get("trigger_2_seen"),
        )

    def _apply_action(self, action: Dict[str, Any], timestamp: str) -> None:
        option_rows = fetch_latest_option_snapshot(symbol=self.symbol)
        action_type = str(action.get("type") or "")
        self.logger.info(
            "OI applying action type=%s ts=%s reason=%s",
            action_type,
            timestamp,
            action.get("reason"),
        )

        if action_type == "OPEN_TYPE_A_POS1":
            self._open_type_a_position("position_1", action, timestamp)
        elif action_type == "OPEN_TYPE_A_POS2":
            self._open_type_a_position("position_2", action, timestamp)
        elif action_type == "CLOSE_TYPE_A_ALL":
            self._close_branch(self.position["type_a"]["position_1"], str(action.get("reason") or ""), timestamp, option_rows=option_rows)
            self._close_branch(self.position["type_a"]["position_2"], str(action.get("reason") or ""), timestamp, option_rows=option_rows)
            self._reset_type_a_state()
        elif action_type == "CLOSE_TYPE_A_POS1":
            self._close_branch(self.position["type_a"]["position_1"], str(action.get("reason") or ""), timestamp, option_rows=option_rows)
            self.state["type_a"]["position_1"] = {"is_open": False, "opened_at": None}
            self.state["type_a"]["position_1_entry_diff"] = None
            if not self._branch_is_open(self.position["type_a"]["position_2"]):
                self._reset_type_a_state()
        elif action_type == "CLOSE_TYPE_A_POS2":
            self._close_branch(self.position["type_a"]["position_2"], str(action.get("reason") or ""), timestamp, option_rows=option_rows)
            self.state["type_a"]["position_2"] = {"is_open": False, "opened_at": None}
            self.state["type_a"]["position_2_entry_diff"] = None
            self.state["type_a"]["flag_2"] = False
            self.state["type_a"]["flag_1"] = False
            if not self._branch_is_open(self.position["type_a"]["position_1"]):
                self._reset_type_a_state()
        elif action_type == "OPEN_TYPE_B_POSITION":
            self._open_type_b_position(action, timestamp)
        elif action_type == "CLOSE_TYPE_B_POSITION":
            self._close_branch(self.position["type_b"]["position"], str(action.get("reason") or ""), timestamp, option_rows=option_rows)
            self._reset_type_b_state()

        self.position["meta"]["last_action_timestamp"] = timestamp

    def _force_square_off_all(self, now: Optional[datetime] = None) -> None:
        now = now or self._now()
        exit_time = _to_iso(now)
        option_rows = fetch_latest_option_snapshot(symbol=self.symbol)
        self.logger.warning(
            "OI force square off ts=%s open_positions=%s",
            exit_time,
            self.has_open_positions(),
        )
        self._close_branch(self.position["type_a"]["position_1"], "hard_close", exit_time, option_rows=option_rows)
        self._close_branch(self.position["type_a"]["position_2"], "hard_close", exit_time, option_rows=option_rows)
        self._close_branch(self.position["type_b"]["position"], "hard_close", exit_time, option_rows=option_rows)
        self.reset_all(now=now)

    def run_once(self) -> None:
        now = self._now()
        self.maybe_session_reset(now=now)

        if not self.strategy.is_tuesday(now):
            self.logger.info("OI bot skipping cycle ts=%s reason=not_tuesday", _to_iso(now))
            self.position["meta"]["selected_strategy"] = "standard_nifty"
            self.position["meta"]["last_action_timestamp"] = _to_iso(now)
            self.save_position()
            self.save_state()
            return

        if self.strategy._hard_close_reached(now):
            self.logger.info("OI bot hard close reached ts=%s", _to_iso(now))
            self._force_square_off_all(now=now)
            return

        evaluation = self.strategy.evaluate(self.state, self.position)
        self.last_evaluation_status = evaluation.get("status") or {}
        self.state = normalize_oi_state(evaluation.get("state"))
        self.position["meta"]["selected_strategy"] = "oi_expiry"
        self.position["meta"]["last_action_timestamp"] = evaluation.get("timestamp")

        for action in evaluation.get("actions") or []:
            self._apply_action(action, str(evaluation.get("timestamp")))

        option_rows = fetch_latest_option_snapshot(symbol=self.symbol)
        self._log_position_monitor(evaluation_status=self.last_evaluation_status, option_rows=option_rows)
        self.save_state()
        self.save_position()

    def get_status_summary(self) -> Dict[str, Any]:
        option_rows = fetch_latest_option_snapshot(symbol=self.symbol)
        aggregate_open_pnl = self._refresh_branch_metrics(option_rows=option_rows)
        type_a_pos1 = self.position["type_a"]["position_1"]
        type_a_pos2 = self.position["type_a"]["position_2"]
        type_b_pos = self.position["type_b"]["position"]
        evaluation_status = self.last_evaluation_status or {}
        type_a_eval = evaluation_status.get("type_a") or {}
        type_b_eval = evaluation_status.get("type_b") or {}
        return {
            "selected_strategy": self.position["meta"].get("selected_strategy", "standard_nifty"),
            "entry_price_below_1m_smas": bool(self.state.get("entry_price_below_1m_smas")),
            "aggregate_open_pnl": aggregate_open_pnl,
            "evaluation_timestamp": evaluation_status.get("evaluation_timestamp"),
            "type_a": {
                "entry_flag_on": bool((self.state.get("type_a") or {}).get("entry_flag_on")),
                "position_1_status": type_a_pos1.get("status", "FLAT"),
                "position_2_status": type_a_pos2.get("status", "FLAT"),
                "position_1_entry_diff": (self.state.get("type_a") or {}).get("position_1_entry_diff"),
                "position_2_entry_diff": (self.state.get("type_a") or {}).get("position_2_entry_diff"),
                "position_2_trigger_consumed": bool((self.state.get("type_a") or {}).get("position_2_trigger_consumed")),
                "position_2_rearmed": bool((self.state.get("type_a") or {}).get("position_2_rearmed")),
                "position_2_reentry_count": int((self.state.get("type_a") or {}).get("position_2_reentry_count") or 0),
                "current_diff": type_a_eval.get("current_diff"),
                "position_1_trade_id": type_a_pos1.get("trade_id"),
                "position_2_trade_id": type_a_pos2.get("trade_id"),
                "position_1_opened_at": type_a_pos1.get("opened_at"),
                "position_2_opened_at": type_a_pos2.get("opened_at"),
                "position_1_close_reason": type_a_pos1.get("close_reason"),
                "position_2_close_reason": type_a_pos2.get("close_reason"),
                "position_1_current_pnl": type_a_pos1.get("pnl"),
                "position_2_current_pnl": type_a_pos2.get("pnl"),
                "pending_actions": type_a_eval.get("pending_actions", []),
                "blockers": type_a_eval.get("blockers", []),
            },
            "type_b": {
                "position_status": type_b_pos.get("status", "FLAT"),
                "trigger_1_seen": bool((self.state.get("type_b") or {}).get("trigger_1_seen")),
                "trigger_2_seen": bool((self.state.get("type_b") or {}).get("trigger_2_seen")),
                "sell_pe_entry_ltp": (self.state.get("type_b") or {}).get("sell_pe_entry_ltp"),
                "trade_id": type_b_pos.get("trade_id"),
                "opened_at": type_b_pos.get("opened_at"),
                "close_reason": type_b_pos.get("close_reason"),
                "current_pnl": type_b_pos.get("pnl"),
                "pending_actions": type_b_eval.get("pending_actions", []),
                "blockers": type_b_eval.get("blockers", []),
            },
        }

    def run_forever(self, sleep_seconds: int = 10) -> None:
        while True:
            try:
                self.run_once()
            except Exception as exc:
                self.logger.error(f"OI bot cycle error: {exc}")
            time.sleep(sleep_seconds)
