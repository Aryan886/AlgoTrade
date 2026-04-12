from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


def backup_current_option_data(
    conn: sqlite3.Connection,
    symbol: str,
    backup_path: Path,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> int:
    query = """
        SELECT *
        FROM option_data
        WHERE symbol = ?
    """
    params: list[object] = [symbol]
    if start:
        query += " AND timestamp >= ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)
    query += " ORDER BY timestamp, tradingsymbol"

    df = pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp"])
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(backup_path, index=False)
    return int(len(df))


def load_existing_option_keys(
    conn: sqlite3.Connection,
    symbol: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> set[tuple[str, str]]:
    query = """
        SELECT timestamp, tradingsymbol
        FROM option_data
        WHERE symbol = ?
    """
    params: list[object] = [symbol]
    if start:
        query += " AND timestamp >= ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)
    rows = conn.execute(query, params).fetchall()
    return {(str(row[0]), str(row[1])) for row in rows}


def load_delta_rows(
    conn: sqlite3.Connection,
    symbol: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    query = """
        SELECT timestamp, symbol, strike_price, option_type, ltp, expiry_date, spot_price, tradingsymbol
        FROM delta_cache
        WHERE symbol = ?
    """
    params: list[object] = [symbol]
    if start:
        query += " AND timestamp >= ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)
    query += " ORDER BY timestamp, tradingsymbol"

    return pd.read_sql_query(query, conn, params=params)


def build_missing_option_rows(
    delta_df: pd.DataFrame,
    existing_keys: set[tuple[str, str]],
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    if delta_df.empty:
        return rows

    for row in delta_df.itertuples(index=False):
        key = (str(row.timestamp), str(row.tradingsymbol))
        if key in existing_keys:
            continue

        rows.append(
            (
                row.timestamp,
                row.symbol,
                int(row.strike_price),
                str(row.option_type),
                float(row.ltp) if row.ltp is not None else None,
                None,
                str(row.expiry_date),
                float(row.spot_price) if row.spot_price is not None else None,
                str(row.tradingsymbol),
                0,
            )
        )
    return rows


def batched(items: Iterable[tuple[object, ...]], size: int) -> Iterable[list[tuple[object, ...]]]:
    batch: list[tuple[object, ...]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def restore_option_data_from_delta_cache(
    db_path: Path,
    symbol: str,
    backup_path: Path,
    start: Optional[str] = None,
    end: Optional[str] = None,
    batch_size: int = 1000,
) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    try:
        backup_rows = backup_current_option_data(conn, symbol, backup_path, start=start, end=end)
        existing_keys = load_existing_option_keys(conn, symbol, start=start, end=end)
        delta_df = load_delta_rows(conn, symbol, start=start, end=end)
        rows_to_insert = build_missing_option_rows(delta_df, existing_keys)

        for chunk in batched(rows_to_insert, batch_size):
            conn.executemany(
                """
                INSERT INTO option_data (
                    timestamp,
                    symbol,
                    strike_price,
                    option_type,
                    ltp,
                    iv,
                    expiry_date,
                    spot_price,
                    tradingsymbol,
                    open_interest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                chunk,
            )
        conn.commit()
    finally:
        conn.close()

    return backup_rows, len(rows_to_insert)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore option_data rows from delta_cache.")
    parser.add_argument("--db", default="db/trading_bot.db", help="SQLite database path")
    parser.add_argument("--symbol", default="NIFTY50", help="Symbol to restore")
    parser.add_argument("--start", default=None, help="Inclusive start timestamp/date")
    parser.add_argument("--end", default=None, help="Inclusive end timestamp/date")
    parser.add_argument(
        "--backup",
        default="exports/option_data_backup_before_delta_restore.csv",
        help="CSV path for backing up current option_data rows before restore",
    )
    args = parser.parse_args()

    backup_rows, inserted_rows = restore_option_data_from_delta_cache(
        db_path=Path(args.db),
        symbol=args.symbol,
        backup_path=Path(args.backup),
        start=args.start,
        end=args.end,
    )
    print(f"Backed up {backup_rows} existing option_data row(s) to {args.backup}")
    print(f"Inserted {inserted_rows} synthetic option_data row(s) from delta_cache")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
