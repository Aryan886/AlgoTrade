from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
import re
from typing import Optional

import pandas as pd

from core.strat_nifty import _simple_rsi


DEFAULT_DB_CANDIDATES = [
    Path("db/trading_bot.db"),
]


def resolve_db_path(explicit_path: Optional[str]) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        if not path.exists():
            raise FileNotFoundError(f"Database file not found: {path}")
        return path

    for candidate in DEFAULT_DB_CANDIDATES:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Could not find a database file automatically. "
        "Pass one explicitly with --db-path."
    )


def validate_identifier(identifier: str, kind: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError(f"Invalid {kind}: {identifier!r}")
    return identifier


def validate_interval_suffix(interval: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", interval):
        raise ValueError(f"Invalid interval: {interval!r}")
    return interval


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cursor = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    )
    return cursor.fetchone() is not None


def get_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    validate_identifier(table_name, "table name")
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]).lower() for row in rows}


def table_has_symbol_rows(conn: sqlite3.Connection, table_name: str, symbol: str) -> bool:
    validate_identifier(table_name, "table name")
    cursor = conn.execute(
        f"SELECT 1 FROM {table_name} WHERE symbol = ? LIMIT 1",
        (symbol,),
    )
    return cursor.fetchone() is not None


def resolve_market_table(
    conn: sqlite3.Connection,
    interval: str,
    symbol: str,
    explicit_table: Optional[str] = None,
) -> tuple[str, set[str]]:
    if explicit_table:
        table_name = validate_identifier(explicit_table, "table name")
        if not table_exists(conn, table_name):
            raise ValueError(f"Table not found in database: {table_name}")
        columns = get_table_columns(conn, table_name)
        required = {"timestamp", "close"}
        missing = sorted(required - columns)
        if missing:
            raise ValueError(
                f"Table {table_name} is missing required columns: {', '.join(missing)}"
            )
        return table_name, columns

    interval = validate_interval_suffix(interval.replace("-", "_"))
    candidate_tables = [
        f"market_data_{interval}",
        f"equity_data_{interval}",
    ]

    matching_candidates: list[tuple[str, set[str], bool]] = []
    fallback_candidates: list[tuple[str, set[str], bool]] = []

    for table_name in candidate_tables:
        if not table_exists(conn, table_name):
            continue

        columns = get_table_columns(conn, table_name)
        if not {"timestamp", "close"}.issubset(columns):
            continue

        has_symbol = "symbol" in columns
        fallback_candidates.append((table_name, columns, has_symbol))

        if has_symbol and table_has_symbol_rows(conn, table_name, symbol):
            matching_candidates.append((table_name, columns, has_symbol))

    if matching_candidates:
        table_name, columns, _ = matching_candidates[0]
        return table_name, columns

    if fallback_candidates:
        table_name, columns, _ = fallback_candidates[0]
        return table_name, columns

    raise ValueError(
        "Could not find a candle table for the requested interval. "
        f"Checked: {', '.join(candidate_tables)}"
    )


def load_market_data(
    db_path: Path,
    symbol: str,
    interval: str,
    limit: Optional[int],
    table_name: Optional[str] = None,
) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        resolved_table_name, columns = resolve_market_table(
            conn=conn,
            interval=interval,
            symbol=symbol,
            explicit_table=table_name,
        )

        selected_columns = ["timestamp", "close"]
        params = []
        where_clauses = []

        if "symbol" in columns:
            where_clauses.append("symbol = ?")
            params.append(symbol)

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        base_select = ", ".join(selected_columns)
        query = f"""
            SELECT {base_select}
            FROM {resolved_table_name}
            {where_sql}
            ORDER BY timestamp
        """

        if limit is not None:
            limit_params = [*params, int(limit)]
            query = f"""
                SELECT {base_select}
                FROM (
                    SELECT {base_select}
                    FROM {resolved_table_name}
                    {where_sql}
                    ORDER BY timestamp DESC
                    LIMIT ?
                )
                ORDER BY timestamp
            """
            params = limit_params

        df = pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp"])
        df.attrs["source_table"] = resolved_table_name
    finally:
        conn.close()

    if df.empty:
        return df

    df = df.dropna(subset=["close"]).copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"]).copy()
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def simple_rsi_series(df: pd.DataFrame, period: int = 14, price_col: str = "close") -> pd.Series:
    prices = pd.to_numeric(df[price_col], errors="coerce")
    delta = prices.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)

    avg_gain = gains.rolling(window=period, min_periods=period).mean()
    avg_loss = losses.rolling(window=period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    both_zero = (avg_gain == 0.0) & (avg_loss == 0.0)
    rsi = rsi.mask(both_zero, 50.0)
    rsi = rsi.mask((avg_gain > 0.0) & (avg_loss == 0.0), 100.0)
    rsi = rsi.mask((avg_gain == 0.0) & (avg_loss > 0.0), 0.0)
    return rsi


def reference_rsi_series(df: pd.DataFrame, period: int = 14, price_col: str = "close") -> pd.Series:
    closes = pd.to_numeric(df[price_col], errors="coerce").tolist()
    result = [pd.NA] * len(closes)

    if period <= 0:
        raise ValueError("period must be greater than 0")

    for idx in range(period, len(closes)):
        window = closes[idx - period : idx + 1]
        deltas = [window[i] - window[i - 1] for i in range(1, len(window))]
        gains = [max(delta, 0.0) for delta in deltas]
        losses = [max(-delta, 0.0) for delta in deltas]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_gain == 0.0 and avg_loss == 0.0:
            result[idx] = 50.0
        elif avg_loss == 0.0:
            result[idx] = 100.0
        elif avg_gain == 0.0:
            result[idx] = 0.0
        else:
            rs = avg_gain / avg_loss
            result[idx] = 100.0 - (100.0 / (1.0 + rs))

    return pd.Series(result, index=df.index, dtype="Float64")


def build_comparison_frame(df: pd.DataFrame, period: int, tolerance: float) -> pd.DataFrame:
    result = df.copy()
    result.attrs = dict(df.attrs)
    result["rsi_strategy_formula"] = simple_rsi_series(result, period=period)
    result["rsi_reference_manual"] = reference_rsi_series(result, period=period)
    result["diff"] = (
        result["rsi_strategy_formula"].astype("Float64")
        - result["rsi_reference_manual"].astype("Float64")
    ).abs()
    result["matches"] = result["diff"].fillna(0.0) <= tolerance
    return result


def print_summary(
    df: pd.DataFrame,
    symbol: str,
    interval: str,
    period: int,
    tail: int,
    tolerance: float,
) -> int:
    valid = df.dropna(subset=["rsi_strategy_formula", "rsi_reference_manual"]).copy()
    if valid.empty:
        print("Not enough candle history to compute RSI yet.")
        return 2

    last_strategy = float(valid.iloc[-1]["rsi_strategy_formula"])
    last_reference = float(valid.iloc[-1]["rsi_reference_manual"])
    helper_latest = _simple_rsi(df[["close"]], period=period)
    max_diff = float(valid["diff"].max())
    mismatch_count = int((valid["diff"] > tolerance).sum())

    print(f"DB rows loaded        : {len(df)}")
    print(f"Symbol / interval     : {symbol} / {interval}")
    print(f"RSI period            : {period}")
    print(f"First candle          : {df.iloc[0]['timestamp']}")
    print(f"Last candle           : {df.iloc[-1]['timestamp']}")
    print(f"Latest strategy RSI   : {last_strategy:.10f}")
    print(f"Latest reference RSI  : {last_reference:.10f}")
    if helper_latest is None:
        print("Strategy helper RSI   : None")
    else:
        print(f"Strategy helper RSI   : {helper_latest:.10f}")
    print(f"Max abs diff          : {max_diff:.12f}")
    print(f"Mismatched rows       : {mismatch_count}")
    print(f"Tolerance             : {tolerance}")

    print("\nLast rows:")
    display_cols = ["timestamp", "close", "rsi_strategy_formula", "rsi_reference_manual", "diff", "matches"]
    print(valid[display_cols].tail(tail).to_string(index=False))

    if helper_latest is not None and abs(helper_latest - last_reference) > tolerance:
        print("\nFAIL: Latest helper RSI does not match the manual reference.")
        return 1

    if mismatch_count:
        print("\nFAIL: RSI mismatch found in historical candles.")
        return 1

    print("\nPASS: Strategy RSI matches the manual reference across the checked history.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-check simple RSI values on historical candles stored in SQLite."
    )
    parser.add_argument("--db-path", help="Path to SQLite DB. Auto-detects common paths if omitted.")
    parser.add_argument("--symbol", default="NIFTY50", help="Symbol to inspect. Default: NIFTY50")
    parser.add_argument("--interval", default="5m", help="Market data interval table suffix. Default: 5m")
    parser.add_argument(
        "--table-name",
        help="Optional explicit candle table name. Auto-detects market_data_<interval> or equity_data_<interval> if omitted.",
    )
    parser.add_argument("--period", type=int, default=14, help="RSI period. Default: 14")
    parser.add_argument("--limit", type=int, default=500, help="Latest N candles to inspect. Default: 500")
    parser.add_argument("--tail", type=int, default=20, help="How many final comparison rows to print. Default: 20")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-9,
        help="Allowed absolute diff before failing. Default: 1e-9",
    )
    parser.add_argument("--csv-out", help="Optional path to save the full comparison as CSV.")
    args = parser.parse_args()

    db_path = resolve_db_path(args.db_path)
    df = load_market_data(
        db_path=db_path,
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        table_name=args.table_name,
    )

    if df.empty:
        source_table = args.table_name or f"market_data_{args.interval} / equity_data_{args.interval}"
        print(
            f"No candle data found for symbol={args.symbol} in "
            f"{source_table} at {db_path}"
        )
        return 2

    comparison = build_comparison_frame(df=df, period=args.period, tolerance=args.tolerance)

    if args.csv_out:
        out_path = Path(args.csv_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        comparison.to_csv(out_path, index=False)
        print(f"Saved comparison CSV to: {out_path}")

    print(f"Using database        : {db_path}")
    if comparison.attrs.get("source_table"):
        print(f"Source candle table   : {comparison.attrs['source_table']}")
    return print_summary(
        df=comparison,
        symbol=args.symbol,
        interval=args.interval,
        period=args.period,
        tail=args.tail,
        tolerance=args.tolerance,
    )


if __name__ == "__main__":
    raise SystemExit(main())
