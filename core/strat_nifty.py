from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd

from utils.db_func import (
    calculate_and_store_high_accuracy_delta,
    fetch_latest_delta_data,
    fetch_market_data,
    fetch_vix_data,
)
from utils.market_data_1h import build_1h_market_data_from_15m
from utils.utility import setup_paper_trading_logger


LegAction = Literal["BUY", "SELL"]
OptionType = Literal["CE", "PE"]


def _nearest_100(x: float) -> int:
    # Nearest 100 with .5 rounding up (not bankers rounding)
    return int(math.floor((float(x) + 50.0) / 100.0) * 100)


def _ceil_100(x: float) -> int:
    return int(math.ceil(float(x) / 100.0) * 100)


def _simple_rsi(df: pd.DataFrame, period: int = 14, price_col: str = "close") -> Optional[float]:
    """
    Compute the latest simple RSI from the most recent closed window.

    Spec alignment:
    - Section 6 calls for "5min RSI (14, simple)".
    """
    if df is None or df.empty or price_col not in df.columns:
        return None
    if period <= 0:
        raise ValueError("period must be greater than 0")

    prices = pd.to_numeric(df[price_col], errors="coerce").dropna()
    if prices.empty or len(prices) < (period + 1):
        return None

    recent = prices.iloc[-(period + 1):]
    delta = recent.diff().iloc[1:]
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)

    avg_gain = float(gains.sum()) / float(period)
    avg_loss = float(losses.sum()) / float(period)

    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    if avg_gain == 0.0:
        return 0.0

    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


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
    - Section 2 entry flow is still TODO, but the RSI helper now exists.
    """

    SESSION_RESET_TIME = dtime(9, 15)
    ENTRY_CUTOFF_TIME = dtime(15, 00)  # 3:00 pm cutoff to allow order placement before market close

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

    def _now(self) -> datetime:
        return datetime.now()

    def _entry_cutoff_reached(self, now: Optional[datetime] = None) -> bool:
        now = now or self._now()
        return now.time() >= self.ENTRY_CUTOFF_TIME

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
        now = now or self._now()
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
    def _fetch_market_data(self, interval: str, limit: Optional[int]) -> pd.DataFrame:
        """Override this in BacktestableStrategy to use HistoricalDataProvider."""
        if interval != "1h":
            return fetch_market_data(symbol=self.symbol, interval=interval, limit=limit)

        try:
            df_1h = fetch_market_data(symbol=self.symbol, interval=interval, limit=limit)
            if df_1h is not None and not df_1h.empty:
                return df_1h
            self.logger.warning("market_data_1h is empty; falling back to resampled 15m data for live strategy evaluation.")
        except Exception as exc:
            self.logger.warning(f"market_data_1h unavailable; falling back to resampled 15m data: {exc}")

        return self._build_1h_from_15m_fallback(limit=limit)

    def _fetch_vix_data(self) -> pd.DataFrame:
        """Override this in BacktestableStrategy to use HistoricalDataProvider."""
        return fetch_vix_data(symbol=self.symbol)

    def _fetch_options_data(self) -> List[Dict[str, Any]]:
        """Override this in BacktestableStrategy to use HistoricalDataProvider."""
        options_data = fetch_latest_delta_data(symbol=self.symbol)
        if options_data:
            return options_data

        calculate_and_store_high_accuracy_delta(symbol=self.symbol)
        return fetch_latest_delta_data(symbol=self.symbol)

    def _get_df(self, interval: str, limit: Optional[int] = 300) -> pd.DataFrame:
        df = self._fetch_market_data(interval, limit)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        return df

    def _build_1h_from_15m_fallback(self, limit: Optional[int]) -> pd.DataFrame:
        """Generate 1h candles in memory when persisted hourly data is unavailable."""
        source_limit = None if limit is None else max((limit * 4) + 12, 400)

        try:
            df_15m = fetch_market_data(symbol=self.symbol, interval="15m", limit=source_limit)
        except Exception as exc:
            self.logger.warning(f"15m fallback data unavailable while building 1h frame: {exc}")
            return pd.DataFrame()

        if df_15m is None or df_15m.empty:
            self.logger.warning("15m fallback data is empty; cannot synthesize 1h candles.")
            return pd.DataFrame()

        df_1h = build_1h_market_data_from_15m(df_15m)
        if df_1h.empty:
            self.logger.warning("Could not synthesize any complete 1h candles from 15m data.")
            return pd.DataFrame()

        if limit is not None and len(df_1h) > limit:
            df_1h = df_1h.iloc[-limit:]

        return df_1h

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
    def main_category_a_active(self, df_1h: pd.DataFrame, df_1m: pd.DataFrame) -> bool:
        df_1m = self._compute_required_indicators(df_1m)
        df_1h = self._compute_required_indicators(df_1h)
        if not _ensure_cols(df_1m, ["close"]) or not _ensure_cols(df_1h, ["sma_20", "sma_50", "sma_200"]):
            return False
        last_1m = df_1m.iloc[-1]
        last_1h = df_1h.iloc[-1]
        try:
            close_1m = float(last_1m["close"])
            return (
                (close_1m < float(last_1h["sma_20"]))
                and (close_1m < float(last_1h["sma_50"]))
                and (close_1m < float(last_1h["sma_200"]))
            )
        except Exception:
            return False

    def _vix_regime(self) -> Optional[Literal["A", "B"]]:
        vix = self._fetch_vix_data()
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
        - Must be the closest available CE strike to spot within ±200 pts strictly.
        - If none exists within ±200, log a warning and choose the closest strike to the ±200 boundary.
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

        in_range = [s for s in ce if abs(s - spot) <= 200.0]
        if in_range:
            # closest to spot (tie-breaker: lower abs, then lower strike)
            chosen = min(in_range, key=lambda s: (abs(s - spot), s))
            return int(chosen)

        # fallback: closest to the interval [spot-200, spot+200]
        lower, upper = spot - 200.0, spot + 200.0
        def dist_to_range(s: float) -> float:
            if s < lower:
                return lower - s
            if s > upper:
                return s - upper
            return 0.0

        chosen = min(ce, key=lambda s: (dist_to_range(s), abs(s - spot), s))
        self.logger.warning(
            f"No CE strike found within ±200 of spot={spot:.2f}. "
            f"Fallback selecting CE strike {int(chosen)} (closest to the ±200 boundary)."
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
            legs.append({"action": "BUY", "tradingsymbol": ce_near["tradingsymbol"], "option_type": "CE", "strike_price": strikes.ce_strike_near, "expiry": ce_near.get("expiry") or ce_near.get("expiry_date"), "last_price": ce_near.get("last_price")})
            legs.append({"action": "BUY", "tradingsymbol": pe_upper["tradingsymbol"], "option_type": "PE", "strike_price": strikes.pe_strike_upper, "expiry": pe_upper.get("expiry") or pe_upper.get("expiry_date"), "last_price": pe_upper.get("last_price")})
            legs.append({"action": "SELL", "tradingsymbol": ce_upper["tradingsymbol"], "option_type": "CE", "strike_price": strikes.pe_strike_upper, "expiry": ce_upper.get("expiry") or ce_upper.get("expiry_date"), "last_price": ce_upper.get("last_price")})
            return legs

        # position_type == "B"
        # SELL PE strike, BUY PE strike + 100
        pe_sell = self._pick_option(options_data, "PE", strikes.pe_strike)
        pe_buy = self._pick_option(options_data, "PE", strikes.pe_strike + 100)
        if not pe_sell or not pe_buy:
            return None
        legs.append({"action": "SELL", "tradingsymbol": pe_sell["tradingsymbol"], "option_type": "PE", "strike_price": strikes.pe_strike, "expiry": pe_sell.get("expiry") or pe_sell.get("expiry_date"), "last_price": pe_sell.get("last_price")})
        legs.append({"action": "BUY", "tradingsymbol": pe_buy["tradingsymbol"], "option_type": "PE", "strike_price": strikes.pe_strike + 100, "expiry": pe_buy.get("expiry") or pe_buy.get("expiry_date"), "last_price": pe_buy.get("last_price")})
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

    def _detect_touches_and_latch(self, df_1m: pd.DataFrame, data_by_interval: Dict[str, pd.DataFrame]) -> List[str]:
        """
        For each subcategory a–o, if the latest closed 1m close is at or above that
        subcategory's latest indicator value, latch its flag.
        Returns the list of subcategories that *newly* latched on this call.
        """
        df_1m = self._compute_required_indicators(df_1m)
        if not _ensure_cols(df_1m, ["close"]):
            return []

        latest_1m = df_1m.iloc[-1]
        try:
            close_1m = float(latest_1m["close"])
        except Exception:
            return []
        touch_ts = pd.Timestamp(df_1m.index[-1])

        newly = []
        defs = self._section1_subcategory_defs()
        for subcat, (interval, indicator) in defs.items():
            df = data_by_interval.get(interval)
            if df is None or df.empty:
                continue
            df = self._compute_required_indicators(df)
            if not _ensure_cols(df, [indicator]):
                continue

            last = df.iloc[-1]
            ind_val = last.get(indicator)
            if pd.isna(ind_val):
                continue

            touched = close_1m >= float(ind_val)
            if touched and not bool(self.state["section1_flags"].get(subcat)):
                self.state["section1_flags"][subcat] = True
                self.state["section1_last_touch"][subcat] = _to_iso(touch_ts)
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
            #return True 
            return (float(last["close"]) < float(last["sma_20"])) and (float(last["close"]) < float(last["sma_5_low"]))
        except Exception:
            return False

    def _section2_prev_candle_above(self, df_1m: pd.DataFrame) -> bool:
        df_1m = self._compute_required_indicators(df_1m)
        if df_1m is None or len(df_1m) < 2:
            return False
        if not _ensure_cols(df_1m, ["close", "sma_20", "sma_5_low"]):
            return False
        previous = df_1m.iloc[-2]
        try:
            return (float(previous["close"]) > float(previous["sma_20"])) and (
                float(previous["close"]) > float(previous["sma_5_low"])
            )
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

    def _section2_rsi_ok(self, df_5m: pd.DataFrame, threshold: float = 40.0) -> Tuple[bool, Optional[float]]:
        """
        Section 2 uses the latest closed 5m candle's RSI(14, simple).
        """
        latest_rsi = _simple_rsi(df_5m, period=14, price_col="close")
        if latest_rsi is None:
            return False, None
        return latest_rsi > float(threshold), float(latest_rsi)

    # ---------------------------
    # Section 3: break & retest
    # ---------------------------
    def _update_section3_break_latch(self, df_1m: pd.DataFrame, df_5m: pd.DataFrame) -> bool:
        df_1m = self._compute_required_indicators(df_1m)
        df_5m = self._compute_required_indicators(df_5m)
        if not _ensure_cols(df_1m, ["close"]) or not _ensure_cols(df_5m, ["sma_20", "donchian_mid"]):
            return False
        last_1m = df_1m.iloc[-1]
        last_5m = df_5m.iloc[-1]
        close_1m = float(last_1m["close"])
        broke = False
        reason = None
        if close_1m < float(last_5m["sma_20"]):
            broke = True
            reason = "sma20"
        if close_1m < float(last_5m["donchian_mid"]):
            broke = True
            reason = "donchian_mid" if reason is None else reason
        if broke and not bool(self.state["section3"].get("break_latched")):
            self.state["section3"]["break_latched"] = True
            self.state["section3"]["break_reason"] = reason
            self.state["section3"]["break_time"] = _to_iso(pd.Timestamp(df_1m.index[-1]))
            self.save_state()
            return True
        return False

    def _section3_retest_ok(self, df_1m: pd.DataFrame, df_5m: pd.DataFrame) -> bool:
        SECTION3_LATCH_EXPIRY_MINUTES = 5
        if not bool(self.state["section3"].get("break_latched")):
            return False
        df_1m = self._compute_required_indicators(df_1m)
        df_5m = self._compute_required_indicators(df_5m)
        if not _ensure_cols(df_1m, ["close"]) or not _ensure_cols(df_5m, ["sma_20", "donchian_mid"]):
            return False
        break_time = _parse_ts(self.state["section3"].get("break_time"))
        current_ts = self._latest_ts(df_1m)
        if break_time is None or current_ts is None or current_ts <= break_time:
            return False
        
        # Check if break latch has expired (5-minute limit)
        now_ts = self._now()
        age_minutes = (now_ts - break_time).total_seconds() / 60.0
        if age_minutes > SECTION3_LATCH_EXPIRY_MINUTES:
            # Expire the latch
            self.state["section3"]["break_latched"] = False
            self.state["section3"]["break_reason"] = None
            self.state["section3"]["break_time"] = None
            self.save_state()
            self.logger.info(f"Section 3 break latch expired after {age_minutes:.1f} minutes")
            return False
        
        last_1m = df_1m.iloc[-1]
        last_5m = df_5m.iloc[-1]
        close_1m = float(last_1m["close"])
        return (close_1m >= float(last_5m["sma_20"])) or (close_1m >= float(last_5m["donchian_mid"]))

    # ---------------------------
    # Public: produce entry intent
    # ---------------------------
    def evaluate_for_entry(self) -> Optional[Dict[str, Any]]:
        """
        Returns an entry intent dict or None.
        This does NOT place orders or manage positions.
        """
        now = self._now()
        if self._entry_cutoff_reached(now):
            self.logger.info(
                "Skipping Nifty entry evaluation because the 3:00 pm cutoff has been reached."
            )
            return None

        # self.maybe_session_reset()

        # Load latest candles (keep moderate history for rolling calcs)
        df_1m = self._compute_required_indicators(self._get_df("1m", limit=300))
        df_5m = self._compute_required_indicators(self._get_df("5m", limit=300))
        df_15m = self._compute_required_indicators(self._get_df("15m", limit=400))
        df_1h = self._compute_required_indicators(self._get_df("1h", limit=400))

        if df_1m.empty or df_5m.empty or df_1h.empty:
            return None

        # Main Category A gate
        if not self.main_category_a_active(df_1h, df_1m):
            return None

        # Latch new subcategory touches (Section 1)
        self._detect_touches_and_latch(df_1m, {"1m": df_1m, "5m": df_5m, "15m": df_15m, "1h": df_1h})

        # Update Section 3 break latch on new 5m candles (it is evaluated on 5m candle closes)
        latest_5m_ts = self._latest_ts(df_5m)
        if latest_5m_ts is not None and self._is_new_candle("5m", df_5m):
            self._update_section3_break_latch(df_1m, df_5m)
            self.mark_processed("5m", latest_5m_ts)

        vix_regime = self._vix_regime()
        if vix_regime is None:
            return None

        position_type: Literal["A", "B"] = vix_regime
        sl_threshold = 25.0 if position_type == "A" else 35.0

        # Section 1 entry: requires any latched subcategory + 2nd flag + SL filter(7)
        # Latch expiry: subcategory latches expire after 5 minutes to ensure timely entries
        LATCH_EXPIRY_MINUTES = 5
        section1_ok = False
        now_ts = self._now()
        raw_active = [k for k, v in (self.state.get("section1_flags") or {}).items() if v]
        active_subcats = []
        for subcat in raw_active:
            latch_time = _parse_ts(self.state.get("section1_last_touch", {}).get(subcat))
            if latch_time is not None:
                age_minutes = (now_ts - latch_time).total_seconds() / 60.0
                if age_minutes <= LATCH_EXPIRY_MINUTES:
                    active_subcats.append(subcat)
                else:
                    # Expire the latch - clear the flag
                    self.state["section1_flags"][subcat] = False
                    self.logger.info(f"Subcategory {subcat} latch expired after {age_minutes:.1f} minutes")
            else:
                # No timestamp recorded, include it (backward compatibility)
                active_subcats.append(subcat)
        
        second_flag_ok = self._second_flag_ok(df_1m)
        if active_subcats:
            if second_flag_ok and self._sl_filter_ok(df_1m, lookback=5, threshold_pts=sl_threshold):
                section1_ok = True

        # Section 2 entry: direct 2nd-flag trigger + 5m RSI + SL filter(7)
        section2_ok = False
        section2_rsi = None
        if second_flag_ok and self._section2_prev_candle_above(df_1m):
            rsi_ok, section2_rsi = self._section2_rsi_ok(df_5m, threshold=40.0)
            if rsi_ok and self._sl_filter_ok(df_1m, lookback=5, threshold_pts=sl_threshold):
                section2_ok = True

        # Section 3 entry: break latched + retest + confirmation(2nd flag) + SL filter(7)
        section3_ok = False
        if self._section3_retest_ok(df_1m, df_5m):
            if second_flag_ok and self._sl_filter_ok(df_1m, lookback=5, threshold_pts=sl_threshold):
                section3_ok = True

        if not (section1_ok or section2_ok or section3_ok):
            return None

        # Entry section priority is explicit: Section 1 > Section 2 > Section 3
        chosen_section = None
        chosen_subcat = None
        if section1_ok:
            chosen_section = "section1"
            if active_subcats:
                chosen_subcat = sorted(active_subcats, key=self._priority_sort_key)[0]
        elif section2_ok:
            chosen_section = "section2"
        else:
            chosen_section = "section3"

        # Build legs from options cache
        options_data = self._fetch_options_data()
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

        now_ts = self._latest_ts(df_1m) or pd.Timestamp(now)
        reason = {
            "section": chosen_section,
            "subcat": chosen_subcat,
            "position_type": position_type,
        }
        if chosen_section == "section2" and section2_rsi is not None:
            reason["rsi_5m"] = round(float(section2_rsi), 6)

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
