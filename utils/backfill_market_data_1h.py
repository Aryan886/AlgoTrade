from __future__ import annotations

import argparse
import sys

from utils.db_func import DB_PATH
from utils.market_data_1h import backfill_market_data_1h


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create and backfill market_data_1h from market_data_15m.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DB_PATH,
        help=f"Path to SQLite database (default: {DB_PATH})",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="Optional symbol to backfill. Repeat --symbol to process multiple symbols.",
    )
    args = parser.parse_args()

    try:
        summary = backfill_market_data_1h(db_path=args.db, symbols=args.symbol)
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    total_rows = sum(summary.values())
    print("market_data_1h backfill completed.")
    for symbol, rows in summary.items():
        print(f"  {symbol}: {rows} hourly rows upserted")
    print(f"Total hourly rows upserted: {total_rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
