from __future__ import annotations

import os
import sqlite3
from typing import Dict, Iterable, List, Optional

import pandas as pd

from utils.db_func import DB_PATH, fetch_market_data, store_market_data, store_sma_from_df
from utils.db_setup import create_market_data_table


REQUIRED_OHLC_COLUMNS = ["open", "high", "low", "close"]


def resample_15m_to_1h(df_15m: pd.DataFrame) -> pd.DataFrame:
    """Resample 15m candles into complete 1h candles aligned to the 09:15 market open."""
    if df_15m is None or df_15m.empty:
        return pd.DataFrame()

    df = df_15m.copy()
    df.columns = [column.lower() for column in df.columns]
    missing = [column for column in REQUIRED_OHLC_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Cannot build 1h market data; 15m data is missing columns: {missing}")

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    df = df[~df.index.duplicated(keep="last")].sort_index()

    rule_kwargs = {"rule": "1h", "offset": "15min", "label": "left", "closed": "left"}
    counts = df["close"].resample(**rule_kwargs).count()
    hourly = df.resample(**rule_kwargs).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    })

    hourly = hourly[counts == 4].dropna(subset=REQUIRED_OHLC_COLUMNS)
    if hourly.empty:
        return hourly

    if "symbol" in df.columns and not df["symbol"].dropna().empty:
        hourly["symbol"] = str(df["symbol"].dropna().iloc[-1])

    return hourly


def add_true_hourly_indicators(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Recompute true hourly indicators from hourly OHLC candles."""
    if df_1h is None or df_1h.empty:
        return pd.DataFrame()

    df = df_1h.copy()
    df.columns = [column.lower() for column in df.columns]

    median_price = (df["high"] + df["low"]) / 2.0
    df["ao_value"] = median_price.rolling(window=5, min_periods=5).mean() - median_price.rolling(window=34, min_periods=34).mean()
    df["donchian_upper"] = df["high"].rolling(window=20, min_periods=20).max()
    df["donchian_lower"] = df["low"].rolling(window=20, min_periods=20).min()
    df["donchian_mid"] = (df["donchian_upper"] + df["donchian_lower"]) / 2.0
    return df


def build_1h_market_data_from_15m(df_15m: pd.DataFrame) -> pd.DataFrame:
    """Build true hourly market data, including hourly AO and Donchian indicators."""
    hourly = resample_15m_to_1h(df_15m)
    if hourly.empty:
        return hourly
    return add_true_hourly_indicators(hourly)


def get_market_data_15m_symbols(db_path: str = DB_PATH) -> List[str]:
    """Return all distinct symbols available in market_data_15m."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='market_data_15m'")
        if cursor.fetchone() is None:
            raise RuntimeError("market_data_15m table does not exist; cannot backfill market_data_1h.")

        cursor.execute("SELECT DISTINCT symbol FROM market_data_15m WHERE symbol IS NOT NULL ORDER BY symbol")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def clear_hourly_rows_for_symbol(conn: sqlite3.Connection, symbol: str) -> None:
    """Remove existing hourly rows for a symbol so the backfill fully refreshes 1h state."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM market_data_1h WHERE symbol = ?", (symbol,))
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='market_sma_1h'")
    if cursor.fetchone() is not None:
        cursor.execute("DELETE FROM market_sma_1h WHERE symbol = ?", (symbol,))
    conn.commit()


def backfill_market_data_1h(
    db_path: str = DB_PATH,
    symbols: Optional[Iterable[str]] = None,
) -> Dict[str, int]:
    """Create/verify market_data_1h and backfill it from market_data_15m."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        create_market_data_table(conn, "1h")
    finally:
        conn.close()

    symbol_list = list(symbols) if symbols is not None else get_market_data_15m_symbols(db_path)
    if not symbol_list:
        raise RuntimeError("No symbols found in market_data_15m; nothing to backfill.")

    summary: Dict[str, int] = {}
    total_source_rows = 0

    for symbol in symbol_list:
        df_15m = fetch_market_data(symbol=symbol, interval="15m", limit=None, db_path=db_path)
        if df_15m is None or df_15m.empty:
            summary[symbol] = 0
            continue

        total_source_rows += len(df_15m)
        df_1h = build_1h_market_data_from_15m(df_15m)
        if df_1h.empty:
            summary[symbol] = 0
            continue

        conn = sqlite3.connect(db_path)
        try:
            clear_hourly_rows_for_symbol(conn, symbol)
        finally:
            conn.close()

        store_market_data(df_1h, symbol=symbol, interval="1h", db_path=db_path)
        store_sma_from_df(df_1h, symbol=symbol, interval="1h", db_path=db_path)
        summary[symbol] = len(df_1h)

    if total_source_rows == 0:
        raise RuntimeError("market_data_15m contains no source rows; cannot backfill market_data_1h.")

    return summary
