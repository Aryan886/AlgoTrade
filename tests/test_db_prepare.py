import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_setup import create_tables, prepare_database


class PrepareDatabaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "db", "trading_bot.db")
        create_tables(self.db_path)

    def _query(self, sql):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def _execute(self, sql):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def test_database_uses_wal_journal_after_prepare(self):
        prepare_database(self.db_path)

        self.assertEqual(self._query("PRAGMA journal_mode")[0][0].lower(), "wal")

    def _indexed_columns(self, table):
        """Column lists of every index on a table, e.g. [('symbol', 'timestamp')]."""
        result = []
        for row in self._query(f"PRAGMA index_list({table})"):
            cols = tuple(col[2] for col in self._query(f"PRAGMA index_info({row[1]})"))
            result.append(cols)
        return result

    def test_option_snapshot_tables_are_indexed_for_latest_lookup(self):
        prepare_database(self.db_path)

        self.assertIn(("symbol", "timestamp"), self._indexed_columns("option_data"))
        self.assertIn(("symbol", "timestamp"), self._indexed_columns("delta_cache"))

    def test_delta_cache_is_indexed_for_held_contract_price_lookup(self):
        # Live databases carry a tradingsymbol column added after the base schema.
        self._execute("ALTER TABLE delta_cache ADD COLUMN tradingsymbol TEXT")

        prepare_database(self.db_path)

        self.assertIn(
            ("symbol", "tradingsymbol", "timestamp"),
            self._indexed_columns("delta_cache"),
        )

    def test_redundant_market_data_index_is_dropped_and_unique_index_kept(self):
        self._execute(
            "CREATE INDEX idx_market_data_1m_symbol_timestamp ON market_data_1m (symbol, timestamp)"
        )

        prepare_database(self.db_path)

        self.assertEqual(self._indexed_columns("market_data_1m"), [("symbol", "timestamp")])
        unique_flags = [row[2] for row in self._query("PRAGMA index_list(market_data_1m)")]
        self.assertEqual(unique_flags, [1])

    def test_prepare_works_on_database_without_option_tables(self):
        empty_path = os.path.join(self._tmp.name, "empty.db")
        sqlite3.connect(empty_path).close()

        prepare_database(empty_path)  # must not raise


if __name__ == "__main__":
    unittest.main()
