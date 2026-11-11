"""
Shared live quotes: one Kite request per instrument per minute.

The delta, open-interest and VIX jobs all need the NIFTY spot price and quotes
for the nearest-expiry option chain. They ask for them here, so whichever job
runs first in a minute does the Kite round trips and the others reuse them.
"""

import logging
import time
import weakref
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

NIFTY_SPOT_QUOTE_KEY = "NSE:NIFTY 50"

# Kite's full-quote endpoint accepts up to 500 instruments per request.
QUOTE_BATCH_SIZE = 500
# ...and allows about one quote request per second, so pause between batches.
BATCH_PAUSE_SECONDS = 0.2

# Quotes held per client: {kite: (minute, {quote_key: quote})}.
# Keyed weakly so a replaced client (new login) drops its quotes.
_minute_quotes = weakref.WeakKeyDictionary()


def reset_live_quotes() -> None:
    """Forget every held quote (used by tests and manual refreshes)."""
    _minute_quotes.clear()


def _quotes_for_current_minute(kite) -> Dict[str, Any]:
    minute = datetime.now().replace(second=0, microsecond=0)
    try:
        held = _minute_quotes.get(kite)
        if held is None or held[0] != minute:
            held = (minute, {})
            _minute_quotes[kite] = held
        return held[1]
    except TypeError:
        # Client object cannot be weakly referenced; nothing is shared.
        return {}


def get_spot_price(kite) -> Optional[float]:
    """NIFTY 50 spot price, requested from Kite at most once per minute."""
    held = _quotes_for_current_minute(kite)
    quote = held.get(NIFTY_SPOT_QUOTE_KEY)
    if quote is None:
        response = kite.quote(NIFTY_SPOT_QUOTE_KEY)
        quote = response.get(NIFTY_SPOT_QUOTE_KEY) if isinstance(response, dict) else None
        if not isinstance(quote, dict) or quote.get("last_price") is None:
            return None
        held[NIFTY_SPOT_QUOTE_KEY] = quote
    return quote["last_price"]


def get_quotes(kite, quote_keys: Iterable[str]) -> Dict[str, Any]:
    """
    Quotes for the given keys. Keys already quoted this minute are reused; the
    rest are requested in batches. Keys Kite did not return are simply absent,
    so a later call asks for them again.
    """
    held = _quotes_for_current_minute(kite)
    keys = list(dict.fromkeys(quote_keys))
    missing = [key for key in keys if key not in held]

    for start in range(0, len(missing), QUOTE_BATCH_SIZE):
        if start:
            time.sleep(BATCH_PAUSE_SECONDS)
        batch = missing[start:start + QUOTE_BATCH_SIZE]
        try:
            response = kite.quote(batch)
        except Exception as exc:
            logger.warning("Quote request for %d instrument(s) failed: %s", len(batch), exc)
            continue
        if not isinstance(response, dict):
            logger.warning("Unexpected quote response type: %s", type(response).__name__)
            continue
        for key, quote in response.items():
            if quote:
                held[key] = quote

    return {key: held[key] for key in keys if key in held}
