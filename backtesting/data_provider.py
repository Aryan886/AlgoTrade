"""
HistoricalDataProvider - Time-gated data access for backtesting.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd

from utils.market_data_1h import build_1h_market_data_from_15m


@dataclass
class HistoricalDataProvider:
    """Provides point-in-time historical data, preventing look-ahead bias."""

    db_path: str
    symbol: str = "NIFTY50"
    INTERVAL_DURATIONS: Dict[str, timedelta] = field(
        default_factory=lambda: {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
        },
        init=False,
        repr=False,
    )
    _market_data: Dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)
    _vix_data: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)
    _delta_cache: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)
    _data_loaded: bool = field(default=False, repr=False)

    def load_all_data(self, start_date: datetime, end_date: datetime) -> None:
        """Pre-load all data for the date range into memory."""
        # Keep enough warmup for the longest lookback currently used by the strategy
        # (1h SMA200 needs well over a month of candles when 1h data is generated from 15m).
        warmup_start = start_date - timedelta(days=60)
        conn = sqlite3.connect(self.db_path)
        try:
            for interval in ["1m", "5m", "15m", "1h"]:
                self._market_data[interval] = self._load_market_data_interval(
                    conn, interval, warmup_start, end_date
                )

            # If 1h data is missing, generate it from 15m data
            if self._market_data.get("1h") is None or self._market_data["1h"].empty:
                self._market_data["1h"] = self._generate_1h_from_15m(conn)

            self._vix_data = self._load_vix_data(conn, warmup_start, end_date)
            self._delta_cache = self._load_delta_cache(conn, warmup_start, end_date)
            self._data_loaded = True
        finally:
            conn.close()

    def _generate_1h_from_15m(self, conn: sqlite3.Connection = None) -> pd.DataFrame:
        """Generate 1H candles from 15m data and merge with pre-calculated SMAs."""
        df_15m = self._market_data.get("15m")
        if df_15m is None or df_15m.empty:
            print("Warning: Cannot generate 1h data - 15m data not available")
            return pd.DataFrame()

        print("Generating 1h candles from 15m data...")
        try:
            resampled = build_1h_market_data_from_15m(df_15m)
        except ValueError as exc:
            print(f"Warning: Could not generate 1h data from 15m candles: {exc}")
            return pd.DataFrame()

        if resampled.empty:
            print("Warning: No complete 1h candles could be generated from 15m data")
            return pd.DataFrame()

        # Try to merge with pre-calculated SMAs from market_sma_1h
        if conn is not None:
            resampled = self._merge_with_sma_1h(resampled, conn)

        print(f"Generated {len(resampled)} 1h candles from 15m data")
        return resampled

    def _merge_with_sma_1h(self, df_1h: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
        """Merge 1H OHLC data with pre-calculated SMAs from market_sma_1h table."""
        try:
            query = "SELECT * FROM market_sma_1h WHERE symbol = ? ORDER BY timestamp"
            sma_df = pd.read_sql_query(query, conn, params=(self.symbol,))
            if sma_df.empty:
                return df_1h

            sma_df["timestamp"] = pd.to_datetime(sma_df["timestamp"])
            sma_df.set_index("timestamp", inplace=True)
            sma_df.columns = [c.lower() for c in sma_df.columns]

            # Both resampled data and market_sma_1h now use :15 timestamps (9:15, 10:15, etc.)
            # No offset needed - they align naturally with market open at 9:15

            # Merge SMAs into the 1H dataframe
            sma_cols = ["sma_5", "sma_20", "sma_50", "sma_200", "sma_5_high", "sma_5_low"]
            for col in sma_cols:
                if col in sma_df.columns and col not in df_1h.columns:
                    # Only align from past data; never use a future SMA row to fill the current bar.
                    df_1h[col] = sma_df[col].reindex(df_1h.index, method="ffill", tolerance=pd.Timedelta("30min"))

            print(f"Merged pre-calculated SMAs from market_sma_1h")
        except Exception as e:
            print(f"Warning: Could not merge market_sma_1h: {e}")

        return df_1h

    def _load_market_data_interval(
        self, conn: sqlite3.Connection, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        table_name = f"market_data_{interval}"
        try:
            query = f"SELECT * FROM {table_name} WHERE symbol = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp"
            df = pd.read_sql_query(query, conn, params=(
                self.symbol, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
            ))
        except Exception as e:
            print(f"Warning: Could not load {table_name}: {e}")
            return pd.DataFrame()
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        df.columns = [c.lower() for c in df.columns]
        return df

    def _load_vix_data(self, conn: sqlite3.Connection, start: datetime, end: datetime) -> pd.DataFrame:
        try:
            query = "SELECT * FROM vix_data WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp"
            df = pd.read_sql_query(query, conn, params=(
                start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
            ))
        except Exception as e:
            print(f"Warning: Could not load vix_data: {e}")
            return pd.DataFrame()
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        df.columns = [c.lower() for c in df.columns]
        return df

    def _load_delta_cache(self, conn: sqlite3.Connection, start: datetime, end: datetime) -> pd.DataFrame:
        try:
            query = "SELECT * FROM delta_cache WHERE symbol = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp"
            df = pd.read_sql_query(query, conn, params=(
                self.symbol, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
            ))
        except Exception as e:
            print(f"Warning: Could not load delta_cache: {e}")
            return pd.DataFrame()
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.columns = [c.lower() for c in df.columns]
        return df

    def fetch_market_data(self, current_time: datetime, interval: str, limit: int = 300) -> pd.DataFrame:
        """Returns last `limit` fully known candles for the requested interval."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")
        df = self._market_data.get(interval)
        if df is None or df.empty:
            return pd.DataFrame()
        available_cutoff = self._latest_fully_available_candle_start(current_time, interval)
        filtered = df[df.index <= available_cutoff]
        if filtered.empty:
            return pd.DataFrame()
        if len(filtered) > limit:
            filtered = filtered.iloc[-limit:]
        return filtered.copy()

    def fetch_vix_data(self, current_time: datetime) -> pd.DataFrame:
        """Returns VIX data where timestamp <= current_time."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")
        if self._vix_data.empty:
            return pd.DataFrame()
        current_ts = pd.Timestamp(current_time)
        filtered = self._vix_data[self._vix_data.index <= current_ts]
        if filtered.empty:
            return pd.DataFrame()
        return filtered.copy()

    def fetch_delta_data(self, current_time: datetime) -> List[Dict[str, Any]]:
        """Returns options data for the most recent timestamp <= current_time."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")
        if self._delta_cache.empty:
            return []
        current_ts = pd.Timestamp(current_time)
        valid_records = self._delta_cache[self._delta_cache["timestamp"] <= current_ts]
        if valid_records.empty:
            return []
        latest_ts = valid_records["timestamp"].max()
        snapshot = valid_records[valid_records["timestamp"] == latest_ts]
        result = []
        for _, row in snapshot.iterrows():
            record = row.to_dict()
            record.setdefault("tradingsymbol", self._build_tradingsymbol(record))
            record.setdefault("ltp", record.get("last_price", 0.0))
            result.append(record)
        return result

    def fetch_option_price(
        self,
        current_time: datetime,
        tradingsymbol: str = "",
        strike_price: int = None,
        option_type: str = "",
        expiry: str = "",
    ) -> float | None:
        """Return the latest known quote for a specific option contract up to current_time."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")
        if self._delta_cache.empty:
            return None

        current_ts = pd.Timestamp(current_time)
        valid_records = self._delta_cache[self._delta_cache["timestamp"] <= current_ts]
        if valid_records.empty:
            return None

        if tradingsymbol and "tradingsymbol" in valid_records.columns:
            exact_rows = valid_records[valid_records["tradingsymbol"] == tradingsymbol]
            if not exact_rows.empty:
                price = exact_rows.iloc[-1].get("ltp")
                if pd.notna(price):
                    return float(price)

        if strike_price is None or not option_type:
            return None

        contract_rows = valid_records[
            (valid_records["strike_price"] == int(strike_price))
            & (valid_records["option_type"].astype(str).str.upper() == str(option_type).upper())
        ]
        if expiry and "expiry_date" in contract_rows.columns:
            expiry_rows = contract_rows[contract_rows["expiry_date"].astype(str) == str(expiry)]
            if not expiry_rows.empty:
                contract_rows = expiry_rows

        if contract_rows.empty:
            return None

        price = contract_rows.iloc[-1].get("ltp")
        if pd.notna(price):
            return float(price)
        return None

    def _build_tradingsymbol(self, record: Dict[str, Any]) -> str:
        strike = record.get("strike_price", 0)
        opt_type = record.get("option_type", "")
        expiry = record.get("expiry_date", "")
        if expiry and strike and opt_type:
            return f"NIFTY{expiry}{int(strike)}{opt_type}"
        return f"NIFTY{int(strike)}{opt_type}"

    def get_available_date_range(self) -> tuple:
        if not self._data_loaded:
            return None, None
        all_starts, all_ends = [], []
        for df in self._market_data.values():
            if not df.empty:
                all_starts.append(df.index.min())
                all_ends.append(df.index.max())
        if not all_starts:
            return None, None
        return min(all_starts).to_pydatetime(), max(all_ends).to_pydatetime()

    def get_trading_days(self, start: datetime, end: datetime) -> List[datetime]:
        if not self._data_loaded or self._market_data.get("1m") is None:
            return []
        df = self._market_data["1m"]
        mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
        filtered = df[mask]
        if filtered.empty:
            return []
        unique_dates = sorted(set(filtered.index.date))
        return [datetime.combine(d, datetime.min.time()) for d in unique_dates]

    def _latest_fully_available_candle_start(self, current_time: datetime, interval: str) -> pd.Timestamp:
        """Return the latest candle timestamp that would have been closed by current_time."""
        interval_duration = self.INTERVAL_DURATIONS.get(interval)
        if interval_duration is None:
            raise ValueError(f"Unsupported interval: {interval}")
        return pd.Timestamp(current_time) - interval_duration
