from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Literal, Optional

import pandas as pd

from core.strat_nifty import NiftyOptionsStrategy
from utils.db_func import fetch_latest_delta_data, fetch_latest_delta_snapshot, fetch_latest_option_price, fetch_market_data
from utils.utility import setup_paper_trading_logger


Side = Literal["BUY", "SELL"]


def _to_iso(ts: Any) -> str:
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


def _df(interval: str, symbol: str, limit: int = 400) -> pd.DataFrame:
    df = fetch_market_data(symbol=symbol, interval=interval, limit=limit)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    return df


def _latest_ts(df: pd.DataFrame) -> Optional[pd.Timestamp]:
    if df is None or df.empty:
        return None
    try:
        return pd.Timestamp(df.index[-1])
    except Exception:
        return None


def _highest_high_last_n(df: pd.DataFrame, n: int) -> Optional[float]:
    if df is None or df.empty or "high" not in df.columns:
        return None
    if len(df) < n:
        return None
    try:
        return float(df.iloc[-n:]["high"].max())
    except Exception:
        return None


def _get_latest_sh(df: pd.DataFrame) -> Optional[float]:
    # fetch_market_data() standardizes SH -> 'sh'
    if df is None or df.empty:
        return None
    if "sh" not in df.columns:
        return None
    try:
        v = df.iloc[-1]["sh"]
        return float(v) if pd.notna(v) else None
    except Exception:
        return None


def _mark_to_market_price_map(options_data: List[Dict[str, Any]]) -> Dict[str, float]:
    m: Dict[str, float] = {}
    for o in options_data or []:
        sym = o.get("tradingsymbol")
        if not sym:
            continue
        try:
            m[str(sym)] = float(o.get("ltp") or 0.0)
        except Exception:
            m[str(sym)] = 0.0
    return m


def _leg_pnl(side: Side, entry: float, current: float) -> float:
    # BUY profit = current - entry; SELL profit = entry - current
    if side == "BUY":
        return current - entry
    return entry - current


@dataclass
class LotSpec:
    lot_id: str
    timeframe: Literal["1m", "5m"]
    sl_lookback: int


class NiftyPaperBot:
    """
    Multi-leg, 2-lot paper bot for the Nifty strategy.

    Implements spec clarifications:
    - Two lots opened simultaneously (Lot1=1m SL, Lot2=5m SL), same legs/strikes.
    - Strict re-entry gating: only open new position when BOTH lots are flat.
    - SL trailing is bearish ratchet using SH: sl_current = min(sl_current, new_sh) (never increases).
    - Exit is candle-close based: if close > sl_current on lot's timeframe, square off that lot only.
    - Strategy flags are reset at 09:15 via NiftyOptionsStrategy session reset.
    """

    SESSION_RESET_TIME = dtime(9, 15)
    ENTRY_CUTOFF_TIME = dtime(15, 00)

    def __init__(
        self,
        symbol: str = "NIFTY50",
        position_file: str = "active_position_nifty.json",
        state_file: str = "nifty_strategy_state.json",
        dry_run: bool = False,
        strategy: Optional[NiftyOptionsStrategy] = None,
    ) -> None:
        self.symbol = symbol or "NIFTY50"
        self.position_file = position_file
        self.dry_run = dry_run

        _paper_logger, _trade_logger, _position_logger, _sma_logger, _equity_logger, nifty_logger = setup_paper_trading_logger()
        self.logger = nifty_logger
        self.trade_logger = nifty_logger
        self.position_logger = nifty_logger

        self.strategy = strategy or NiftyOptionsStrategy(symbol=self.symbol, state_file=state_file)
        self.position: Dict[str, Any] = self._load_position()
        self._last_options_snapshot_timestamp: Optional[str] = None

        self.lots = [
            LotSpec(lot_id="lot1", timeframe="1m", sl_lookback=7),
            LotSpec(lot_id="lot2", timeframe="5m", sl_lookback=7),
        ]

    def _now(self) -> datetime:
        return datetime.now()

    def _entry_cutoff_reached(self, now: Optional[datetime] = None) -> bool:
        now = now or self._now()
        return now.time() >= self.ENTRY_CUTOFF_TIME

    # ---------------------------
    # Data helpers (overridable for backtesting)
    # ---------------------------
    def _get_df(self, interval: str, limit: int = 400) -> pd.DataFrame:
        """Override this in BacktestableBot to use HistoricalDataProvider."""
        return _df(interval, self.symbol, limit)

    def _fetch_options_data(self) -> List[Dict[str, Any]]:
        """Override this in BacktestableBot to use HistoricalDataProvider."""
        options_data = fetch_latest_delta_data(symbol=self.symbol)
        if options_data:
            snapshot = fetch_latest_delta_snapshot(symbol=self.symbol)
            self._last_options_snapshot_timestamp = snapshot.get("timestamp")
            return options_data

        snapshot = fetch_latest_delta_snapshot(symbol=self.symbol)
        self._last_options_snapshot_timestamp = snapshot.get("timestamp")
        options_data = snapshot.get("options_data") or []
        if options_data:
            return options_data
        return []

    # ---------------------------
    # Persistence
    # ---------------------------
    def _load_position(self) -> Dict[str, Any]:
        if not os.path.exists(self.position_file):
            return {"symbol": self.symbol, "lots": {}, "meta": {}}
        try:
            with open(self.position_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"symbol": self.symbol, "lots": {}, "meta": {}}
            data.setdefault("symbol", self.symbol)
            data.setdefault("lots", {})
            data.setdefault("meta", {})
            return data
        except Exception as e:
            self.logger.warning(f"Failed to load {self.position_file}: {e}. Resetting.")
            return {"symbol": self.symbol, "lots": {}, "meta": {}}

    def save_position(self) -> None:
        if self.dry_run:
            return
        try:
            with open(self.position_file, "w", encoding="utf-8") as f:
                json.dump(self.position, f, indent=2, default=str)
        except Exception as e:
            self.logger.error(f"Failed to persist position: {e}")

    # ---------------------------
    # Lot state helpers
    # ---------------------------
    def _lot_is_open(self, lot_id: str) -> bool:
        lot = (self.position.get("lots") or {}).get(lot_id) or {}
        return bool(lot.get("status") == "OPEN")

    def all_lots_flat(self) -> bool:
        lots = self.position.get("lots") or {}
        # flat = no OPEN lots
        for lot_id in ("lot1", "lot2"):
            if lots.get(lot_id, {}).get("status") == "OPEN":
                return False
        return True

    # ---------------------------
    # Entry / exit / trailing
    # ---------------------------
    def _open_two_lots(self, intent: Dict[str, Any]) -> None:
        """
        Creates two independent lots with identical legs but separate SL timeframe.
        """
        if self._entry_cutoff_reached():
            self.logger.info("Skipping new Nifty entry because the 3:00 pm cutoff has been reached.")
            return

        if not self.all_lots_flat():
            self.logger.info("Skipping new Nifty entry because an existing position is still open.")
            return

        now = self._now()
        lots_obj: Dict[str, Any] = self.position.setdefault("lots", {})

        # Snapshot underlying data for SL initialization
        df_1m = self._get_df("1m", limit=200)
        df_5m = self._get_df("5m", limit=200)
        if df_1m.empty or df_5m.empty:
            self.logger.warning("Cannot open lots: missing 1m/5m market data.")
            return

        # Entry close price comes from latest 1m close (spec uses entry close)
        try:
            entry_close = float(df_1m.iloc[-1]["close"])
        except Exception:
            self.logger.warning("Cannot open lots: missing 1m close for entry price reference.")
            return

        # Initialize SL levels as highest-high of last 7 candles on each timeframe (absolute price level)
        lot1_sl = _highest_high_last_n(df_1m, 7)
        lot2_sl = _highest_high_last_n(df_5m, 7)
        if lot1_sl is None or lot2_sl is None:
            self.logger.warning("Cannot open lots: insufficient candles for initial SL (need 7).")
            return

        legs = intent.get("legs") or []
        if not legs:
            return

        # Build entry price map from option cache for mark-to-market
        options_data = self._fetch_options_data()
        price_map = _mark_to_market_price_map(options_data or [])
        distinct_expiries = sorted({str(o.get("expiry") or o.get("expiry_date") or "")[:10] for o in options_data if o.get("expiry") or o.get("expiry_date")})
        self.logger.info(
            "STANDARD_NIFTY bot entry snapshot ts=%s rows=%s expiries=%s",
            self._last_options_snapshot_timestamp,
            len(options_data or []),
            distinct_expiries,
        )

        def normalize_leg(l: Dict[str, Any]) -> Dict[str, Any]:
            sym = l.get("tradingsymbol")
            side = (l.get("action") or "").upper()
            option_type = (l.get("option_type") or "").upper() or None
            strike_price = l.get("strike_price")
            expiry = l.get("expiry")
            entry_price = None
            try:
                if sym in price_map:
                    entry_price = float(price_map[sym])
                else:
                    entry_price = float(l.get("last_price") or 0.0)
            except Exception:
                entry_price = 0.0
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

        for spec in self.lots:
            sl_init = lot1_sl if spec.lot_id == "lot1" else lot2_sl
            lot_legs = [dict(leg) for leg in norm_legs]
            lots_obj[spec.lot_id] = {
                "lot_id": spec.lot_id,
                "timeframe": spec.timeframe,
                "status": "OPEN",
                "opened_at": _to_iso(now),
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
                },
            }

        self.position["meta"] = {"last_entry_intent_ts": intent.get("timestamp")}

        msg = f"OPENED NIFTY POSITION: 2 lots, {len(norm_legs)} legs, type={intent.get('position_type')} reason={intent.get('reason')}"
        self.logger.info(msg)
        self.trade_logger.info(msg)
        for leg in norm_legs:
            self.trade_logger.info(f"  {leg['side']} {leg['tradingsymbol']} @ {leg['entry_price']}")
            self.logger.info(
                "STANDARD_NIFTY persisted leg: side=%s symbol=%s strike=%s type=%s expiry=%s entry_price=%s snapshot_ts=%s",
                leg.get("side"),
                leg.get("tradingsymbol"),
                leg.get("strike_price"),
                leg.get("option_type"),
                leg.get("expiry"),
                leg.get("entry_price"),
                self._last_options_snapshot_timestamp,
            )

        self.save_position()

    def _trail_sl_for_lot(self, lot_id: str, timeframe: Literal["1m", "5m"], df_tf: pd.DataFrame) -> None:
        lot = (self.position.get("lots") or {}).get(lot_id)
        if not lot or lot.get("status") != "OPEN":
            return

        ts = _latest_ts(df_tf)
        if ts is None:
            return

        last_trail = _parse_ts(lot.get("last_trail_ts"))
        if last_trail is not None and ts <= last_trail:
            return

        new_sh = _get_latest_sh(df_tf)
        if new_sh is None:
            return

        cur_sl = float(lot.get("sl_current_level") or 0.0)
        # Bearish ratchet only: SL never increases
        new_sl = min(cur_sl, float(new_sh)) if cur_sl else float(new_sh)

        if new_sl < cur_sl:
            lot["sl_current_level"] = float(new_sl)
            self.position_logger.info(f"[{lot_id}] SL ratchet down: {cur_sl:.2f} -> {new_sl:.2f} (new_sh={new_sh:.2f})")

        lot["last_trail_ts"] = _to_iso(ts)
        self.save_position()

    def _exit_check_for_lot(self, lot_id: str, timeframe: Literal["1m", "5m"], df_tf: pd.DataFrame) -> None:
        lot = (self.position.get("lots") or {}).get(lot_id)
        if not lot or lot.get("status") != "OPEN":
            return

        ts = _latest_ts(df_tf)
        if ts is None:
            return
        last_check = _parse_ts(lot.get("last_exit_check_ts"))
        if last_check is not None and ts <= last_check:
            return

        lot["last_exit_check_ts"] = _to_iso(ts)

        try:
            close = float(df_tf.iloc[-1]["close"])
        except Exception:
            self.save_position()
            return

        opened_at = _parse_ts(lot.get("opened_at"))
        
        if opened_at is not None and ts < (opened_at + pd.Timedelta(minutes=5)):
            self.save_position()
            return

        sl = float(lot.get("sl_current_level") or 0.0)
        if sl and close > sl:
            self._close_lot(lot_id, exit_time=_to_iso(ts))
        self.save_position()

    def _lot_has_new_candle(self, lot_id: str, df_tf: pd.DataFrame) -> bool:
        lot = (self.position.get("lots") or {}).get(lot_id)
        if not lot or lot.get("status") != "OPEN":
            return False

        ts = _latest_ts(df_tf)
        if ts is None:
            return False

        processed = [
            parsed
            for parsed in (
                _parse_ts(lot.get("last_trail_ts")),
                _parse_ts(lot.get("last_exit_check_ts")),
            )
            if parsed is not None
        ]
        if not processed:
            return True
        return ts > max(processed)

    def _close_lot(self, lot_id: str, exit_time: str) -> None:
        lot = (self.position.get("lots") or {}).get(lot_id)
        if not lot or lot.get("status") != "OPEN":
            return

        options_data = self._fetch_options_data()
        price_map = _mark_to_market_price_map(options_data or [])

        pnl = 0.0
        for leg in lot.get("legs") or []:
            sym = leg.get("tradingsymbol")
            side = (leg.get("side") or "BUY").upper()
            entry = float(leg.get("entry_price") or 0.0)
            current = self._resolve_leg_current_price(leg, price_map)
            leg["exit_price"] = current
            leg["exit_time"] = exit_time
            pnl += _leg_pnl(side, entry, current)

        lot["status"] = "CLOSED"
        lot["closed_at"] = exit_time
        lot["pnl"] = pnl

        msg = f"CLOSED {lot_id} ({lot.get('timeframe')}) pnl={pnl:.2f}"
        self.logger.info(msg)
        self.trade_logger.info(msg)

        self.save_position()

    def _force_square_off_all_open_lots(self, exit_time: str) -> None:
        lots = self.position.get("lots") or {}
        open_lot_ids = [lot_id for lot_id in ("lot1", "lot2") if lots.get(lot_id, {}).get("status") == "OPEN"]
        if not open_lot_ids:
            return

        self.logger.info(
            f"3:00 pm cutoff reached. Force-closing all open Nifty lots: {', '.join(open_lot_ids)}."
        )
        for lot_id in open_lot_ids:
            self._close_lot(lot_id, exit_time=exit_time)

    # ---------------------------
    # Main cycle
    # ---------------------------
    def run_once(self) -> None:
        """
        One scheduling cycle. Safe to call every minute from your automation.
        """
        now = self._now()

        # Strategy session reset (flags/timestamps) at 09:15
        self.strategy.maybe_session_reset(now=now)

        if self._entry_cutoff_reached(now):
            self._force_square_off_all_open_lots(exit_time=_to_iso(now))
            return

        df_1m = self._get_df("1m", limit=300)
        df_5m = self._get_df("5m", limit=300)
        if df_1m.empty:
            return

        # Trailing + exit checks for open lots
        lots = self.position.get("lots") or {}

        if lots.get("lot1", {}).get("status") == "OPEN":
            if not df_1m.empty:
                self._trail_sl_for_lot("lot1", "1m", df_1m)
                self._exit_check_for_lot("lot1", "1m", df_1m)

        # 5m lot only on new 5m candle
        if lots.get("lot2", {}).get("status") == "OPEN":
            if not df_5m.empty and self._lot_has_new_candle("lot2", df_5m):
                self._trail_sl_for_lot("lot2", "5m", df_5m)
                self._exit_check_for_lot("lot2", "5m", df_5m)

        # Strict re-entry gating: only evaluate entry if BOTH lots are flat
        if not self.all_lots_flat():
            return

        intent = self.strategy.evaluate_for_entry()
        if not intent:
            return

        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would open 2 lots with legs={len(intent.get('legs') or [])} type={intent.get('position_type')}")
            return

        self._open_two_lots(intent)

        # Clear all latched flags so a fresh touch is required before the next entry.
        self.strategy.state["section1_flags"] = {k: False for k in list("abcdefghijklmno")}
        self.strategy.state["section1_last_touch"] = {k: None for k in list("abcdefghijklmno")}
        self.strategy.state["section3"] = {
            "break_latched": False,
            "break_reason": None,
            "break_time": None,
        }
        self.strategy.save_state()

    def _lookup_held_leg_price(self, leg: Dict[str, Any]) -> Optional[float]:
        return fetch_latest_option_price(
            symbol=self.symbol,
            tradingsymbol=leg.get("tradingsymbol"),
            strike_price=leg.get("strike_price"),
            option_type=leg.get("option_type"),
            expiry=leg.get("expiry"),
        )

    def _resolve_leg_current_price(self, leg: Dict[str, Any], price_map: Dict[str, float]) -> float:
        sym = leg.get("tradingsymbol")
        if sym in price_map:
            current = float(price_map[sym])
            leg["last_price"] = current
            self.position_logger.info(
                "STANDARD_NIFTY mark-to-market from snapshot: symbol=%s expiry=%s price=%.2f snapshot_ts=%s",
                sym,
                leg.get("expiry"),
                current,
                self._last_options_snapshot_timestamp,
            )
            return current

        fallback_price = self._lookup_held_leg_price(leg)
        if fallback_price is not None:
            leg["last_price"] = float(fallback_price)
            self.position_logger.info(
                "STANDARD_NIFTY mark-to-market from fallback: symbol=%s expiry=%s price=%.2f snapshot_ts=%s",
                sym,
                leg.get("expiry"),
                float(fallback_price),
                self._last_options_snapshot_timestamp,
            )
            self.logger.warning(
                f"Quote for held leg {sym} missing from latest snapshot; using last known contract price {fallback_price:.2f}."
            )
            return float(fallback_price)

        last_known = leg.get("last_price")
        if last_known is not None:
            self.logger.warning(
                f"Quote for held leg {sym} unavailable; reusing prior mark price {float(last_known):.2f}."
            )
            return float(last_known)

        entry = float(leg.get("entry_price") or 0.0)
        self.logger.warning(
            f"Quote for held leg {sym} unavailable and no prior mark exists; falling back to entry price {entry:.2f}."
        )
        return entry

    def run_forever(self, sleep_seconds: int = 10) -> None:
        self.logger.info("Starting Nifty paper bot loop...")
        while True:
            try:
                self.run_once()
            except Exception as e:
                self.logger.error(f"Nifty bot cycle error: {e}")
            time.sleep(sleep_seconds)


if __name__ == "__main__":
    bot = NiftyPaperBot(symbol="NIFTY50", dry_run=False)
    bot.run_forever()
