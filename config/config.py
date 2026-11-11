import os

DEFAULT_DB_PATH = "db/trading_bot.db"
DB_PATH_ENV_VAR = "ALGOTRADE_DB_PATH"


def resolve_db_path(env=None) -> str:
    """Database file used by the live code; set ALGOTRADE_DB_PATH to override."""
    env = os.environ if env is None else env
    override = (env.get(DB_PATH_ENV_VAR) or "").strip()
    return override or DEFAULT_DB_PATH


DB_PATH = resolve_db_path()

CONFIG = {
    "STRIKE_BAND": 500,           # +/- band around ATM (e.g., 200 means ATM-200 to ATM+200)
    "STRIKE_INTERVAL": 50,       # Strike spacing (NIFTY: 50 or 100)
    "RISK_FREE_RATE": 0.065,      # Annual risk-free rate
    "EXPIRY_MODE": "nearest_only", # Only use soonest expiry (weekly or monthly)
    "ENABLE_DELTA_LOGGING": True, # Log all deltas to delta_cache if True
    "PRUNE_AFTER_MINUTES": 2,     # Prune option_data older than this (reduced from 10)
    "OI_STRIKE_BAND": 300,        # +/- points around dynamic ATM for OI snapshots
    "OI_STRIKE_INTERVAL": 50,     # NIFTY option strike spacing for OI snapshots
    "OI_EXPIRY_MODE": "nearest_only",
    "OI_FETCH_INTERVAL_SECONDS": 60,
    "SAVE_CANDLE_CSV": False,     # Write a CSV of the candle frame on every fetch (debugging only)
} 
