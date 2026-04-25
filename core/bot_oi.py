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
from utils.utility import setup_paper_trading_logger


def _to_iso(ts: Any) -> str:
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return str(ts)


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
        _paper_logger, _trade_logger, _position_logger, _sma_logger, _equity_logger, nifty_logger = setup_paper_trading_logger()
        self.logger = nifty_logger
        self.trade_logger = nifty_logger
        self.position_logger = nifty_logger
        self.strategy = strategy or OIExpiryStrategy(symbol=self.symbol)
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
        except Exception:
            return build_default_oi_state()

    def _load_position(self) -> Dict[str, Any]:
        if not os.path.exists(self.position_file):
            return build_default_oi_position(symbol=self.symbol)
        try:
            with open(self.position_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
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
        with open(self.state_file, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=2, default=str)

    def save_position(self) -> None:
        if self.dry_run:
            return
        with open(self.position_file, "w", encoding="utf-8") as handle:
            json.dump(self.position, handle, indent=2, default=str)

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
        self.state = build_default_oi_state()
        self.state["session"]["last_reset_date"] = now.date().isoformat()
        self.position = build_default_oi_position(symbol=self.symbol)
        self.position["meta"]["last_action_timestamp"] = _to_iso(now)
        self.save_state()
        self.save_position()

    def maybe_session_reset(self, now: Optional[datetime] = None) -> bool:
        now = now or self._now()
        today = now.date().isoformat()
        last_reset_date = (self.state.get("session") or {}).get("last_reset_date")
        if last_reset_date == today:
            return False
        if now.time() < self.strategy.SESSION_RESET_TIME:
            return False
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
            "Running OI Tuesday safety cleanup for lingering positions (%s).",
            cleanup_reason,
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

    def _resolve_leg_current_price(self, leg: Dict[str, Any], option_rows: Optional[List[Dict[str, Any]]] = None) -> float:
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

    def _close_branch(self, branch: Dict[str, Any], reason: str, exit_time: str, option_rows: Optional[List[Dict[str, Any]]] = None) -> None:
        if not self._branch_is_open(branch):
            return
        self._branch_pnl(branch, option_rows=option_rows)
        for leg in branch.get("legs") or []:
            leg["exit_price"] = self._resolve_leg_current_price(leg, option_rows=option_rows)
            leg["exit_time"] = exit_time
        branch["status"] = "CLOSED"
        branch["closed_at"] = exit_time
        branch["close_reason"] = reason
        self.trade_logger.info(f"CLOSED OI branch reason={reason} pnl={branch.get('pnl', 0.0):.2f}")

    def _open_type_a_position(self, slot: str, action: Dict[str, Any], timestamp: str) -> None:
        branch = self.position["type_a"][slot]
        branch.update({
            "status": "OPEN",
            "opened_at": timestamp,
            "closed_at": None,
            "close_reason": None,
            "entry_diff": float(action.get("entry_diff") or 0.0),
            "current_diff": float(action.get("entry_diff") or 0.0),
            "pnl": 0.0,
            "legs": [self._normalize_leg(leg) for leg in (action.get("legs") or [])],
            "oi_contracts": action.get("oi_contracts"),
            "strike_bundle": action.get("strike_bundle"),
        })
        self.state["type_a"]["strike_bundle"] = copy.deepcopy(action.get("strike_bundle"))
        if slot == "position_1":
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
        self.trade_logger.info(f"OPENED OI Type A {slot} entry_diff={float(action.get('entry_diff') or 0.0):.2f}")

    def _open_type_b_position(self, action: Dict[str, Any], timestamp: str) -> None:
        branch = self.position["type_b"]["position"]
        branch.update({
            "status": "OPEN",
            "opened_at": timestamp,
            "closed_at": None,
            "close_reason": None,
            "pnl": 0.0,
            "legs": [self._normalize_leg(leg) for leg in (action.get("legs") or [])],
            "trigger_1": bool(action.get("trigger_1")),
            "trigger_2": bool(action.get("trigger_2")),
            "selected_contracts": action.get("selected_contracts"),
            "sell_pe_entry_ltp": float(action.get("sell_pe_entry_ltp") or 0.0),
            "oi_contracts": action.get("oi_contracts"),
        })
        self.state["type_b"]["position"] = {"is_open": True, "opened_at": timestamp}
        self.state["type_b"]["sell_pe_entry_ltp"] = float(action.get("sell_pe_entry_ltp") or 0.0)
        self.state["type_b"]["selected_contracts"] = copy.deepcopy(action.get("selected_contracts"))
        self.state["type_b"]["trigger_1_seen"] = bool(action.get("trigger_1"))
        self.state["type_b"]["trigger_2_seen"] = bool(action.get("trigger_2"))
        self.trade_logger.info(
            f"OPENED OI Type B trigger_1={bool(action.get('trigger_1'))} trigger_2={bool(action.get('trigger_2'))}"
        )

    def _apply_action(self, action: Dict[str, Any], timestamp: str) -> None:
        option_rows = fetch_latest_option_snapshot(symbol=self.symbol)
        action_type = str(action.get("type") or "")

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
        self._close_branch(self.position["type_a"]["position_1"], "hard_close", exit_time, option_rows=option_rows)
        self._close_branch(self.position["type_a"]["position_2"], "hard_close", exit_time, option_rows=option_rows)
        self._close_branch(self.position["type_b"]["position"], "hard_close", exit_time, option_rows=option_rows)
        self.reset_all(now=now)

    def run_once(self) -> None:
        now = self._now()
        self.maybe_session_reset(now=now)

        if not self.strategy.is_tuesday(now):
            self.position["meta"]["selected_strategy"] = "standard_nifty"
            self.position["meta"]["last_action_timestamp"] = _to_iso(now)
            self.save_position()
            self.save_state()
            return

        if self.strategy._hard_close_reached(now):
            self._force_square_off_all(now=now)
            return

        evaluation = self.strategy.evaluate(self.state, self.position)
        self.state = normalize_oi_state(evaluation.get("state"))
        self.position["meta"]["selected_strategy"] = "oi_expiry"
        self.position["meta"]["last_action_timestamp"] = evaluation.get("timestamp")

        for action in evaluation.get("actions") or []:
            self._apply_action(action, str(evaluation.get("timestamp")))

        self.save_state()
        self.save_position()

    def get_status_summary(self) -> Dict[str, Any]:
        type_a_pos1 = self.position["type_a"]["position_1"]
        type_a_pos2 = self.position["type_a"]["position_2"]
        type_b_pos = self.position["type_b"]["position"]
        return {
            "selected_strategy": self.position["meta"].get("selected_strategy", "standard_nifty"),
            "type_a": {
                "entry_flag_on": bool((self.state.get("type_a") or {}).get("entry_flag_on")),
                "position_1_status": type_a_pos1.get("status", "FLAT"),
                "position_2_status": type_a_pos2.get("status", "FLAT"),
                "position_1_entry_diff": (self.state.get("type_a") or {}).get("position_1_entry_diff"),
                "position_2_entry_diff": (self.state.get("type_a") or {}).get("position_2_entry_diff"),
                "position_2_trigger_consumed": bool((self.state.get("type_a") or {}).get("position_2_trigger_consumed")),
                "position_2_rearmed": bool((self.state.get("type_a") or {}).get("position_2_rearmed")),
                "position_2_reentry_count": int((self.state.get("type_a") or {}).get("position_2_reentry_count") or 0),
            },
            "type_b": {
                "position_status": type_b_pos.get("status", "FLAT"),
                "trigger_1_seen": bool((self.state.get("type_b") or {}).get("trigger_1_seen")),
                "trigger_2_seen": bool((self.state.get("type_b") or {}).get("trigger_2_seen")),
                "sell_pe_entry_ltp": (self.state.get("type_b") or {}).get("sell_pe_entry_ltp"),
            },
        }

    def run_forever(self, sleep_seconds: int = 10) -> None:
        while True:
            try:
                self.run_once()
            except Exception as exc:
                self.logger.error(f"OI bot cycle error: {exc}")
            time.sleep(sleep_seconds)
