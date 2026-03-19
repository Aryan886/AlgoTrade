from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd

from utils.db_func import fetch_latest_delta_data, fetch_market_data, fetch_vix_data
from utils.utility import setup_paper_trading_logger


LegAction = Literal["BUY", "SELL"]
OptionType = Literal["CE", "PE"]


def _nearest_100(x: float) -> int:
    # Nearest 100 with .5 rounding up (not bankers rounding)
    return int(math.floor((float(x) + 50.0) / 100.0) * 100)


def _ceil_100(x: float) -> int:
    return int(math.ceil(float(x) / 100.0) * 100)


def _rsi_stub(_df_5m: pd.DataFrame) -> Optional[float]:
    """
    TODO (Spec Section 6): RSI Filter Signal.
    Intentionally not implemented/wired yet per user instruction.
    """
    return None


def _rolling_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def _ensure_cols(df: pd.DataFrame, cols: List[str]) -> bool:
    return df is not None and not df.empty and all(c in df.columns for c in cols)


def _to_iso(ts: pd.Timestamp) -> str:
    # Consistent string for JSON persistence
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return str(ts)


def _parse_ts(ts_str: Optional[str]) -> Optional[pd.Timestamp]:
    if not ts_str:
        return None
    try:
        return pd.Timestamp(ts_str)
    except Exception:
        return None


@dataclass(frozen=True)
class StrikePlan:
    spot: float
    pe_strike: int
    ce_strike_near: int
    pe_strike_upper: int


class NiftyOptionsStrategy:
    """
    Stateful Nifty options strategy based on STRATEGY_NIFTY.md.

    Key design constraints (explicit):
    - Multi-leg from day one (Type A has 3 legs).
    - Flags are latched and persisted to JSON.
    - Flags + last-processed timestamps hard-reset at 09:15 each session.
    - Section 2 (RSI) is a stub/TODO only for now (not wired).
    """

    SESSION_RESET_TIME = dtime(9, 15)

    def __init__(
        self,
        symbol: str = "NIFTY50",
        state_file: str = "nifty_strategy_state.json",
    ) -> None:
        self.symbol = symbol or "NIFTY50"
        self.state_file = state_file

        _paper_logger, _trade_logger, _position_logger, _sma_logger, _equity_logger, nifty_logger = setup_paper_trading_logger()
        self.logger = nifty_logger
        self.trade_logger = nifty_logger
        self.position_logger = nifty_logger

        self.state: Dict[str, Any] = self._load_state()

    # ---------------------------
    # Persistence + session reset
    # ---------------------------
    def _default_state(self) -> Dict[str, Any]:
        return {
            "last_reset_date": None,  # YYYY-MM-DD
            "last_processed": {"1m": None, "5m": None, "15m": None, "1h": None},
            "section1_flags": {k: False for k in list("abcdefghijklmno")},
            "section1_last_touch": {k: None for k in list("abcdefghijklmno")},  # timestamp strings
            "section3": {
                "break_latched": False,
                "break_reason": None,  # "sma20"|"donchian_mid"|None
                "break_time": None,
            },
        }

    def _load_state(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_file):
            return self._default_state()
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            base = self._default_state()
            # shallow merge for known keys
            base.update({k: data.get(k, base[k]) for k in base.keys()})
            # ensure nested dicts exist
            base["last_processed"] = {**base["last_processed"], **(data.get("last_processed") or {})}
            base["section1_flags"] = {**base["section1_flags"], **(data.get("section1_flags") or {})}
            base["section1_last_touch"] = {**base["section1_last_touch"], **(data.get("section1_last_touch") or {})}
            base["section3"] = {**base["section3"], **(data.get("section3") or {})}
            return base
        except Exception as e:
            self.logger.warning(f"Failed to load state file '{self.state_file}': {e}. Resetting.")
            return self._default_state()

    def save_state(self) -> None:
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to persist strategy state: {e}")

    def maybe_session_reset(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        today = now.date().isoformat()
        last_reset_date = self.state.get("last_reset_date")
        if last_reset_date == today:
            return False

        # Hard reset is considered "due" once we are past (or at) 09:15 local time.
        if now.time() < self.SESSION_RESET_TIME:
            return False

        self.logger.info("NIFTY STRATEGY SESSION RESET at 09:15: clearing flags and last-processed timestamps.")
        self.state = self._default_state()
        self.state["last_reset_date"] = today
        self.save_state()
        return True

    # ---------------------------
    # Data helpers
    # ---------------------------
    def _get_df(self, interval: str, limit: Optional[int] = 300) -> pd.DataFrame:
        df = fetch_market_data(symbol=self.symbol, interval=interval, limit=limit)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        return df

    def _compute_required_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Ensures required columns exist using in-memory computations where needed.
        This avoids dependency on separate SMA tables for the index itself.
        """
        if df is None or df.empty:
            return df

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        # SMAs on close
        if "sma_20" not in df.columns:
            df["sma_20"] = _rolling_sma(df["close"], 20)
        if "sma_50" not in df.columns:
            df["sma_50"] = _rolling_sma(df["close"], 50)
        if "sma_200" not in df.columns:
            df["sma_200"] = _rolling_sma(df["close"], 200)

        # 1m only: sma_5_low is SMA of lows
        if "sma_5_low" not in df.columns:
            df["sma_5_low"] = df["low"].rolling(window=5, min_periods=5).mean()

        return df

    def _latest_ts(self, df: pd.DataFrame) -> Optional[pd.Timestamp]:
        if df is None or df.empty:
            return None
        try:
            return pd.Timestamp(df.index[-1])
        except Exception:
            return None

    def _is_new_candle(self, interval: str, df: pd.DataFrame) -> bool:
        latest = self._latest_ts(df)
        if latest is None:
            return False
        last = _parse_ts(self.state.get("last_processed", {}).get(interval))
        return last is None or latest > last

    def mark_processed(self, interval: str, ts: pd.Timestamp) -> None:
        self.state.setdefault("last_processed", {})[interval] = _to_iso(ts)
        self.save_state()

    # ---------------------------
    # Spec logic
    # ---------------------------
    def main_category_a_active(self, df_1h: pd.DataFrame) -> bool:
        df_1h = self._compute_required_indicators(df_1h)
        if not _ensure_cols(df_1h, ["close", "sma_20", "sma_50", "sma_200"]):
            return False
        last = df_1h.iloc[-1]
        # All three must be simultaneously true
        try:
            return (last["close"] < last["sma_20"]) and (last["close"] < last["sma_50"]) and (last["close"] < last["sma_200"])
        except Exception:
            return False

    def _vix_regime(self) -> Optional[Literal["A", "B"]]:
        vix = fetch_vix_data(symbol=self.symbol)
        if vix is None or vix.empty or "vix_value" not in vix.columns:
            self.logger.warning("VIX data missing; cannot determine VIX regime.")
            return None
        vix = vix.copy()
        vix["vix_sma20"] = vix["vix_value"].rolling(window=20, min_periods=20).mean()
        last = vix.iloc[-1]
        if pd.isna(last.get("vix_sma20")):
            self.logger.warning("VIX SMA20 not ready; cannot determine VIX regime.")
            return None
        return "A" if float(last["vix_value"]) > float(last["vix_sma20"]) else "B"

    def _spot_price(self, df_1m: pd.DataFrame) -> Optional[float]:
        if df_1m is None or df_1m.empty or "close" not in df_1m.columns:
            return None
        try:
            return float(df_1m.iloc[-1]["close"])
        except Exception:
            return None

    def _strike_plan(self, spot: float, options_data: List[Dict[str, Any]]) -> Optional[StrikePlan]:
        if spot is None or not options_data:
            return None

        pe_strike = _nearest_100(spot)
        pe_strike_upper = _ceil_100(spot + 200.0)

        ce_strike_near = self._select_ce_strike_near(spot, options_data)
        if ce_strike_near is None:
            return None

        return StrikePlan(
            spot=float(spot),
            pe_strike=int(pe_strike),
            ce_strike_near=int(ce_strike_near),
            pe_strike_upper=int(pe_strike_upper),
        )

    def _select_ce_strike_near(self, spot: float, options_data: List[Dict[str, Any]]) -> Optional[int]:
        """
        Spec clarification:
        - Must be the closest available CE strike to spot within ±20 pts strictly.
        - If none exists within ±20, log a warning and choose the closest strike to the ±20 boundary.
        """
        ce = []
        for o in options_data:
            if (o.get("option_type") or "").upper() != "CE":
                continue
            strike = o.get("strike_price")
            if strike is None:
                continue
            try:
                ce.append(float(strike))
            except Exception:
                continue

        if not ce:
            self.logger.warning("No CE strikes available in options cache.")
            return None

        in_range = [s for s in ce if abs(s - spot) <= 20.0]
        if in_range:
            # closest to spot (tie-breaker: lower abs, then lower strike)
            chosen = min(in_range, key=lambda s: (abs(s - spot), s))
            return int(chosen)

        # fallback: closest to the interval [spot-20, spot+20]
        lower, upper = spot - 20.0, spot + 20.0
        def dist_to_range(s: float) -> float:
            if s < lower:
                return lower - s
            if s > upper:
                return s - upper
            return 0.0

        chosen = min(ce, key=lambda s: (dist_to_range(s), abs(s - spot), s))
        self.logger.warning(
            f"No CE strike found within ±20 of spot={spot:.2f}. "
            f"Fallback selecting CE strike {int(chosen)} (closest to the ±20 boundary)."
        )
        return int(chosen)

    def _pick_option(self, options_data: List[Dict[str, Any]], option_type: OptionType, strike: int) -> Optional[Dict[str, Any]]:
        candidates = []
        for o in options_data:
            if (o.get("option_type") or "").upper() != option_type:
                continue
            sp = o.get("strike_price")
            if sp is None:
                continue
            try:
                if int(float(sp)) == int(strike):
                    candidates.append(o)
            except Exception:
                continue

        if not candidates:
            self.logger.warning(f"Option not found in cache: {option_type} {strike}")
            return None

        # pick highest OI? not available in delta_cache; pick highest ltp if multiple
        def ltp_key(x: Dict[str, Any]) -> float:
            try:
                return float(x.get("ltp") or 0.0)
            except Exception:
                return 0.0

        chosen = min(candidates, key=lambda x: (x.get("expiry") or "", -ltp_key(x)))
        # standardize fields for downstream code
        chosen = dict(chosen)
        chosen["tradingsymbol"] = chosen.get("tradingsymbol")
        chosen["last_price"] = chosen.get("ltp")
        chosen["option_type"] = option_type
        chosen["strike_price"] = int(strike)
        return chosen

    def _build_legs(self, position_type: Literal["A", "B"], strikes: StrikePlan, options_data: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """
        Return legs as list of dicts:
        {tradingsymbol, option_type, strike_price, action, entry_price_hint}
        """
        legs: List[Dict[str, Any]] = []
        if position_type == "A":
            # BUY CE near, BUY PE upper, SELL CE upper
            ce_near = self._pick_option(options_data, "CE", strikes.ce_strike_near)
            pe_upper = self._pick_option(options_data, "PE", strikes.pe_strike_upper)
            ce_upper = self._pick_option(options_data, "CE", strikes.pe_strike_upper)
            if not ce_near or not pe_upper or not ce_upper:
                return None
            legs.append({"action": "BUY", "tradingsymbol": ce_near["tradingsymbol"], "option_type": "CE", "strike_price": strikes.ce_strike_near, "last_price": ce_near.get("last_price")})
            legs.append({"action": "BUY", "tradingsymbol": pe_upper["tradingsymbol"], "option_type": "PE", "strike_price": strikes.pe_strike_upper, "last_price": pe_upper.get("last_price")})
            legs.append({"action": "SELL", "tradingsymbol": ce_upper["tradingsymbol"], "option_type": "CE", "strike_price": strikes.pe_strike_upper, "last_price": ce_upper.get("last_price")})
            return legs

        # position_type == "B"
        # SELL PE strike, BUY PE strike + 100
        pe_sell = self._pick_option(options_data, "PE", strikes.pe_strike)
        pe_buy = self._pick_option(options_data, "PE", strikes.pe_strike + 100)
        if not pe_sell or not pe_buy:
            return None
        legs.append({"action": "SELL", "tradingsymbol": pe_sell["tradingsymbol"], "option_type": "PE", "strike_price": strikes.pe_strike, "last_price": pe_sell.get("last_price")})
        legs.append({"action": "BUY", "tradingsymbol": pe_buy["tradingsymbol"], "option_type": "PE", "strike_price": strikes.pe_strike + 100, "last_price": pe_buy.get("last_price")})
        return legs

    # ---------------------------
    # Section 1: subcategories a-o
    # ---------------------------
    def _section1_subcategory_defs(self) -> Dict[str, Tuple[str, str]]:
        # subcat -> (interval, indicator)
        return {
            "a": ("1m", "sma_20"),
            "b": ("1m", "sma_50"),
            "c": ("1m", "sma_200"),
            "d": ("5m", "sma_20"),
            "e": ("5m", "donchian_mid"),
            "f": ("5m", "sma_50"),
            "g": ("5m", "sma_200"),
            "h": ("15m", "sma_20"),
            "i": ("15m", "donchian_mid"),
            "j": ("15m", "sma_50"),
            "k": ("15m", "sma_200"),
            "l": ("1h", "sma_20"),
            "m": ("1h", "donchian_mid"),
            "n": ("1h", "sma_50"),
            "o": ("1h", "sma_200"),
        }

    def _priority_sort_key(self, subcat: str) -> Tuple[int, int]:
        # Lower is higher priority
        tf_rank = {"1m": 0, "5m": 1, "15m": 2, "1h": 3}
        ind_rank = {"sma_20": 0, "sma_50": 1, "sma_200": 2, "donchian_mid": 3}
        interval, indicator = self._section1_subcategory_defs()[subcat]
        return (tf_rank[interval], ind_rank[indicator])

    def _detect_touches_and_latch(self, data_by_interval: Dict[str, pd.DataFrame]) -> List[str]:
        """
        For each subcategory a–o, if its timeframe candle's HIGH touches the indicator level, latch its flag.
        Returns the list of subcategories that *newly* latched on this call.
        """
        newly = []
        defs = self._section1_subcategory_defs()
        for subcat, (interval, indicator) in defs.items():
            df = data_by_interval.get(interval)
            if df is None or df.empty:
                continue
            df = self._compute_required_indicators(df)
            if not _ensure_cols(df, ["high", indicator]):
                continue

            last = df.iloc[-1]
            ind_val = last.get(indicator)
            if pd.isna(ind_val):
                continue

            touched = float(last["high"]) >= float(ind_val)
            if touched and not bool(self.state["section1_flags"].get(subcat)):
                self.state["section1_flags"][subcat] = True
                self.state["section1_last_touch"][subcat] = _to_iso(pd.Timestamp(df.index[-1]))
                newly.append(subcat)

        if newly:
            self.save_state()
        return newly

    def _second_flag_ok(self, df_1m: pd.DataFrame) -> bool:
        df_1m = self._compute_required_indicators(df_1m)
        if not _ensure_cols(df_1m, ["close", "sma_20", "sma_5_low"]):
            return False
        last = df_1m.iloc[-1]
        try:
            return (float(last["close"]) < float(last["sma_20"])) and (float(last["close"]) < float(last["sma_5_low"]))
        except Exception:
            return False

    def _sl_filter_ok(self, df_1m: pd.DataFrame, lookback: int, threshold_pts: float) -> bool:
        """
        SL filter in spec:
        SL pts = highest high last N candles (including current) - current close
        must be <= threshold to proceed.
        """
        if df_1m is None or df_1m.empty or "high" not in df_1m.columns or "close" not in df_1m.columns:
            return False
        if len(df_1m) < lookback:
            return False
        window = df_1m.iloc[-lookback:]
        hh = float(window["high"].max())
        close = float(df_1m.iloc[-1]["close"])
        sl_pts = hh - close
        return sl_pts <= float(threshold_pts)

    # ---------------------------
    # Section 3: break & retest
    # ---------------------------
    def _update_section3_break_latch(self, df_5m: pd.DataFrame) -> bool:
        df_5m = self._compute_required_indicators(df_5m)
        if not _ensure_cols(df_5m, ["close", "sma_20", "donchian_mid"]):
            return False
        last = df_5m.iloc[-1]
        close = float(last["close"])
        broke = False
        reason = None
        if close < float(last["sma_20"]):
            broke = True
            reason = "sma20"
        if close < float(last["donchian_mid"]):
            broke = True
            reason = "donchian_mid" if reason is None else reason
        if broke and not bool(self.state["section3"].get("break_latched")):
            self.state["section3"]["break_latched"] = True
            self.state["section3"]["break_reason"] = reason
            self.state["section3"]["break_time"] = _to_iso(pd.Timestamp(df_5m.index[-1]))
            self.save_state()
            return True
        return False

    def _section3_retest_ok(self, df_5m: pd.DataFrame) -> bool:
        if not bool(self.state["section3"].get("break_latched")):
            return False
        df_5m = self._compute_required_indicators(df_5m)
        if not _ensure_cols(df_5m, ["high", "sma_20", "donchian_mid"]):
            return False
        break_time = _parse_ts(self.state["section3"].get("break_time"))
        current_ts = self._latest_ts(df_5m)
        if break_time is None or current_ts is None or current_ts <= break_time:
            return False
        last = df_5m.iloc[-1]
        high = float(last["high"])
        return (high >= float(last["sma_20"])) or (high >= float(last["donchian_mid"]))

    # ---------------------------
    # Public: produce entry intent
    # ---------------------------
    def evaluate_for_entry(self) -> Optional[Dict[str, Any]]:
        """
        Returns an entry intent dict or None.
        This does NOT place orders or manage positions.
        """
       # self.maybe_session_reset()

        # Load latest candles (keep moderate history for rolling calcs)
        df_1m = self._compute_required_indicators(self._get_df("1m", limit=300))
        df_5m = self._compute_required_indicators(self._get_df("5m", limit=300))
        df_15m = self._compute_required_indicators(self._get_df("15m", limit=400))
        df_1h = self._compute_required_indicators(self._get_df("1h", limit=400))

        if df_1m.empty or df_5m.empty or df_1h.empty:
            return None

        # Main Category A gate
        if not self.main_category_a_active(df_1h):
            return None

        # Latch new subcategory touches (Section 1)
        self._detect_touches_and_latch({"1m": df_1m, "5m": df_5m, "15m": df_15m, "1h": df_1h})

        # Update Section 3 break latch on new 5m candles (it is evaluated on 5m candle closes)
        latest_5m_ts = self._latest_ts(df_5m)
        if latest_5m_ts is not None and self._is_new_candle("5m", df_5m):
            self._update_section3_break_latch(df_5m)
            self.mark_processed("5m", latest_5m_ts)

        vix_regime = self._vix_regime()
        if vix_regime is None:
            return None

        position_type: Literal["A", "B"] = vix_regime
        sl_threshold = 25.0 if position_type == "A" else 35.0

        # Section 1 entry: requires any latched subcategory + 2nd flag + SL filter(7)
        section1_ok = False
        active_subcats = [k for k, v in (self.state.get("section1_flags") or {}).items() if v]
        if active_subcats:
            if self._second_flag_ok(df_1m) and self._sl_filter_ok(df_1m, lookback=7, threshold_pts=sl_threshold):
                section1_ok = True

        # Section 2: stub only (explicitly not wired)
        _ = _rsi_stub(df_5m)

        # Section 3 entry: break latched + retest + confirmation(2nd flag) + SL filter(7)
        section3_ok = False
        if self._section3_retest_ok(df_5m):
            if self._second_flag_ok(df_1m) and self._sl_filter_ok(df_1m, lookback=7, threshold_pts=sl_threshold):
                section3_ok = True

        if not (section1_ok or section3_ok):
            return None

        # If Section 1 triggers, apply subcategory priority among latched ones
        chosen_subcat = None
        if section1_ok and active_subcats:
            chosen_subcat = sorted(active_subcats, key=self._priority_sort_key)[0]

        # Build legs from options cache
        options_data = fetch_latest_delta_data(symbol=self.symbol)
        if not options_data:
            self.logger.warning("Options cache missing; cannot form legs.")
            return None

        spot = self._spot_price(df_1m)
        if spot is None:
            return None
        strikes = self._strike_plan(spot, options_data)
        if strikes is None:
            return None

        legs = self._build_legs(position_type, strikes, options_data)
        if not legs:
            return None

        now_ts = self._latest_ts(df_1m) or pd.Timestamp.now()
        reason = {
            "section": "section1" if section1_ok else "section3",
            "subcat": chosen_subcat,
            "position_type": position_type,
        }

        return {
            "symbol": self.symbol,
            "timestamp": _to_iso(now_ts),
            "reason": reason,
            "spot": strikes.spot,
            "position_type": position_type,
            "strikes": {
                "pe_strike": strikes.pe_strike,
                "ce_strike_near": strikes.ce_strike_near,
                "pe_strike_upper": strikes.pe_strike_upper,
            },
            "legs": legs,  # list[leg]
        }

