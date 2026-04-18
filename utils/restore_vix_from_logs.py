from __future__ import annotations

import argparse
import os
import re
import sqlite3
from pathlib import Path

import pandas as pd


LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - INFO - VIX calculated separately: (?P<vix>\d+(?:\.\d+)?)$"
)


def parse_log_rows(logs_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    for path in sorted(logs_dir.glob("market_data_automation.log*")):
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                match = LOG_PATTERN.match(line.strip())
                if not match:
                    continue
                rows.append(
                    {
                        "timestamp": pd.Timestamp(match.group("timestamp")),
                        "vix_value": float(match.group("vix")),
                    }
                )

    if not rows:
        return pd.DataFrame(columns=["vix_value"])

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    df = df.sort_values("timestamp")
    df = df.set_index("timestamp")
    return df


def load_existing_vix(conn: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    query = """
        SELECT timestamp, vix_value
        FROM vix_data
        WHERE symbol = ?
        ORDER BY timestamp
    """
    df = pd.read_sql_query(query, conn, params=(symbol,), parse_dates=["timestamp"])
    if df.empty:
        return pd.DataFrame(columns=["vix_value"])
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    df = df.set_index("timestamp")
    return df[["vix_value"]]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    restored = df.copy()
    restored = restored.sort_index()
    series = restored["vix_value"]
    restored["vix_ao_value"] = series.rolling(window=5).mean() - series.rolling(window=34).mean()
    restored["vix_donchian_upper"] = series.rolling(window=20).max()
    restored["vix_donchian_lower"] = series.rolling(window=20).min()
    restored["vix_donchian_mid"] = (
        restored["vix_donchian_upper"] + restored["vix_donchian_lower"]
    ) / 2.0
    return restored


def backup_current_vix(conn: sqlite3.Connection, symbol: str, backup_path: Path) -> int:
    query = """
        SELECT *
        FROM vix_data
        WHERE symbol = ?
        ORDER BY timestamp
    """
    df = pd.read_sql_query(query, conn, params=(symbol,), parse_dates=["timestamp"])
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(backup_path, index=False)
    return int(len(df))


def write_restored_vix(conn: sqlite3.Connection, symbol: str, restored_df: pd.DataFrame) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM vix_data WHERE symbol = ?", (symbol,))

    rows = []
    for ts, row in restored_df.iterrows():
        rows.append(
            (
                pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                symbol,
                float(row["vix_value"]),
                None if pd.isna(row["vix_ao_value"]) else float(row["vix_ao_value"]),
                None if pd.isna(row["vix_donchian_upper"]) else float(row["vix_donchian_upper"]),
                None if pd.isna(row["vix_donchian_lower"]) else float(row["vix_donchian_lower"]),
                None if pd.isna(row["vix_donchian_mid"]) else float(row["vix_donchian_mid"]),
            )
        )

    cursor.executemany(
        """
        INSERT INTO vix_data (
            timestamp,
            symbol,
            vix_value,
            vix_ao_value,
            vix_donchian_upper,
            vix_donchian_lower,
            vix_donchian_mid
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def restore_vix_data(db_path: Path, logs_dir: Path, symbol: str, backup_path: Path) -> None:
    log_df = parse_log_rows(logs_dir)
    if log_df.empty:
        raise RuntimeError(f"No VIX log entries found under {logs_dir}")

    conn = sqlite3.connect(db_path)
    try:
        backup_rows = backup_current_vix(conn, symbol, backup_path)
        existing_df = load_existing_vix(conn, symbol)

        merged = pd.concat([existing_df, log_df])
        merged = merged[~merged.index.duplicated(keep="last")]
        merged = merged.sort_index()
        restored = compute_indicators(merged)
        write_restored_vix(conn, symbol, restored)
    finally:
        conn.close()

    print(f"Backed up {backup_rows} existing {symbol} VIX row(s) to {backup_path}")
    print(
        f"Restored {len(restored)} {symbol} VIX row(s) from logs+existing data "
        f"covering {restored.index.min()} -> {restored.index.max()}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore vix_data rows from automation logs.")
    parser.add_argument("--db", default="db/trading_bot.db", help="SQLite database path")
    parser.add_argument("--logs-dir", default="logs", help="Directory containing automation logs")
    parser.add_argument("--symbol", default="NIFTY50", help="Symbol to restore")
    parser.add_argument(
        "--backup",
        default="exports/vix_data_backup_before_log_restore.csv",
        help="CSV path for backing up current vix_data rows before restore",
    )
    args = parser.parse_args()

    restore_vix_data(
        db_path=Path(args.db),
        logs_dir=Path(args.logs_dir),
        symbol=args.symbol,
        backup_path=Path(args.backup),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
