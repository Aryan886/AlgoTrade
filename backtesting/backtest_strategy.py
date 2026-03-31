"""
BacktestableStrategy - Strategy wrapper for backtesting.

This class overrides data fetching methods to use HistoricalDataProvider
instead of live database queries.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional

import pandas as pd

import core.strat_nifty as strat_nifty_module
from core.strat_nifty import NiftyOptionsStrategy, _ensure_cols
from backtesting.data_provider import HistoricalDataProvider
from backtesting.option_snapshot import validate_nifty_strategy_snapshot
from backtesting.trade_logger import BacktestTradeLogger, NullTradeLogger, GateCheckResult


class BacktestableStrategy(NiftyOptionsStrategy):
    """
    Subclass of NiftyOptionsStrategy that uses HistoricalDataProvider for data.

    This ensures the strategy only sees data up to the current simulated time,
    preventing look-ahead bias in backtesting.
    """

    def __init__(
        self,
        data_provider: HistoricalDataProvider,
        current_time_fn: Callable[[], datetime],
        symbol: str = "NIFTY50",
        state_file: Optional[str] = None,
        debug_logger: Optional[BacktestTradeLogger] = None,
    ) -> None:
        """
        Args:
            data_provider: HistoricalDataProvider instance with loaded data
            current_time_fn: Callable that returns the current simulated time
            symbol: Trading symbol (default: NIFTY50)
            state_file: Path to state file (None for in-memory state)
            debug_logger: Optional BacktestTradeLogger for rich debugging
        """
        self._data_provider = data_provider
        self._current_time_fn = current_time_fn
        self._debug_logger = debug_logger or NullTradeLogger()

        # Initialize parent with a dummy state file
        # We'll override state management for backtesting
        safe_logger = logging.getLogger("backtest.strategy.init")
        original_logger_factory = strat_nifty_module.setup_paper_trading_logger
        strat_nifty_module.setup_paper_trading_logger = lambda: (
            safe_logger,
            safe_logger,
            safe_logger,
            safe_logger,
            safe_logger,
            safe_logger,
        )
        try:
            super().__init__(symbol=symbol, state_file=state_file or "")
        finally:
            strat_nifty_module.setup_paper_trading_logger = original_logger_factory

        # Override to use in-memory state for backtest isolation
        if state_file is None:
            self.state = self._default_state()

        # Use a null logger to reduce noise during backtesting
        self.logger = logging.getLogger("backtest.strategy")
        self.trade_logger = self.logger
        self.position_logger = self.logger

    def _fetch_market_data(self, interval: str, limit: Optional[int]) -> pd.DataFrame:
        """Override to use historical data provider with time-gating."""
        current_time = self._current_time_fn()
        return self._data_provider.fetch_market_data(
            current_time=current_time,
            interval=interval,
            limit=300 or 500,  # Fetch more data than needed to ensure we have enough after time-gating
        )

    def _fetch_vix_data(self) -> pd.DataFrame:
        """Override to use historical VIX data."""
        current_time = self._current_time_fn()
        return self._data_provider.fetch_vix_data(current_time)

    def _fetch_options_data(self) -> List[Dict[str, Any]]:
        """Override to use historical options data."""
        current_time = self._current_time_fn()
        return self._data_provider.fetch_delta_data(current_time)

    def _now(self) -> datetime:
        return self._current_time_fn()

    def save_state(self) -> None:
        """Override to prevent file I/O during backtesting."""
        # In-memory state only; no persistence
        pass

    def _load_state(self) -> Dict[str, Any]:
        """Override to always start with fresh state."""
        return self._default_state()

    def reset_state(self) -> None:
        """Reset strategy state to default values."""
        self.state = self._default_state()

    def _build_legs(self, position_type: Literal["A", "B"], strikes, options_data: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """Apply strict snapshot validation only in backtests before selecting exact legs."""
        validation_error = validate_nifty_strategy_snapshot(position_type, strikes, options_data)
        if validation_error:
            self.logger.warning(validation_error)
            return None
        return super()._build_legs(position_type, strikes, options_data)

    def evaluate_for_entry(self) -> Optional[Dict[str, Any]]:
        """
        Override to capture gate checks for debug logging.

        Returns an entry intent dict with _gate_checks field for logging.
        """
        now = self._now()
        gate_checks: List[GateCheckResult] = []

        # Check entry cutoff
        cutoff_reached = self._entry_cutoff_reached(now)
        gate_checks.append(GateCheckResult(
            gate_name="Entry Cutoff",
            passed=not cutoff_reached,
            details={"current_time": str(now.time()), "cutoff_time": str(self.ENTRY_CUTOFF_TIME)},
        ))
        if cutoff_reached:
            self._debug_logger.log_entry_evaluation_blocked(now, gate_checks, "Entry cutoff reached")
            return None

        # Load latest candles
        df_1m = self._compute_required_indicators(self._get_df("1m", limit=300))
        df_5m = self._compute_required_indicators(self._get_df("5m", limit=300))
        df_15m = self._compute_required_indicators(self._get_df("15m", limit=400))
        df_1h = self._compute_required_indicators(self._get_df("1h", limit=400))

        if df_1m.empty or df_5m.empty or df_1h.empty:
            gate_checks.append(GateCheckResult(
                gate_name="Data Availability",
                passed=False,
                details={"1m_empty": df_1m.empty, "5m_empty": df_5m.empty, "1h_empty": df_1h.empty},
            ))
            self._debug_logger.log_entry_evaluation_blocked(now, gate_checks, "Missing market data")
            return None

        # Main Category A gate
        cat_a_active = self.main_category_a_active(df_1h)
        cat_a_details = self._get_main_category_a_values(df_1h)
        gate_checks.append(GateCheckResult(
            gate_name="Main Category A",
            passed=cat_a_active,
            details=cat_a_details,
        ))
        if not cat_a_active:
            self._debug_logger.log_entry_evaluation_blocked(now, gate_checks, "Main Category A not active")
            return None

        # Latch new subcategory touches (Section 1)
        #self._detect_touches_and_latch({"1m": df_1m, "5m": df_5m, "15m": df_15m, "1h": df_1h})
        self._detect_touches_and_latch({"5m": df_5m, "15m": df_15m, "1h": df_1h})

        # Update Section 3 break latch
        latest_5m_ts = self._latest_ts(df_5m)
        if latest_5m_ts is not None and self._is_new_candle("5m", df_5m):
            self._update_section3_break_latch(df_5m)
            self.mark_processed("5m", latest_5m_ts)

        # VIX Regime
        vix_regime = self._vix_regime()
        vix_details = self._get_vix_regime_values()
        if vix_regime is None:
            gate_checks.append(GateCheckResult(
                gate_name="VIX Regime",
                passed=False,
                details=vix_details,
            ))
            self._debug_logger.log_entry_evaluation_blocked(now, gate_checks, "VIX regime unavailable")
            return None

        position_type: Literal["A", "B"] = vix_regime
        sl_threshold = 25.0 if position_type == "A" else 35.0
        vix_details["position_type"] = position_type
        vix_details["sl_threshold"] = sl_threshold
        gate_checks.append(GateCheckResult(
            gate_name="VIX Regime",
            passed=True,
            details=vix_details,
        ))

        # Section 1 entry check
        section1_ok = False
        active_subcats = [k for k, v in (self.state.get("section1_flags") or {}).items() if v]
        second_flag_ok = self._second_flag_ok(df_1m)

        if active_subcats:
            # Log 1st Flag with detailed touch information
            section1_details = self._get_section1_flag_details({"1m": df_1m, "5m": df_5m, "15m": df_15m, "1h": df_1h})
            gate_checks.append(GateCheckResult(
                gate_name="1st Flag (Section 1)",
                passed=True,
                details=section1_details,
            ))

            if second_flag_ok:
                second_flag_details = self._get_second_flag_values(df_1m)
                gate_checks.append(GateCheckResult(
                    gate_name="2nd Flag",
                    passed=True,
                    details=second_flag_details,
                ))
                sl_filter_ok = self._sl_filter_ok(df_1m, lookback=5, threshold_pts=sl_threshold)
                sl_filter_details = self._get_sl_filter_values(df_1m, lookback=5, threshold=sl_threshold)

                gate_checks.append(GateCheckResult(
                    gate_name="SL Filter",
                    passed=sl_filter_ok,
                    details=sl_filter_details,
                ))

                if sl_filter_ok:
                    section1_ok = True

        # Section 2 entry check
        section2_ok = False
        section2_rsi = None
        if second_flag_ok:
            section2_prev_candle_ok = self._section2_prev_candle_above(df_1m)
            gate_checks.append(GateCheckResult(
                gate_name="Section 2 Previous Candle",
                passed=section2_prev_candle_ok,
                details=self._get_section2_prev_candle_values(df_1m),
            ))
            if section2_prev_candle_ok:
                rsi_ok, section2_rsi = self._section2_rsi_ok(df_5m, threshold=40.0)
                gate_checks.append(GateCheckResult(
                    gate_name="RSI Filter",
                    passed=rsi_ok,
                    details={"rsi_value": section2_rsi, "threshold": 40.0},
                ))
                if rsi_ok:
                    sl_filter_ok = self._sl_filter_ok(df_1m, lookback=5, threshold_pts=sl_threshold)
                    sl_filter_details = self._get_sl_filter_values(df_1m, lookback=5, threshold=sl_threshold)
                    gate_checks.append(GateCheckResult(
                        gate_name="SL Filter (Section 2)",
                        passed=sl_filter_ok,
                        details=sl_filter_details,
                    ))
                    if sl_filter_ok:
                        section2_ok = True

        # Section 3 entry check
        section3_ok = False
        break_latched = bool(self.state["section3"].get("break_latched"))
        gate_checks.append(GateCheckResult(
            gate_name="Section 3 Break Latch",
            passed=break_latched,
            details={
                "break_latched": break_latched,
                "break_reason": self.state["section3"].get("break_reason"),
            },
        ))

        if self._section3_retest_ok(df_5m):
            gate_checks.append(GateCheckResult(
                gate_name="Section 3 Retest",
                passed=True,
                details=self._get_section3_retest_values(df_5m),
            ))
            if second_flag_ok:
                sl_filter_ok = self._sl_filter_ok(df_1m, lookback=5, threshold_pts=sl_threshold)
                if sl_filter_ok:
                    section3_ok = True

        if not (section1_ok or section2_ok or section3_ok):
            self._debug_logger.log_entry_evaluation_blocked(now, gate_checks, "No entry section conditions met")
            return None

        # Entry section priority
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
            gate_checks.append(GateCheckResult(
                gate_name="Options Data",
                passed=False,
                details={"error": "Options cache missing"},
            ))
            self._debug_logger.log_entry_evaluation_blocked(now, gate_checks, "Options cache missing")
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
            "timestamp": self._to_iso(now_ts),
            "reason": reason,
            "spot": strikes.spot,
            "position_type": position_type,
            "strikes": {
                "pe_strike": strikes.pe_strike,
                "ce_strike_near": strikes.ce_strike_near,
                "pe_strike_upper": strikes.pe_strike_upper,
            },
            "legs": legs,
            "_gate_checks": gate_checks,  # For debug logging
        }

    def _to_iso(self, ts) -> str:
        """Convert timestamp to ISO string."""
        if isinstance(ts, pd.Timestamp):
            ts = ts.to_pydatetime()
        if hasattr(ts, "strftime"):
            return ts.strftime("%Y-%m-%d %H:%M:%S")
        return str(ts)

    def _get_main_category_a_values(self, df_1h: pd.DataFrame) -> Dict[str, Any]:
        """Extract values used in Main Category A check for logging."""
        df_1h = self._compute_required_indicators(df_1h)
        if df_1h is None or df_1h.empty:
            return {"error": "No 1h data"}
        if not _ensure_cols(df_1h, ["close", "sma_20", "sma_50", "sma_200"]):
            return {"error": "Missing required columns"}
        last = df_1h.iloc[-1]
        return {
            "1h_close": float(last.get("close", 0)),
            "sma_20": float(last.get("sma_20", 0)),
            "sma_50": float(last.get("sma_50", 0)),
            "sma_200": float(last.get("sma_200", 0)),
        }

    def _get_section1_flag_details(self, data_by_interval: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
        """Extract detailed information about Section 1 flags for logging."""
        active_subcats = [k for k, v in (self.state.get("section1_flags") or {}).items() if v]
        if not active_subcats:
            return {"active_subcategories": [], "touches": []}
        
        defs = self._section1_subcategory_defs()
        touches = []
        
        for subcat in sorted(active_subcats, key=self._priority_sort_key):
            interval, indicator = defs[subcat]
            df = data_by_interval.get(interval)
            
            touch_info = {
                "subcat": subcat,
                "interval": interval,
                "indicator": indicator,
                "high": None,
                "indicator_value": None,
                "touch_time": self.state.get("section1_last_touch", {}).get(subcat),
            }
            
            if df is not None and not df.empty:
                df = self._compute_required_indicators(df)
                if _ensure_cols(df, ["high", indicator]):
                    last = df.iloc[-1]
                    touch_info["high"] = float(last["high"])
                    ind_val = last.get(indicator)
                    if pd.notna(ind_val):
                        touch_info["indicator_value"] = float(ind_val)
            
            touches.append(touch_info)
        
        return {
            "active_subcategories": active_subcats,
            "touches": touches,
        }

    def _get_vix_regime_values(self) -> Dict[str, Any]:
        """Extract values used in VIX regime check for logging."""
        vix = self._fetch_vix_data()
        if vix is None or vix.empty or "vix_value" not in vix.columns:
            return {"error": "VIX data missing"}
        vix = vix.copy()
        vix["vix_sma20"] = vix["vix_value"].rolling(window=20, min_periods=20).mean()
        last = vix.iloc[-1]
        return {
            "vix_value": float(last.get("vix_value", 0)),
            "vix_sma20": float(last.get("vix_sma20", 0)) if pd.notna(last.get("vix_sma20")) else None,
        }

    def _get_second_flag_values(self, df_1m: pd.DataFrame) -> Dict[str, Any]:
        """Extract values used in 2nd flag check for logging."""
        if df_1m is None or df_1m.empty:
            return {"error": "No 1m data"}
        if not _ensure_cols(df_1m, ["close", "sma_20", "sma_5_low"]):
            return {"error": "Missing required columns"}
        last = df_1m.iloc[-1]
        return {
            "1m_close": float(last.get("close", 0)),
            "sma_20": float(last.get("sma_20", 0)),
            "sma_5_low": float(last.get("sma_5_low", 0)),
        }

    def _get_section2_prev_candle_values(self, df_1m: pd.DataFrame) -> Dict[str, Any]:
        """Extract values used in the Section 2 previous-candle gate for logging."""
        if df_1m is None or df_1m.empty:
            return {"error": "No 1m data"}
        if len(df_1m) < 2:
            return {"error": "Insufficient data (need 2 candles)"}
        if not _ensure_cols(df_1m, ["close", "sma_20", "sma_5_low"]):
            return {"error": "Missing required columns"}
        previous = df_1m.iloc[-2]
        return {
            "previous_1m_close": float(previous.get("close", 0)),
            "previous_sma_20": float(previous.get("sma_20", 0)),
            "previous_sma_5_low": float(previous.get("sma_5_low", 0)),
        }

    def _get_sl_filter_values(self, df_1m: pd.DataFrame, lookback: int, threshold: float) -> Dict[str, Any]:
        """Extract values used in SL filter check for logging."""
        if df_1m is None or df_1m.empty or "high" not in df_1m.columns or "close" not in df_1m.columns:
            return {"error": "Missing data"}
        if len(df_1m) < lookback:
            return {"error": f"Insufficient data (need {lookback} candles)"}
        window = df_1m.iloc[-lookback:]
        hh = float(window["high"].max())
        close = float(df_1m.iloc[-1]["close"])
        sl_pts = hh - close
        return {
            "highest_high": hh,
            "close": close,
            "sl_pts": sl_pts,
            "threshold": threshold,
            "lookback": lookback,
        }

    def _get_section3_retest_values(self, df_5m: pd.DataFrame) -> Dict[str, Any]:
        """Extract values used in Section 3 retest check for logging."""
        df_5m = self._compute_required_indicators(df_5m)
        if df_5m is None or df_5m.empty:
            return {"error": "No 5m data"}
        last = df_5m.iloc[-1]
        return {
            "high": float(last.get("high", 0)),
            "sma_20": float(last.get("sma_20", 0)),
            "donchian_mid": float(last.get("donchian_mid", 0)) if "donchian_mid" in last else None,
        }
