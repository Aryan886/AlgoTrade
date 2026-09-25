from __future__ import annotations

import copy
from datetime import date, datetime, time as dtime
from typing import Any, Dict, List, Optional

import pandas as pd

from utils.db_func import (
    fetch_current_exact_option_ltp,
    fetch_latest_oi_vwap_5m_for_contract,
    fetch_latest_open_interest_5m_snapshot,
    fetch_latest_open_interest_snapshot,
    fetch_latest_option_snapshot,
    fetch_market_data,
    find_nearest_expiry_open_interest_contract,
    find_nearest_expiry_option_contract,
    floor_to_100_strike,
    scan_first_lower_pe_contract_below_ltp,
)
from utils.utility import setup_oi_logging


def _to_iso(ts: Any) -> str:
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return str(ts)


def build_default_oi_state() -> Dict[str, Any]:
    return {
        "entry_price_below_1m_smas": False,
        "session": {
            "last_reset_date": None,
        },
        "type_a": {
            "entry_flag_on": False,
            "position_1": {"is_open": False, "opened_at": None},
            "position_2": {"is_open": False, "opened_at": None},
            "position_1_entry_diff": None,
            "position_2_entry_diff": None,
            "position_2_trigger_consumed": False,
            "position_2_rearmed": False,
            "position_2_reentry_count": 0,
            "flag_1": False,
            "flag_2": False,
            "strike_bundle": None,
        },
        "type_b": {
            "trigger_1_seen": False,
            "trigger_2_seen": False,
            "position": {"is_open": False, "opened_at": None},
            "sell_pe_entry_ltp": None,
            "selected_contracts": None,
        },
    }


def normalize_oi_state(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    incoming = state or {}
    base = build_default_oi_state()

    base["entry_price_below_1m_smas"] = bool(incoming.get("entry_price_below_1m_smas"))

    session = incoming.get("session") or {}
    base["session"].update(session)

    type_a = incoming.get("type_a") or {}
    base["type_a"].update({k: v for k, v in type_a.items() if k in base["type_a"]})
    base["type_a"]["position_1"] = {
        **build_default_oi_state()["type_a"]["position_1"],
        **(type_a.get("position_1") or {}),
    }
    base["type_a"]["position_2"] = {
        **build_default_oi_state()["type_a"]["position_2"],
        **(type_a.get("position_2") or {}),
    }

    type_b = incoming.get("type_b") or {}
    base["type_b"].update({k: v for k, v in type_b.items() if k in base["type_b"]})
    base["type_b"]["position"] = {
        **build_default_oi_state()["type_b"]["position"],
        **(type_b.get("position") or {}),
    }
    return base


class OIExpiryStrategy:
    ACTIVE_WEEKDAY = 1  # Tuesday
    ENTRY_START_TIME = dtime(9, 25)
    HARD_CLOSE_TIME = dtime(15, 20)
    SESSION_RESET_TIME = dtime(9, 15)

    def __init__(self, symbol: str = "NIFTY50") -> None:
        self.symbol = symbol or "NIFTY50"
        self.logger, _oi_trade_logger, _oi_position_logger = setup_oi_logging()

    def _now(self) -> datetime:
        return datetime.now()

    def _get_df(self, interval: str, limit: int = 300) -> pd.DataFrame:
        df = fetch_market_data(symbol=self.symbol, interval=interval, limit=limit)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df.columns = [str(col).lower() for col in df.columns]
        return df

    def _fetch_option_snapshot(self) -> List[Dict[str, Any]]:
        return fetch_latest_option_snapshot(symbol=self.symbol)

    def _fetch_open_interest_snapshot(self, current_time=None) -> List[Dict[str, Any]]:
        return fetch_latest_open_interest_snapshot(symbol=self.symbol, current_time=current_time)

    def _fetch_open_interest_5m_snapshot(self, current_time=None) -> List[Dict[str, Any]]:
        return fetch_latest_open_interest_5m_snapshot(symbol=self.symbol, current_time=current_time)

    def is_tuesday(self, now: Optional[datetime] = None) -> bool:
        now = now or self._now()
        return now.weekday() == self.ACTIVE_WEEKDAY

    def _entry_window_open(self, now: Optional[datetime] = None) -> bool:
        now = now or self._now()
        return self.is_tuesday(now) and self.ENTRY_START_TIME <= now.time() < self.HARD_CLOSE_TIME

    def _hard_close_reached(self, now: Optional[datetime] = None) -> bool:
        now = now or self._now()
        return now.time() >= self.HARD_CLOSE_TIME

    def _position_is_open(self, branch: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(branch, dict):
            return False
        return bool(branch.get("status") == "OPEN")

    def _latest_ts(self, df: pd.DataFrame) -> Optional[pd.Timestamp]:
        if df is None or df.empty:
            return None
        try:
            return pd.Timestamp(df.index[-1])
        except Exception:
            return None

    def _entry_flag_on(self, latest_1m: pd.Series, latest_5m: pd.Series) -> bool:
        try:
            close_1m = float(latest_1m["close"])
            high_1m = float(latest_1m["high"])
        except Exception:
            return False

        for column_name in ("sma_20", "donchian_mid"):
            if column_name not in latest_5m or pd.isna(latest_5m[column_name]):
                continue
            try:
                if abs(high_1m - float(latest_5m[column_name])) <= 5.0:
                    return True
            except Exception:
                continue

        try:
            donchian_mid = float(latest_5m["donchian_mid"])
            return close_1m < donchian_mid
        except Exception:
            return False

    def _entry_price_below_1m_smas(self, latest_1m: pd.Series) -> bool:
        try:
            close_1m = float(latest_1m["close"])
            sma_20 = float(latest_1m["sma_20"])
            sma_50 = float(latest_1m["sma_50"])
        except Exception:
            return False

        if pd.isna(close_1m) or pd.isna(sma_20) or pd.isna(sma_50):
            return False
        return close_1m < sma_20 and close_1m < sma_50

    def _evaluate_universal_oi(
        self,
        atm_strike: int,
        oi_rows: List[Dict[str, Any]],
        current_date: date,
    ) -> Dict[str, Any]:
        atm_pe = find_nearest_expiry_open_interest_contract(
            strike_price=atm_strike,
            option_type="PE",
            symbol=self.symbol,
            oi_rows=oi_rows,
            current_date=current_date,
        )
        atm_ce = find_nearest_expiry_open_interest_contract(
            strike_price=atm_strike,
            option_type="CE",
            symbol=self.symbol,
            oi_rows=oi_rows,
            current_date=current_date,
        )
        otm_ce = find_nearest_expiry_open_interest_contract(
            strike_price=atm_strike + 100,
            option_type="CE",
            symbol=self.symbol,
            oi_rows=oi_rows,
            current_date=current_date,
        )

        contracts = {
            "atm_pe": atm_pe,
            "atm_ce": atm_ce,
            "atm_plus_100_ce": otm_ce,
        }
        if not all(contracts.values()):
            missing = [name for name, contract in contracts.items() if not contract]
            return {
                "passed": False,
                "contracts": contracts,
                "reason": "missing_oi_contracts",
                "missing_contracts": missing,
            }

        try:
            pe_oi = float(atm_pe["open_interest"])
            ce_oi = float(atm_ce["open_interest"])
            otm_ce_oi = float(otm_ce["open_interest"])
        except Exception:
            return {
                "passed": False,
                "contracts": contracts,
                "reason": "invalid_oi_values",
            }

        return {
            "passed": pe_oi > ce_oi and pe_oi > otm_ce_oi,
            "contracts": contracts,
            "reason": "passed" if pe_oi > ce_oi and pe_oi > otm_ce_oi else "oi_filter_failed",
            "pe_oi": pe_oi,
            "ce_oi": ce_oi,
            "otm_ce_oi": otm_ce_oi,
        }

    def _build_type_a_legs(
        self,
        atm_strike: int,
        option_rows: List[Dict[str, Any]],
        current_date: date,
    ) -> Optional[Dict[str, Any]]:
        buy_ce = find_nearest_expiry_option_contract(
            strike_price=atm_strike,
            option_type="CE",
            symbol=self.symbol,
            option_rows=option_rows,
            current_date=current_date,
        )
        sell_ce = find_nearest_expiry_option_contract(
            strike_price=atm_strike + 50,
            option_type="CE",
            symbol=self.symbol,
            option_rows=option_rows,
            current_date=current_date,
        )
        if not buy_ce or not sell_ce:
            return None

        try:
            buy_ltp = float(buy_ce["ltp"])
            sell_ltp = float(sell_ce["ltp"])
        except Exception:
            return None

        expiry = str(buy_ce.get("expiry_date") or buy_ce.get("expiry") or "")[:10]
        return {
            "legs": [
                {
                    "action": "BUY",
                    "tradingsymbol": buy_ce.get("tradingsymbol"),
                    "option_type": "CE",
                    "strike_price": int(buy_ce["strike_price"]),
                    "expiry": expiry,
                    "last_price": buy_ltp,
                },
                {
                    "action": "SELL",
                    "tradingsymbol": sell_ce.get("tradingsymbol"),
                    "option_type": "CE",
                    "strike_price": int(sell_ce["strike_price"]),
                    "expiry": str(sell_ce.get("expiry_date") or sell_ce.get("expiry") or "")[:10],
                    "last_price": sell_ltp,
                },
            ],
            "entry_diff": float(buy_ltp - sell_ltp),
            "strike_bundle": {
                "atm_strike": int(atm_strike),
                "buy_ce": {
                    "tradingsymbol": buy_ce.get("tradingsymbol"),
                    "strike_price": int(buy_ce["strike_price"]),
                    "expiry": expiry,
                },
                "sell_ce": {
                    "tradingsymbol": sell_ce.get("tradingsymbol"),
                    "strike_price": int(sell_ce["strike_price"]),
                    "expiry": str(sell_ce.get("expiry_date") or sell_ce.get("expiry") or "")[:10],
                },
            },
        }

    def _build_type_b_legs(
        self,
        atm_strike: int,
        option_rows: List[Dict[str, Any]],
        current_date: date,
    ) -> Optional[Dict[str, Any]]:
        deep_itm_ce = find_nearest_expiry_option_contract(
            strike_price=atm_strike - 200,
            option_type="CE",
            symbol=self.symbol,
            option_rows=option_rows,
            current_date=current_date,
        )
        buy_pe = find_nearest_expiry_option_contract(
            strike_price=atm_strike,
            option_type="PE",
            symbol=self.symbol,
            option_rows=option_rows,
            current_date=current_date,
        )
        sell_pe = scan_first_lower_pe_contract_below_ltp(
            buy_strike_price=atm_strike,
            max_ltp=35.0,
            symbol=self.symbol,
            option_rows=option_rows,
            current_date=current_date,
        )
        if not deep_itm_ce or not buy_pe or not sell_pe:
            return None

        try:
            deep_itm_ce_ltp = float(deep_itm_ce["ltp"])
            buy_pe_ltp = float(buy_pe["ltp"])
            sell_pe_ltp = float(sell_pe["ltp"])
        except Exception:
            return None

        return {
            "legs": [
                {
                    "action": "BUY",
                    "tradingsymbol": deep_itm_ce.get("tradingsymbol"),
                    "option_type": "CE",
                    "strike_price": int(deep_itm_ce["strike_price"]),
                    "expiry": str(deep_itm_ce.get("expiry_date") or deep_itm_ce.get("expiry") or "")[:10],
                    "last_price": deep_itm_ce_ltp,
                },
                {
                    "action": "BUY",
                    "tradingsymbol": buy_pe.get("tradingsymbol"),
                    "option_type": "PE",
                    "strike_price": int(buy_pe["strike_price"]),
                    "expiry": str(buy_pe.get("expiry_date") or buy_pe.get("expiry") or "")[:10],
                    "last_price": buy_pe_ltp,
                },
                {
                    "action": "SELL",
                    "tradingsymbol": sell_pe.get("tradingsymbol"),
                    "option_type": "PE",
                    "strike_price": int(sell_pe["strike_price"]),
                    "expiry": str(sell_pe.get("expiry_date") or sell_pe.get("expiry") or "")[:10],
                    "last_price": sell_pe_ltp,
                },
            ],
            "sell_pe_entry_ltp": sell_pe_ltp,
            "selected_contracts": {
                "deep_itm_ce": {
                    "tradingsymbol": deep_itm_ce.get("tradingsymbol"),
                    "strike_price": int(deep_itm_ce["strike_price"]),
                    "expiry": str(deep_itm_ce.get("expiry_date") or deep_itm_ce.get("expiry") or "")[:10],
                },
                "buy_pe": {
                    "tradingsymbol": buy_pe.get("tradingsymbol"),
                    "strike_price": int(buy_pe["strike_price"]),
                    "expiry": str(buy_pe.get("expiry_date") or buy_pe.get("expiry") or "")[:10],
                },
                "sell_pe": {
                    "tradingsymbol": sell_pe.get("tradingsymbol"),
                    "strike_price": int(sell_pe["strike_price"]),
                    "expiry": str(sell_pe.get("expiry_date") or sell_pe.get("expiry") or "")[:10],
                },
            },
        }

    def _current_type_a_diff(
        self,
        strike_bundle: Optional[Dict[str, Any]],
        option_rows: List[Dict[str, Any]],
    ) -> Optional[float]:
        if not strike_bundle:
            return None
        buy_ce = strike_bundle.get("buy_ce") or {}
        sell_ce = strike_bundle.get("sell_ce") or {}
        buy_ltp = fetch_current_exact_option_ltp(
            symbol=self.symbol,
            tradingsymbol=buy_ce.get("tradingsymbol"),
            strike_price=buy_ce.get("strike_price"),
            option_type="CE",
            expiry=buy_ce.get("expiry"),
            option_rows=option_rows,
        )
        sell_ltp = fetch_current_exact_option_ltp(
            symbol=self.symbol,
            tradingsymbol=sell_ce.get("tradingsymbol"),
            strike_price=sell_ce.get("strike_price"),
            option_type="CE",
            expiry=sell_ce.get("expiry"),
            option_rows=option_rows,
        )
        if buy_ltp is None or sell_ltp is None:
            return None
        return float(buy_ltp - sell_ltp)

    def _current_position_pnl(
        self,
        branch: Optional[Dict[str, Any]],
        option_rows: List[Dict[str, Any]],
    ) -> Optional[float]:
        if not self._position_is_open(branch):
            return None
        pnl = 0.0
        for leg in branch.get("legs") or []:
            current_price = fetch_current_exact_option_ltp(
                symbol=self.symbol,
                tradingsymbol=leg.get("tradingsymbol"),
                strike_price=leg.get("strike_price"),
                option_type=leg.get("option_type"),
                expiry=leg.get("expiry"),
                option_rows=option_rows,
            )
            if current_price is None:
                return None
            entry_price = float(leg.get("entry_price") or 0.0)
            side = str(leg.get("side") or "BUY").upper()
            if side == "BUY":
                pnl += float(current_price - entry_price)
            else:
                pnl += float(entry_price - current_price)
        return float(pnl)

    def _type_a_pos2_trigger_threshold(self, type_a_state: Dict[str, Any]) -> Optional[float]:
        entry_diff = type_a_state.get("position_1_entry_diff")
        if entry_diff is None:
            return None
        try:
            return float(entry_diff) - 5.0
        except Exception:
            return None

    def _append_blocker(self, status_branch: Dict[str, Any], blocker: str) -> None:
        blockers = status_branch.setdefault("blockers", [])
        if blocker not in blockers:
            blockers.append(blocker)

    def _append_pending_action(self, status_branch: Dict[str, Any], action_type: str) -> None:
        pending = status_branch.setdefault("pending_actions", [])
        pending.append(action_type)

    def _log_evaluation(self, status: Dict[str, Any], actions: List[Dict[str, Any]]) -> None:
        type_a = status.get("type_a") or {}
        type_b = status.get("type_b") or {}
        self.logger.info(
            "oi_eval ts=%s window_open=%s data_ready=%s spot=%s atm=%s actions=%s "
            "type_a_open=%s/%s type_a_diff=%s type_a_pnl=%s/%s type_a_blockers=%s "
            "type_b_open=%s type_b_pnl=%s type_b_triggers=%s/%s type_b_blockers=%s",
            status.get("evaluation_timestamp"),
            status.get("entry_window_open"),
            status.get("data_ready"),
            status.get("spot"),
            status.get("atm_strike"),
            ",".join(str(action.get("type")) for action in actions) or "none",
            type_a.get("position_1_open"),
            type_a.get("position_2_open"),
            type_a.get("current_diff"),
            type_a.get("position_1_pnl"),
            type_a.get("position_2_pnl"),
            ",".join(type_a.get("blockers") or []) or "none",
            type_b.get("position_open"),
            type_b.get("current_pnl"),
            type_b.get("trigger_1_seen"),
            type_b.get("trigger_2_seen"),
            ",".join(type_b.get("blockers") or []) or "none",
        )

    def evaluate(
        self,
        state: Optional[Dict[str, Any]],
        position: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        now = self._now()
        next_state = normalize_oi_state(copy.deepcopy(state))
        position = position or {}
        actions: List[Dict[str, Any]] = []

        type_a_pos1 = ((position.get("type_a") or {}).get("position_1") or {})
        type_a_pos2 = ((position.get("type_a") or {}).get("position_2") or {})
        type_b_pos = ((position.get("type_b") or {}).get("position") or {})

        df_1m = self._get_df("1m", limit=300)
        df_5m = self._get_df("5m", limit=300)
        latest_1m_ts = self._latest_ts(df_1m)
        evaluation_ts = latest_1m_ts or pd.Timestamp(now)

        status = {
            "selected_strategy": "oi_expiry" if self.is_tuesday(now) else "standard_nifty",
            "evaluation_timestamp": _to_iso(evaluation_ts),
            "entry_window_open": self._entry_window_open(now),
            "entry_price_below_1m_smas": False,
            "data_ready": False,
            "spot": None,
            "atm_strike": None,
            "type_a": {
                "entry_flag_on": False,
                "current_diff": None,
                "position_1_open": self._position_is_open(type_a_pos1),
                "position_2_open": self._position_is_open(type_a_pos2),
                "position_1_pnl": None,
                "position_2_pnl": None,
                "reentry_threshold": None,
                "blockers": [],
                "pending_actions": [],
            },
            "type_b": {
                "trigger_1_seen": False,
                "trigger_2_seen": False,
                "position_open": self._position_is_open(type_b_pos),
                "current_pnl": None,
                "blockers": [],
                "pending_actions": [],
            },
        }

        self.logger.info(
            "oi_eval_start ts=%s tuesday=%s pos_a1=%s pos_a2=%s pos_b=%s",
            status["evaluation_timestamp"],
            self.is_tuesday(now),
            status["type_a"]["position_1_open"],
            status["type_a"]["position_2_open"],
            status["type_b"]["position_open"],
        )

        if df_1m.empty or df_5m.empty:
            if df_1m.empty:
                self._append_blocker(status["type_a"], "missing_1m_data")
                self._append_blocker(status["type_b"], "missing_1m_data")
            if df_5m.empty:
                self._append_blocker(status["type_a"], "missing_5m_data")
                self._append_blocker(status["type_b"], "missing_5m_data")
            self.logger.warning(
                "oi_eval blocker=missing_market_data missing_1m=%s missing_5m=%s ts=%s",
                df_1m.empty,
                df_5m.empty,
                status["evaluation_timestamp"],
            )
            self._log_evaluation(status, actions)
            return {"timestamp": _to_iso(evaluation_ts), "actions": actions, "state": next_state, "status": status}

        latest_1m = df_1m.iloc[-1]
        latest_5m = df_5m.iloc[-1]
        current_date = (latest_1m_ts.to_pydatetime().date() if latest_1m_ts is not None else now.date())
        current_time = latest_1m_ts.to_pydatetime() if latest_1m_ts is not None else now

        option_rows = self._fetch_option_snapshot()
        oi_rows = self._fetch_open_interest_snapshot(current_time=current_time)
        oi_5m_rows = self._fetch_open_interest_5m_snapshot(current_time=current_time)

        try:
            spot = float(latest_1m["close"])
        except Exception:
            self._append_blocker(status["type_a"], "invalid_spot_price")
            self._append_blocker(status["type_b"], "invalid_spot_price")
            self.logger.warning("oi_eval blocker=invalid_spot_price ts=%s", status["evaluation_timestamp"])
            self._log_evaluation(status, actions)
            return {"timestamp": _to_iso(evaluation_ts), "actions": actions, "state": next_state, "status": status}

        atm_strike = floor_to_100_strike(spot)
        entry_flag_on = self._entry_flag_on(latest_1m, latest_5m)
        entry_price_below_1m_smas = self._entry_price_below_1m_smas(latest_1m)
        status["spot"] = float(spot)
        status["atm_strike"] = int(atm_strike)
        next_state["entry_price_below_1m_smas"] = bool(entry_price_below_1m_smas)
        next_state["type_a"]["entry_flag_on"] = bool(entry_flag_on)
        status["entry_price_below_1m_smas"] = bool(entry_price_below_1m_smas)
        status["data_ready"] = bool(option_rows) and bool(oi_rows)

        if not option_rows:
            self._append_blocker(status["type_a"], "missing_option_snapshot")
            self._append_blocker(status["type_b"], "missing_option_snapshot")
            self.logger.warning("oi_eval blocker=missing_option_snapshot ts=%s", status["evaluation_timestamp"])
        if not oi_rows:
            self._append_blocker(status["type_a"], "missing_oi_snapshot")
            self._append_blocker(status["type_b"], "missing_oi_snapshot")
            self.logger.warning("oi_eval blocker=missing_oi_snapshot ts=%s", status["evaluation_timestamp"])
        if not oi_5m_rows:
            self._append_blocker(status["type_b"], "missing_oi_5m_snapshot")

        current_diff = self._current_type_a_diff(next_state["type_a"].get("strike_bundle"), option_rows) if option_rows else None
        type_a_pos1_pnl = self._current_position_pnl(type_a_pos1, option_rows) if option_rows else None
        type_a_pos2_pnl = self._current_position_pnl(type_a_pos2, option_rows) if option_rows else None
        type_a_trigger_threshold = self._type_a_pos2_trigger_threshold(next_state["type_a"])
        status["type_a"]["entry_flag_on"] = bool(entry_flag_on)
        status["type_a"]["current_diff"] = current_diff
        status["type_a"]["position_1_pnl"] = type_a_pos1_pnl
        status["type_a"]["position_2_pnl"] = type_a_pos2_pnl
        status["type_a"]["reentry_threshold"] = type_a_trigger_threshold
        if status["type_a"]["position_1_open"] and current_diff is None:
            self._append_blocker(status["type_a"], "type_a_diff_unavailable")
        if status["type_a"]["position_1_open"] and type_a_pos1_pnl is None:
            self._append_blocker(status["type_a"], "type_a_position_1_pnl_unavailable")
        if status["type_a"]["position_2_open"] and type_a_pos2_pnl is None:
            self._append_blocker(status["type_a"], "type_a_position_2_pnl_unavailable")

        if self._position_is_open(type_a_pos1) and current_diff is not None:
            if current_diff <= 12.0:
                action = {
                    "type": "CLOSE_TYPE_A_ALL",
                    "reason": "position_1_sl",
                    "current_diff": current_diff,
                }
                actions.append(action)
                self._append_pending_action(status["type_a"], action["type"])
            elif current_diff >= 45.0:
                action = {
                    "type": "CLOSE_TYPE_A_POS1",
                    "reason": "position_1_target",
                    "current_diff": current_diff,
                }
                actions.append(action)
                self._append_pending_action(status["type_a"], action["type"])
            elif (not self._position_is_open(type_a_pos2)) and type_a_trigger_threshold is not None:
                if current_diff > type_a_trigger_threshold:
                    next_state["type_a"]["flag_1"] = False
                    if (
                        next_state["type_a"].get("position_2_trigger_consumed")
                        and int(next_state["type_a"].get("position_2_reentry_count") or 0) < 1
                    ):
                        next_state["type_a"]["position_2_trigger_consumed"] = False
                        next_state["type_a"]["position_2_rearmed"] = True
                elif (
                    current_diff <= type_a_trigger_threshold
                    and not next_state["type_a"].get("position_2_trigger_consumed")
                ):
                    if (
                        (not next_state["type_a"].get("position_2_rearmed"))
                        or int(next_state["type_a"].get("position_2_reentry_count") or 0) < 1
                    ):
                        next_state["type_a"]["flag_1"] = True
            elif type_a_trigger_threshold is None:
                self._append_blocker(status["type_a"], "type_a_reentry_threshold_unavailable")

        if self._position_is_open(type_a_pos2) and current_diff is not None:
            if current_diff <= 12.0:
                action = {
                    "type": "CLOSE_TYPE_A_POS2",
                    "reason": "position_2_sl",
                    "current_diff": current_diff,
                }
                actions.append(action)
                self._append_pending_action(status["type_a"], action["type"])
            elif current_diff >= 50.0:
                action = {
                    "type": "CLOSE_TYPE_A_POS2",
                    "reason": "position_2_target",
                    "current_diff": current_diff,
                }
                actions.append(action)
                self._append_pending_action(status["type_a"], action["type"])

        type_b_pnl = self._current_position_pnl(type_b_pos, option_rows) if option_rows else None
        status["type_b"]["trigger_1_seen"] = bool(next_state["type_b"].get("trigger_1_seen"))
        status["type_b"]["trigger_2_seen"] = bool(next_state["type_b"].get("trigger_2_seen"))
        status["type_b"]["current_pnl"] = type_b_pnl
        if status["type_b"]["position_open"] and type_b_pnl is None:
            self._append_blocker(status["type_b"], "type_b_pnl_unavailable")

        if self._position_is_open(type_b_pos):
            sell_pe = ((next_state["type_b"].get("selected_contracts") or {}).get("sell_pe") or {})
            sell_pe_ltp = fetch_current_exact_option_ltp(
                symbol=self.symbol,
                tradingsymbol=sell_pe.get("tradingsymbol"),
                strike_price=sell_pe.get("strike_price"),
                option_type="PE",
                expiry=sell_pe.get("expiry"),
                option_rows=option_rows,
            )
            sell_pe_entry_ltp = next_state["type_b"].get("sell_pe_entry_ltp")
            if sell_pe_ltp is not None and sell_pe_entry_ltp is not None and sell_pe_ltp > (float(sell_pe_entry_ltp) + 20.0):
                action = {
                    "type": "CLOSE_TYPE_B_POSITION",
                    "reason": "sell_pe_rise",
                    "sell_pe_ltp": sell_pe_ltp,
                }
                actions.append(action)
                self._append_pending_action(status["type_b"], action["type"])
            else:
                if sell_pe_ltp is None:
                    self._append_blocker(status["type_b"], "type_b_sell_pe_ltp_unavailable")
                current_sh = latest_1m.get("sh")
                try:
                    current_high = float(latest_1m["high"])
                    current_sh_value = float(current_sh) if current_sh is not None else None
                except Exception:
                    current_high = None
                    current_sh_value = None
                if current_high is not None and current_sh_value is not None and current_high > current_sh_value:
                    action = {
                        "type": "CLOSE_TYPE_B_POSITION",
                        "reason": "sh_breach",
                        "high": current_high,
                        "sh": current_sh_value,
                    }
                    actions.append(action)
                    self._append_pending_action(status["type_b"], action["type"])
                elif type_b_pnl is not None and type_b_pnl > 3000.0:
                    action = {
                        "type": "CLOSE_TYPE_B_POSITION",
                        "reason": "profit_target",
                        "current_pnl": type_b_pnl,
                    }
                    actions.append(action)
                    self._append_pending_action(status["type_b"], action["type"])

        if not self._entry_window_open(now):
            self._append_blocker(status["type_a"], "entry_window_closed")
            self._append_blocker(status["type_b"], "entry_window_closed")
            self._log_evaluation(status, actions)
            return {"timestamp": _to_iso(evaluation_ts), "actions": actions, "state": next_state, "status": status}

        if (
            (not self._position_is_open(type_a_pos1))
            and (not self._position_is_open(type_a_pos2))
            and entry_flag_on
            and entry_price_below_1m_smas
        ):
            oi_result = self._evaluate_universal_oi(atm_strike=atm_strike, oi_rows=oi_rows, current_date=current_date)
            if oi_result["passed"]:
                type_a_payload = self._build_type_a_legs(atm_strike=atm_strike, option_rows=option_rows, current_date=current_date)
                if type_a_payload:
                    action = {
                        "type": "OPEN_TYPE_A_POS1",
                        "reason": "entry_flag_and_oi",
                        "entry_price_below_1m_smas": True,
                        **type_a_payload,
                        "oi_contracts": oi_result["contracts"],
                    }
                    actions.append(action)
                    self._append_pending_action(status["type_a"], action["type"])
                else:
                    self._append_blocker(status["type_a"], "type_a_legs_unavailable")
                    self.logger.warning(
                        "oi_eval blocker=type_a_legs_unavailable ts=%s atm=%s",
                        status["evaluation_timestamp"],
                        atm_strike,
                    )
            else:
                self._append_blocker(status["type_a"], str(oi_result.get("reason") or "oi_filter_failed"))
                self.logger.info(
                    "oi_eval blocker=%s ts=%s pe_oi=%s ce_oi=%s otm_ce_oi=%s",
                    oi_result.get("reason"),
                    status["evaluation_timestamp"],
                    oi_result.get("pe_oi"),
                    oi_result.get("ce_oi"),
                    oi_result.get("otm_ce_oi"),
                )
        elif self._position_is_open(type_a_pos1) and (not self._position_is_open(type_a_pos2)) and next_state["type_a"].get("flag_1"):
            strike_bundle = next_state["type_a"].get("strike_bundle") or {}
            bundle_atm = strike_bundle.get("atm_strike")
            if bundle_atm is not None:
                oi_result = self._evaluate_universal_oi(atm_strike=int(bundle_atm), oi_rows=oi_rows, current_date=current_date)
                if oi_result["passed"]:
                    type_a_payload = self._build_type_a_legs(atm_strike=int(bundle_atm), option_rows=option_rows, current_date=current_date)
                    if type_a_payload:
                        reentry_kind = "reopen" if next_state["type_a"].get("position_2_rearmed") else "initial"
                        next_state["type_a"]["flag_2"] = True
                        action = {
                            "type": "OPEN_TYPE_A_POS2",
                            "reason": "flag_1_and_oi_recheck",
                            "reentry_kind": reentry_kind,
                            **type_a_payload,
                            "oi_contracts": oi_result["contracts"],
                        }
                        actions.append(action)
                        self._append_pending_action(status["type_a"], action["type"])
                    else:
                        self._append_blocker(status["type_a"], "type_a_reentry_legs_unavailable")
                else:
                    self._append_blocker(status["type_a"], str(oi_result.get("reason") or "oi_filter_failed"))
            else:
                self._append_blocker(status["type_a"], "missing_strike_bundle")
        elif (not self._position_is_open(type_a_pos1)) and (not self._position_is_open(type_a_pos2)):
            if not entry_flag_on:
                self._append_blocker(status["type_a"], "entry_flag_off")
            if not entry_price_below_1m_smas:
                self._append_blocker(status["type_a"], "entry_price_not_below_1m_smas")

        if not self._position_is_open(type_b_pos):
            trigger_1 = False
            trigger_2 = False

            try:
                trigger_1 = abs(float(latest_1m["close"]) - float(latest_5m["sma_50"])) <= 10.0
            except Exception:
                trigger_1 = False

            deep_itm_ce_strike = atm_strike - 200
            deep_itm_ce_ltp = fetch_current_exact_option_ltp(
                symbol=self.symbol,
                strike_price=deep_itm_ce_strike,
                option_type="CE",
                option_rows=option_rows,
            )
            deep_itm_ce_vwap = fetch_latest_oi_vwap_5m_for_contract(
                strike_price=deep_itm_ce_strike,
                option_type="CE",
                symbol=self.symbol,
                oi_5m_rows=oi_5m_rows,
                current_time=current_time,
                current_date=current_date,
            )
            if deep_itm_ce_ltp is not None and deep_itm_ce_vwap is not None:
                trigger_2 = abs(float(deep_itm_ce_ltp) - float(deep_itm_ce_vwap)) <= 5.0
            else:
                self._append_blocker(status["type_b"], "type_b_trigger_2_data_unavailable")

            next_state["type_b"]["trigger_1_seen"] = bool(trigger_1)
            next_state["type_b"]["trigger_2_seen"] = bool(trigger_2)
            status["type_b"]["trigger_1_seen"] = bool(trigger_1)
            status["type_b"]["trigger_2_seen"] = bool(trigger_2)

            if (trigger_1 or trigger_2) and entry_price_below_1m_smas:
                oi_result = self._evaluate_universal_oi(atm_strike=atm_strike, oi_rows=oi_rows, current_date=current_date)
                if oi_result["passed"]:
                    type_b_payload = self._build_type_b_legs(atm_strike=atm_strike, option_rows=option_rows, current_date=current_date)
                    if type_b_payload:
                        action = {
                            "type": "OPEN_TYPE_B_POSITION",
                            "reason": "trigger_and_oi",
                            "trigger_1": trigger_1,
                            "trigger_2": trigger_2,
                            "entry_price_below_1m_smas": True,
                            **type_b_payload,
                            "oi_contracts": oi_result["contracts"],
                        }
                        actions.append(action)
                        self._append_pending_action(status["type_b"], action["type"])
                    else:
                        self._append_blocker(status["type_b"], "type_b_legs_unavailable")
                else:
                    self._append_blocker(status["type_b"], str(oi_result.get("reason") or "oi_filter_failed"))
                    self.logger.info(
                        "oi_eval blocker=%s ts=%s pe_oi=%s ce_oi=%s otm_ce_oi=%s",
                        oi_result.get("reason"),
                        status["evaluation_timestamp"],
                        oi_result.get("pe_oi"),
                        oi_result.get("ce_oi"),
                        oi_result.get("otm_ce_oi"),
                    )
            elif not trigger_1 and not trigger_2:
                self._append_blocker(status["type_b"], "type_b_triggers_not_met")
            elif not entry_price_below_1m_smas:
                self._append_blocker(status["type_b"], "entry_price_not_below_1m_smas")

        status["type_b"]["trigger_1_seen"] = bool(next_state["type_b"].get("trigger_1_seen"))
        status["type_b"]["trigger_2_seen"] = bool(next_state["type_b"].get("trigger_2_seen"))
        self._log_evaluation(status, actions)
        return {
            "timestamp": _to_iso(evaluation_ts),
            "actions": actions,
            "state": next_state,
            "status": status,
        }
