"""
HistoricalDataProvider - Time-gated data access for backtesting.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from utils.db_func import _canonical_option_order_by, _sort_option_like_dataframe
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
    _option_data: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)
    _open_interest_data: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)
    _open_interest_5m_data: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)
    _data_loaded: bool = field(default=False, repr=False)

    def load_all_data(self, start_date: datetime, end_date: datetime) -> None:
        """Pre-load all data for the date range into memory."""
        # Keep enough warmup for the longest lookback currently used by the strategy
        # Stored hourly data/SMAs are the production-parity path; fallback generation
        # from 15m remains available when market_data_1h is missing.
        warmup_start = start_date - timedelta(days=60)
        conn = sqlite3.connect(self.db_path)
        try:
            for interval in ["1m", "5m", "15m", "1h"]:
                self._market_data[interval] = self._load_market_data_interval(
                    conn, interval, warmup_start, end_date
                )

            # Prefer stored hourly data because it matches the production strategy's
            # current indicator basis. If it is unavailable, fall back to a 15m resample.
            if self._market_data.get("1h") is None or self._market_data["1h"].empty:
                self._market_data["1h"] = self._generate_1h_from_15m()

            if self._market_data.get("1h") is not None and not self._market_data["1h"].empty:
                self._market_data["1h"] = self._merge_with_sma_1h(self._market_data["1h"], conn)

            self._vix_data = self._load_vix_data(conn, warmup_start, end_date)
            self._delta_cache = self._load_delta_cache(conn, warmup_start, end_date)
            self._option_data = self._load_option_data(conn, start_date, end_date)
            self._open_interest_data = self._load_open_interest_data(conn, warmup_start, end_date)
            self._open_interest_5m_data = self._load_open_interest_5m_data(conn, warmup_start, end_date)
            self._data_loaded = True
        finally:
            conn.close()

    def _generate_1h_from_15m(self) -> pd.DataFrame:
        """Generate fallback 1H candles from 15m data when market_data_1h is unavailable."""
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

        print(f"Generated {len(resampled)} 1h candles from 15m data")
        return resampled

    def _merge_with_sma_1h(self, df_1h: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
        """Merge stored 1h SMA values by exact timestamp only.

        Exact alignment avoids pulling in a nearby future row via forward-fill while
        preserving parity with the existing production hourly/SMA tables.
        """
        try:
            query = "SELECT * FROM market_sma_1h WHERE symbol = ? ORDER BY timestamp"
            sma_df = pd.read_sql_query(query, conn, params=(self.symbol,))
            if sma_df.empty:
                return df_1h

            sma_df["timestamp"] = pd.to_datetime(sma_df["timestamp"])
            sma_df.set_index("timestamp", inplace=True)
            sma_df.columns = [c.lower() for c in sma_df.columns]

            sma_cols = ["sma_5", "sma_20", "sma_50", "sma_200", "sma_5_high", "sma_5_low"]
            for col in sma_cols:
                if col in sma_df.columns and col not in df_1h.columns:
                    df_1h[col] = sma_df[col].reindex(df_1h.index)

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

    def _load_option_data(self, conn: sqlite3.Connection, start: datetime, end: datetime) -> pd.DataFrame:
        try:
            query = (
                "SELECT * FROM option_data "
                "WHERE symbol = ? AND timestamp >= ? AND timestamp <= ? "
                f"ORDER BY {_canonical_option_order_by(include_timestamp=True)}"
            )
            df = pd.read_sql_query(query, conn, params=(
                self.symbol, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
            ))
        except Exception as e:
            print(f"Warning: Could not load option_data: {e}")
            return pd.DataFrame()
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.columns = [c.lower() for c in df.columns]
        return _sort_option_like_dataframe(df)

    def _load_open_interest_data(self, conn: sqlite3.Connection, start: datetime, end: datetime) -> pd.DataFrame:
        try:
            from utils.db_setup import create_open_interest_table

            create_open_interest_table(conn)
            query = """
                SELECT *
                FROM option_open_interest
                WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY """ + _canonical_option_order_by(include_timestamp=True)
            df = pd.read_sql_query(query, conn, params=(
                self.symbol, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
            ))
        except Exception as e:
            print(f"Warning: Could not load option_open_interest: {e}")
            return pd.DataFrame()
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        for col in ["last_trade_time"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        df.columns = [c.lower() for c in df.columns]
        return _sort_option_like_dataframe(df)

    def _load_open_interest_5m_data(self, conn: sqlite3.Connection, start: datetime, end: datetime) -> pd.DataFrame:
        try:
            from utils.db_setup import create_open_interest_5m_table

            create_open_interest_5m_table(conn)
            query = """
                SELECT *
                FROM option_open_interest_5m
                WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY """ + _canonical_option_order_by(include_timestamp=True)
            df = pd.read_sql_query(query, conn, params=(
                self.symbol, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
            ))
        except Exception as e:
            print(f"Warning: Could not load option_open_interest_5m: {e}")
            return pd.DataFrame()
        if df.empty:
            return df
        for col in ["timestamp", "last_trade_time", "source_start_timestamp", "source_end_timestamp"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        df.columns = [c.lower() for c in df.columns]
        return _sort_option_like_dataframe(df)

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
        """Returns VIX snapshots at or before current_time.

        Unlike market_data tables, vix_data is stored as irregular point-in-time
        snapshots rather than candle bars, so no candle-close offset is applied.
        """
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
        """Returns canonical backtest option data from delta_cache up to current_time."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")
        current_ts = pd.Timestamp(current_time)
        delta_snapshot = self._latest_snapshot(self._delta_cache, current_ts)
        option_snapshot = self._latest_snapshot(self._option_data, current_ts)
        return self._merge_delta_with_option_enrichment(delta_snapshot, option_snapshot)

    def fetch_next_delta_snapshot(self, current_time: datetime) -> Tuple[Optional[datetime], List[Dict[str, Any]]]:
        """Returns the first canonical delta_cache option snapshot after current_time."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")

        current_ts = pd.Timestamp(current_time)
        delta_snapshot = self._next_snapshot(self._delta_cache, current_ts, strict=False)
        if not delta_snapshot.empty:
            snapshot_ts = pd.Timestamp(delta_snapshot["timestamp"].iloc[0])
            option_snapshot = self._latest_snapshot(self._option_data, snapshot_ts)
            return snapshot_ts.to_pydatetime(), self._merge_delta_with_option_enrichment(delta_snapshot, option_snapshot)

        option_snapshot = self._next_snapshot(self._option_data, current_ts, strict=False)
        if option_snapshot.empty:
            return None, []

        snapshot_ts = pd.Timestamp(option_snapshot["timestamp"].iloc[0])
        return snapshot_ts.to_pydatetime(), self._normalize_snapshot(option_snapshot, source="option")

    def fetch_option_snapshot(self, current_time: datetime) -> List[Dict[str, Any]]:
        """Return the latest pure option snapshot at or before current_time."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")
        current_ts = pd.Timestamp(current_time)
        option_snapshot = self._latest_snapshot(self._option_data, current_ts)
        return self._normalize_snapshot(option_snapshot, source="option")

    def fetch_next_option_snapshot(self, current_time: datetime) -> Tuple[Optional[datetime], List[Dict[str, Any]]]:
        """Return the first pure option snapshot at or after current_time."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")

        current_ts = pd.Timestamp(current_time)
        option_snapshot = self._next_snapshot(self._option_data, current_ts, strict=False)
        if option_snapshot.empty:
            return None, []

        snapshot_ts = pd.Timestamp(option_snapshot["timestamp"].iloc[0])
        return snapshot_ts.to_pydatetime(), self._normalize_snapshot(option_snapshot, source="option")

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

        current_ts = pd.Timestamp(current_time)
        valid_records = self._delta_cache[self._delta_cache["timestamp"] <= current_ts] if not self._delta_cache.empty else pd.DataFrame()
        option_records = self._option_data[self._option_data["timestamp"] <= current_ts] if not self._option_data.empty else pd.DataFrame()

        if tradingsymbol and "tradingsymbol" in valid_records.columns:
            exact_rows = valid_records[valid_records["tradingsymbol"] == tradingsymbol]
            if not exact_rows.empty:
                price = exact_rows.iloc[-1].get("ltp")
                if pd.notna(price):
                    return float(price)

        if tradingsymbol and "tradingsymbol" in option_records.columns:
            exact_rows = option_records[option_records["tradingsymbol"] == tradingsymbol]
            if not exact_rows.empty:
                price = exact_rows.iloc[-1].get("ltp")
                if pd.notna(price):
                    return float(price)

        if strike_price is None or not option_type:
            return None

        contract_rows = valid_records[
            (valid_records["strike_price"] == int(strike_price))
            & (valid_records["option_type"].astype(str).str.upper() == str(option_type).upper())
        ] if not valid_records.empty else pd.DataFrame()
        if expiry and "expiry_date" in contract_rows.columns:
            expiry_rows = contract_rows[contract_rows["expiry_date"].astype(str) == str(expiry)]
            if not expiry_rows.empty:
                contract_rows = expiry_rows

        if not contract_rows.empty:
            price = contract_rows.iloc[-1].get("ltp")
            if pd.notna(price):
                return float(price)

        option_contract_rows = option_records[
            (option_records["strike_price"] == int(strike_price))
            & (option_records["option_type"].astype(str).str.upper() == str(option_type).upper())
        ] if not option_records.empty else pd.DataFrame()
        if expiry and "expiry_date" in option_contract_rows.columns:
            expiry_rows = option_contract_rows[option_contract_rows["expiry_date"].astype(str) == str(expiry)]
            if not expiry_rows.empty:
                option_contract_rows = expiry_rows

        if not option_contract_rows.empty:
            price = option_contract_rows.iloc[-1].get("ltp")
            if pd.notna(price):
                return float(price)
        return None

    def fetch_open_interest_snapshot(self, current_time: datetime) -> List[Dict[str, Any]]:
        """Return the most recent OI snapshot at or before current_time."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")
        current_ts = pd.Timestamp(current_time)
        snapshot = self._latest_snapshot(self._open_interest_data, current_ts)
        if snapshot.empty:
            return []
        return snapshot.to_dict("records")

    def fetch_open_interest_history(self, current_time: datetime) -> pd.DataFrame:
        """Return all persisted OI rows at or before current_time."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")
        if self._open_interest_data.empty:
            return pd.DataFrame()
        current_ts = pd.Timestamp(current_time)
        history = self._open_interest_data[self._open_interest_data["timestamp"] <= current_ts].copy()
        return _sort_option_like_dataframe(history)

    def fetch_open_interest_5m_snapshot(self, current_time: datetime) -> List[Dict[str, Any]]:
        """Return the latest derived 5-minute OI snapshot at or before current_time."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")
        if self._open_interest_5m_data.empty:
            return []
        current_bucket = pd.Timestamp(current_time).floor("5min")
        snapshot = self._latest_snapshot(self._open_interest_5m_data, current_bucket)
        if snapshot.empty:
            return []
        return snapshot.to_dict("records")

    def fetch_open_interest_5m_history(self, current_time: datetime) -> pd.DataFrame:
        """Return all derived 5-minute OI rows at or before the requested bucket."""
        if not self._data_loaded:
            raise RuntimeError("Data not loaded. Call load_all_data() first.")
        if self._open_interest_5m_data.empty:
            return pd.DataFrame()
        current_bucket = pd.Timestamp(current_time).floor("5min")
        history = self._open_interest_5m_data[self._open_interest_5m_data["timestamp"] <= current_bucket].copy()
        return _sort_option_like_dataframe(history)

    def _latest_snapshot(self, df: pd.DataFrame, current_ts: pd.Timestamp) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        valid_records = df[df["timestamp"] <= current_ts]
        if valid_records.empty:
            return pd.DataFrame()
        latest_ts = valid_records["timestamp"].max()
        snapshot = valid_records[valid_records["timestamp"] == latest_ts].copy()
        return _sort_option_like_dataframe(snapshot)

    def _next_snapshot(self, df: pd.DataFrame, current_ts: pd.Timestamp, strict: bool = True) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        if strict:
            future_records = df[df["timestamp"] > current_ts]
        else:
            future_records = df[df["timestamp"] >= current_ts]
        if future_records.empty:
            return pd.DataFrame()
        next_ts = future_records["timestamp"].min()
        return future_records[future_records["timestamp"] == next_ts].copy()

    def _normalize_snapshot(self, snapshot: pd.DataFrame, source: str) -> List[Dict[str, Any]]:
        if snapshot is None or snapshot.empty:
            return []
        result = []
        for _, row in snapshot.iterrows():
            record = row.to_dict()
            record.setdefault("tradingsymbol", self._build_tradingsymbol(record))
            if source == "option":
                record.setdefault("ltp", record.get("ltp") or record.get("last_price") or 0.0)
            else:
                record.setdefault("ltp", record.get("ltp") or record.get("last_price") or 0.0)
            result.append(record)
        return result

    def _merge_delta_with_option_enrichment(
        self,
        delta_snapshot: pd.DataFrame,
        option_snapshot: pd.DataFrame,
    ) -> List[Dict[str, Any]]:
        delta_records = self._normalize_snapshot(delta_snapshot, source="delta")
        option_records = self._normalize_snapshot(option_snapshot, source="option")
        if not delta_records:
            return option_records
        if not option_records:
            return delta_records

        option_by_key = {self._snapshot_contract_key(record): record for record in option_records}
        merged: List[Dict[str, Any]] = []
        for delta_record in delta_records:
            merged_record = dict(delta_record)
            option_record = option_by_key.get(self._snapshot_contract_key(delta_record))
            if option_record:
                for key, value in option_record.items():
                    if key not in merged_record or pd.isna(merged_record.get(key)):
                        merged_record[key] = value
            merged.append(merged_record)
        return merged

    @staticmethod
    def _snapshot_contract_key(record: Dict[str, Any]) -> tuple[Any, Any, str, str]:
        return (
            record.get("tradingsymbol"),
            record.get("strike_price"),
            (record.get("option_type") or "").upper(),
            str(record.get("expiry_date") or record.get("expiry") or ""),
        )

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

    def get_backtest_data_coverage(self) -> Dict[str, Dict[str, Any]]:
        """Return loaded coverage windows for required backtest tables."""
        if not self._data_loaded:
            return {}

        coverage = {
            "market_data_1m": self._describe_loaded_frame(self._market_data.get("1m")),
            "market_data_5m": self._describe_loaded_frame(self._market_data.get("5m")),
            "vix_data": self._describe_loaded_frame(self._vix_data),
            "delta_cache": self._describe_loaded_snapshot_frame(self._delta_cache),
            "option_data": self._describe_loaded_snapshot_frame(self._option_data),
            "option_open_interest": self._describe_loaded_snapshot_frame(self._open_interest_data),
            "option_open_interest_5m": self._describe_loaded_snapshot_frame(self._open_interest_5m_data),
        }
        return coverage

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
        """Return the latest open-timestamped candle that would be closed by current_time.

        Backtesting assumes market_data tables use candle-open timestamps:
        a 5m row stamped 09:20:00 represents the candle that opened at 09:20 and
        closes at 09:25, so it is only available once current_time >= 09:25.
        """
        interval_duration = self.INTERVAL_DURATIONS.get(interval)
        if interval_duration is None:
            raise ValueError(f"Unsupported interval: {interval}")
        return pd.Timestamp(current_time) - interval_duration

    def _describe_loaded_frame(self, df: Optional[pd.DataFrame]) -> Dict[str, Any]:
        if df is None or df.empty:
            return {"rows": 0, "start": None, "end": None}

        start = pd.Timestamp(df.index.min()).to_pydatetime()
        end = pd.Timestamp(df.index.max()).to_pydatetime()
        return {"rows": int(len(df)), "start": start, "end": end}

    def _describe_loaded_snapshot_frame(self, df: Optional[pd.DataFrame]) -> Dict[str, Any]:
        if df is None or df.empty:
            return {"rows": 0, "start": None, "end": None}

        if "timestamp" not in df.columns:
            return {"rows": int(len(df)), "start": None, "end": None}

        timestamps = pd.to_datetime(df["timestamp"], errors="coerce").dropna()
        if timestamps.empty:
            return {"rows": int(len(df)), "start": None, "end": None}

        start = pd.Timestamp(timestamps.min()).to_pydatetime()
        end = pd.Timestamp(timestamps.max()).to_pydatetime()
        return {"rows": int(len(df)), "start": start, "end": end}
