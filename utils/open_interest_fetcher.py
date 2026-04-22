"""
Fetch and persist NIFTY option open-interest snapshots from Kite.

This module is intentionally separate from the existing option_data/delta
pipeline so OI collection cannot disturb current market-data consumers.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from broker.zerodha_client import kite_from_saved_token
from config.config import CONFIG
from utils.db_func import DB_PATH, rebuild_open_interest_5m_for_rows, store_open_interest_snapshot


logger = logging.getLogger(__name__)

NIFTY_SPOT_QUOTE_KEY = "NSE:NIFTY 50"
NFO_EXCHANGE = "NFO"


def round_to_interval(value: float, interval: int) -> int:
    """Round to the nearest interval, with half-interval rounded up."""
    interval = int(interval)
    return int(((float(value) + (interval / 2.0)) // interval) * interval)


def build_oi_strike_window(
    spot_price: float,
    band: int = CONFIG.get("OI_STRIKE_BAND", 300),
    interval: int = CONFIG.get("OI_STRIKE_INTERVAL", 50),
) -> List[int]:
    """Return the dynamic ATM +/- band strikes for an OI snapshot."""
    atm = round_to_interval(float(spot_price), int(interval))
    return list(range(atm - int(band), atm + int(band) + int(interval), int(interval)))


def _parse_expiry(expiry_value: Any) -> Optional[date]:
    if expiry_value is None:
        return None
    if isinstance(expiry_value, datetime):
        return expiry_value.date()
    if isinstance(expiry_value, date):
        return expiry_value
    try:
        return datetime.strptime(str(expiry_value), "%Y-%m-%d").date()
    except Exception:
        return None


def _expiry_to_string(expiry_value: Any) -> str:
    expiry = _parse_expiry(expiry_value)
    return expiry.strftime("%Y-%m-%d") if expiry else str(expiry_value)


def select_nearest_expiry_instruments(
    instruments: Iterable[Dict[str, Any]],
    current_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Filter to NIFTY CE/PE instruments for the nearest non-expired expiry."""
    current_date = current_date or datetime.now().date()
    valid: List[tuple[date, Dict[str, Any]]] = []

    for inst in instruments:
        if inst.get("name") != "NIFTY":
            continue
        if str(inst.get("instrument_type") or "").upper() not in {"CE", "PE"}:
            continue
        expiry = _parse_expiry(inst.get("expiry"))
        if expiry is None or expiry < current_date:
            continue
        valid.append((expiry, inst))

    if not valid:
        return []

    nearest_expiry = min(expiry for expiry, _ in valid)
    return [inst for expiry, inst in valid if expiry == nearest_expiry]


def filter_instruments_for_strikes(
    instruments: Iterable[Dict[str, Any]],
    strikes: Iterable[int],
) -> List[Dict[str, Any]]:
    strike_set = {int(strike) for strike in strikes}
    selected = []
    for inst in instruments:
        try:
            strike = int(float(inst.get("strike")))
        except Exception:
            continue
        if strike in strike_set:
            selected.append(inst)

    return sorted(
        selected,
        key=lambda inst: (
            int(float(inst.get("strike") or 0)),
            str(inst.get("instrument_type") or ""),
            str(inst.get("tradingsymbol") or ""),
        ),
    )


def _first_present(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


class NiftyOpenInterestFetcher:
    """Kite-backed NIFTY OI snapshot fetcher."""

    def __init__(self, kite=None, symbol: str = "NIFTY50") -> None:
        self.kite = kite
        self.symbol = symbol or "NIFTY50"

    def connect(self) -> bool:
        if self.kite is not None:
            return True
        try:
            self.kite = kite_from_saved_token()
            return self.kite is not None
        except Exception as exc:
            logger.error("Failed to connect to Kite for OI snapshot: %s", exc)
            return False

    def fetch_spot_price(self) -> Optional[float]:
        if self.kite is None:
            return None
        quote = self.kite.quote(NIFTY_SPOT_QUOTE_KEY)
        spot_quote = quote.get(NIFTY_SPOT_QUOTE_KEY) if isinstance(quote, dict) else None
        if not spot_quote:
            return None
        try:
            return float(spot_quote.get("last_price"))
        except Exception:
            return None

    def fetch_snapshot(
        self,
        band: int = CONFIG.get("OI_STRIKE_BAND", 300),
        interval: int = CONFIG.get("OI_STRIKE_INTERVAL", 50),
        current_date: Optional[date] = None,
        timestamp: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch one dynamic nearest-expiry OI snapshot from Kite."""
        if not self.connect():
            return []

        spot_price = self.fetch_spot_price()
        if spot_price is None:
            logger.warning("Could not fetch NIFTY spot price for OI snapshot")
            return []

        instruments = self.kite.instruments(NFO_EXCHANGE)
        nearest = select_nearest_expiry_instruments(instruments, current_date=current_date)
        if not nearest:
            logger.warning("No active nearest-expiry NIFTY option instruments found")
            return []

        strikes = build_oi_strike_window(spot_price, band=band, interval=interval)
        selected = filter_instruments_for_strikes(nearest, strikes)
        if not selected:
            logger.warning("No NIFTY option instruments found for OI strikes: %s", strikes)
            return []

        quote_keys = [f"{NFO_EXCHANGE}:{inst['tradingsymbol']}" for inst in selected if inst.get("tradingsymbol")]
        if not quote_keys:
            return []

        quotes = self.kite.quote(quote_keys)
        if not isinstance(quotes, dict):
            logger.warning("Unexpected OI quote response type: %s", type(quotes).__name__)
            return []

        snapshot_time = timestamp or datetime.now()
        rows = []
        missing = []

        for inst in selected:
            tradingsymbol = inst.get("tradingsymbol")
            quote_key = f"{NFO_EXCHANGE}:{tradingsymbol}"
            quote = quotes.get(quote_key)
            if not quote:
                missing.append(quote_key)
                continue

            rows.append({
                "timestamp": snapshot_time,
                "symbol": self.symbol,
                "exchange": NFO_EXCHANGE,
                "tradingsymbol": tradingsymbol,
                "instrument_token": inst.get("instrument_token") or quote.get("instrument_token"),
                "expiry_date": _expiry_to_string(inst.get("expiry")),
                "strike_price": inst.get("strike"),
                "option_type": str(inst.get("instrument_type") or "").upper(),
                "spot_price": spot_price,
                "last_price": _first_present(quote, "last_price", "lastPrice", "ltp"),
                "open_interest": _first_present(quote, "oi", "open_interest", "openInterest"),
                "oi_day_high": quote.get("oi_day_high"),
                "oi_day_low": quote.get("oi_day_low"),
                "vwap": _first_present(quote, "average_price", "averagePrice", "vwap"),
                "last_trade_time": quote.get("last_trade_time"),
                "volume": quote.get("volume"),
            })

        if missing:
            logger.warning("Missing %d OI quote(s): %s", len(missing), ", ".join(missing[:10]))

        return rows


def fetch_and_store_open_interest(
    symbol: str = "NIFTY50",
    db_path: str = DB_PATH,
    band: int = CONFIG.get("OI_STRIKE_BAND", 300),
    interval: int = CONFIG.get("OI_STRIKE_INTERVAL", 50),
    kite=None,
) -> int:
    """Fetch and persist one NIFTY OI snapshot. Returns rows stored."""
    fetcher = NiftyOpenInterestFetcher(kite=kite, symbol=symbol)
    rows = fetcher.fetch_snapshot(band=band, interval=interval)
    stored = store_open_interest_snapshot(rows, db_path=db_path)
    if stored:
        rebuild_open_interest_5m_for_rows(rows, db_path=db_path)
        logger.info("Stored %d NIFTY OI rows", stored)
    else:
        logger.warning("No NIFTY OI rows stored")
    return stored
