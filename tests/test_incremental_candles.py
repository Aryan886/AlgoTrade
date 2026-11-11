import math
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import data_fetcher
from utils.data_fetcher import fetch_and_save_data
from utils.db_func import fetch_market_data
from utils.db_setup import create_tables

IST = timezone(timedelta(hours=5, minutes=30))
STEP_MINUTES = {"minute": 1, "5minute": 5, "15minute": 15, "60minute": 60}


def _naive(value):
    return value.replace(tzinfo=None) if value.tzinfo else value


class _FakeKite:
    """Kite stand-in serving deterministic candles and recording every request."""

    def __init__(self):
        self.historical_calls = []

    def instruments(self, exchange):
        return [{"tradingsymbol": "NIFTY 50", "instrument_token": 256265}]

    def historical_data(self, instrument_token, from_date, to_date, interval):
        from_date, to_date = _naive(from_date), _naive(to_date)
        self.historical_calls.append((interval, from_date, to_date))
        step = timedelta(minutes=STEP_MINUTES[interval])

        candles = []
        day = from_date.date()
        while day <= to_date.date():
            if day.weekday() < 5:
                candle_time = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=15)
                session_end = candle_time.replace(hour=15, minute=30)
                while candle_time < session_end and candle_time <= to_date:
                    if candle_time >= from_date:
                        candles.append(self._candle(candle_time, step, to_date))
                    candle_time += step
            day += timedelta(days=1)
        return candles

    @staticmethod
    def _candle(candle_time, step, to_date):
        k = candle_time.timestamp() / step.total_seconds()
        open_price = 25000 + 60 * math.sin(k / 9) + 25 * math.sin(k / 41)
        close_price = open_price + 12 * math.sin(k * 1.7)
        if candle_time + step > to_date:
            # Still-forming candle: its close moves until the candle completes.
            progress = (to_date - candle_time) / step
            close_price = open_price + (close_price - open_price) * progress
        return {
            "date": candle_time.replace(tzinfo=IST),
            "open": open_price,
            "high": max(open_price, close_price) + 3 + 2 * math.sin(k),
            "low": min(open_price, close_price) - 3 - 2 * math.cos(k),
            "close": close_price,
            "volume": 0,
        }


FIRST_RUN = datetime(2026, 11, 11, 10, 0, 30)   # a Wednesday, market open
SECOND_RUN = datetime(2026, 11, 11, 10, 2, 10)


class IncrementalCandleTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.db_path = os.path.join(temp_dir.name, "test.db")
        self.data_dir = os.path.join(temp_dir.name, "datas")
        create_tables(self.db_path)

        self.kite = _FakeKite()
        for target, value in (
            ("utils.data_fetcher.kite_from_saved_token", lambda: self.kite),
            ("utils.data_fetcher.get_data_path", lambda name: __import__("pathlib").Path(self.data_dir) / name),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        data_fetcher.reset_candle_windows()
        self.addCleanup(data_fetcher.reset_candle_windows)

    def _run(self, now, interval="1m", db_path=None):
        return fetch_and_save_data(
            intervals=[interval],
            return_interval=interval,
            db_path=db_path or self.db_path,
            now=now,
        )

    def test_second_run_requests_only_candles_since_the_last_one_held(self):
        self._run(FIRST_RUN)
        self._run(SECOND_RUN)

        first_call, second_call = self.kite.historical_calls
        self.assertEqual(first_call[1], datetime(2026, 11, 6, 9, 15))   # 5 days of history
        self.assertEqual(second_call[1], datetime(2026, 11, 11, 10, 0))  # the candle still forming at run 1
        self.assertEqual(second_call[2], SECOND_RUN)

    def _stored(self, table_prefix="market_data", db_path=None, interval="1m"):
        conn = sqlite3.connect(db_path or self.db_path)
        try:
            cursor = conn.execute(
                f"SELECT * FROM {table_prefix}_{interval} WHERE symbol = 'NIFTY50' ORDER BY timestamp"
            )
            columns = [column[0] for column in cursor.description]
            return {row[columns.index("timestamp")]: dict(zip(columns, row)) for row in cursor.fetchall()}
        finally:
            conn.close()

    def test_second_run_writes_only_new_and_changed_candles(self):
        self._run(FIRST_RUN)
        # Mark every stored row; a rewrite by the next run would clear the mark.
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE market_data_1m SET close = -1")
        conn.execute("UPDATE market_sma_1m SET sma_200 = -1")
        conn.commit()
        conn.close()

        self._run(SECOND_RUN)

        expected_rewritten = [
            "2026-11-11 10:00:00",  # was still forming at the first run
            "2026-11-11 10:01:00",
            "2026-11-11 10:02:00",
        ]
        candles = self._stored()
        self.assertEqual(
            [ts for ts, row in candles.items() if row["close"] != -1],
            expected_rewritten,
        )
        smas = self._stored("market_sma")
        self.assertEqual(
            [ts for ts, row in smas.items() if row["sma_200"] != -1],
            expected_rewritten,
        )
        self.assertEqual(len(candles), 375 * 3 + 48)  # Fri, Mon, Tue + 09:15..10:02 today

    def test_incremental_values_match_a_full_recompute(self):
        self._run(FIRST_RUN)
        _, incremental_df = self._run(SECOND_RUN)

        full_db = os.path.join(os.path.dirname(self.db_path), "full.db")
        create_tables(full_db)
        data_fetcher.reset_candle_windows()
        _, full_df = self._run(SECOND_RUN, db_path=full_db)

        self.assertEqual(len(self.kite.historical_calls), 3)
        self.assertEqual(self.kite.historical_calls[2][1], datetime(2026, 11, 6, 9, 15))

        self.assertEqual(list(incremental_df.index), list(full_df.index))
        indicator_columns = ["ao_value", "donchian_upper", "donchian_lower", "donchian_mid"]
        for column in ["Open", "High", "Low", "Close"] + indicator_columns:
            self.assertEqual(incremental_df[column].tolist(), full_df[column].tolist(), column)

        incremental, full = self._stored(), self._stored(db_path=full_db)
        self.assertEqual(list(incremental), list(full))
        sl_values = set()
        for timestamp in ("2026-11-11 10:00:00", "2026-11-11 10:01:00", "2026-11-11 10:02:00"):
            for column in ["open", "high", "low", "close", "SL", "SH"] + indicator_columns:
                self.assertEqual(incremental[timestamp][column], full[timestamp][column], (timestamp, column))
            self.assertIsNotNone(incremental[timestamp]["ao_value"])
            sl_values.add(incremental[timestamp]["SL"])
        self.assertNotIn(None, sl_values)

        incremental_sma = self._stored("market_sma")
        full_sma = self._stored("market_sma", db_path=full_db)
        for timestamp in ("2026-11-11 10:00:00", "2026-11-11 10:01:00", "2026-11-11 10:02:00"):
            self.assertEqual(incremental_sma[timestamp], full_sma[timestamp])

    def test_fetched_candles_are_readable_through_fetch_market_data(self):
        self._run(FIRST_RUN)
        self._run(SECOND_RUN)

        latest = fetch_market_data(symbol="NIFTY50", interval="1m", limit=3, db_path=self.db_path)

        self.assertEqual(
            [ts.strftime("%H:%M") for ts in latest.index],
            ["10:00", "10:01", "10:02"],
        )

    def test_no_csv_is_written_unless_enabled(self):
        results, _ = self._run(FIRST_RUN)

        self.assertFalse(os.path.exists(self.data_dir))
        self.assertIsNone(results[0][2])

    def test_csv_is_written_when_enabled(self):
        with patch.dict("utils.data_fetcher.CONFIG", {"SAVE_CANDLE_CSV": True}):
            results, _ = self._run(FIRST_RUN)

        csv_path = results[0][2]
        self.assertTrue(os.path.isfile(csv_path))
        self.assertTrue(str(csv_path).startswith(self.data_dir))

    def test_failed_incremental_fetch_keeps_history_for_the_next_run(self):
        self._run(FIRST_RUN)
        with patch.object(self.kite, "historical_data", side_effect=RuntimeError("timeout")):
            _, df = self._run(SECOND_RUN)
        self.assertIsNone(df)

        third_run = datetime(2026, 11, 11, 10, 3, 5)
        self._run(third_run)

        self.assertEqual(self.kite.historical_calls[-1][1], datetime(2026, 11, 11, 10, 0))
        self.assertIn("2026-11-11 10:03:00", self._stored())


if __name__ == "__main__":
    unittest.main()
