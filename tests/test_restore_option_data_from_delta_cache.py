import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.restore_option_data_from_delta_cache import restore_option_data_from_delta_cache


class RestoreOptionDataFromDeltaCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "restore_option_data.db")
        self.backup_path = os.path.join(self.temp_dir.name, "option_backup.csv")
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE option_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strike_price INTEGER NOT NULL,
                option_type TEXT NOT NULL,
                ltp REAL,
                iv REAL,
                expiry_date TEXT NOT NULL,
                spot_price REAL,
                tradingsymbol TEXT NOT NULL,
                open_interest INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE delta_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                strike_price INTEGER NOT NULL,
                option_type TEXT NOT NULL,
                delta REAL,
                expiry_date TEXT NOT NULL,
                spot_price REAL,
                symbol TEXT NOT NULL,
                ltp REAL,
                tradingsymbol TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

    def _fetch_option_rows(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """
            SELECT timestamp, strike_price, option_type, ltp, iv, expiry_date, spot_price, tradingsymbol, open_interest
            FROM option_data
            ORDER BY timestamp, tradingsymbol
            """
        ).fetchall()
        conn.close()
        return rows

    def test_restore_backfills_missing_option_rows_from_delta_cache(self):
        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            """
            INSERT INTO delta_cache (
                timestamp, strike_price, option_type, delta, expiry_date, spot_price, symbol, ltp, tradingsymbol
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("2026-03-24 09:15:05", 22850, "CE", 10.0, "2026-03-24", 22870.0, "NIFTY50", 101.5, "NIFTY22850CE"),
                ("2026-03-24 09:15:05", 22850, "PE", -11.0, "2026-03-24", 22870.0, "NIFTY50", 98.25, "NIFTY22850PE"),
            ],
        )
        conn.commit()
        conn.close()

        backup_rows, inserted_rows = restore_option_data_from_delta_cache(
            db_path=Path(self.db_path),
            symbol="NIFTY50",
            backup_path=Path(self.backup_path),
            start="2026-03-24",
            end="2026-03-24 23:59:59",
        )

        rows = self._fetch_option_rows()
        self.assertEqual(backup_rows, 0)
        self.assertEqual(inserted_rows, 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "2026-03-24 09:15:05")
        self.assertEqual(rows[0][4], None)
        self.assertEqual(rows[0][8], 0)
        self.assertTrue(os.path.exists(self.backup_path))

    def test_restore_does_not_overwrite_existing_option_rows(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO option_data (
                timestamp, symbol, strike_price, option_type, ltp, iv, expiry_date, spot_price, tradingsymbol, open_interest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-03-24 09:15:05", "NIFTY50", 22850, "CE", 150.0, 0.22, "2026-03-24", 22870.0, "NIFTY22850CE", 1234),
        )
        conn.execute(
            """
            INSERT INTO delta_cache (
                timestamp, strike_price, option_type, delta, expiry_date, spot_price, symbol, ltp, tradingsymbol
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-03-24 09:15:05", 22850, "CE", 10.0, "2026-03-24", 22870.0, "NIFTY50", 101.5, "NIFTY22850CE"),
        )
        conn.commit()
        conn.close()

        backup_rows, inserted_rows = restore_option_data_from_delta_cache(
            db_path=Path(self.db_path),
            symbol="NIFTY50",
            backup_path=Path(self.backup_path),
            start="2026-03-24",
            end="2026-03-24 23:59:59",
        )

        rows = self._fetch_option_rows()
        self.assertEqual(backup_rows, 1)
        self.assertEqual(inserted_rows, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], 150.0)
        self.assertEqual(rows[0][4], 0.22)
        self.assertEqual(rows[0][8], 1234)

    def test_restore_preserves_chronological_snapshot_order(self):
        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            """
            INSERT INTO delta_cache (
                timestamp, strike_price, option_type, delta, expiry_date, spot_price, symbol, ltp, tradingsymbol
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("2026-03-24 09:16:05", 22900, "CE", 8.0, "2026-03-24", 22880.0, "NIFTY50", 91.0, "NIFTY22900CE"),
                ("2026-03-24 09:15:05", 22850, "PE", -11.0, "2026-03-24", 22870.0, "NIFTY50", 98.25, "NIFTY22850PE"),
                ("2026-03-24 09:15:05", 22850, "CE", 10.0, "2026-03-24", 22870.0, "NIFTY50", 101.5, "NIFTY22850CE"),
            ],
        )
        conn.commit()
        conn.close()

        _, inserted_rows = restore_option_data_from_delta_cache(
            db_path=Path(self.db_path),
            symbol="NIFTY50",
            backup_path=Path(self.backup_path),
            start="2026-03-24",
            end="2026-03-24 23:59:59",
        )

        rows = self._fetch_option_rows()
        self.assertEqual(inserted_rows, 3)
        self.assertEqual([row[0] for row in rows], [
            "2026-03-24 09:15:05",
            "2026-03-24 09:15:05",
            "2026-03-24 09:16:05",
        ])
