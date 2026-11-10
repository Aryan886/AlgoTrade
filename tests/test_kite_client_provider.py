import os
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import broker.zerodha_client as zerodha_client


class _FakeKiteConnect:
    """Stands in for the third-party KiteConnect class."""

    def __init__(self, api_key):
        self.api_key = api_key
        self.access_token = None

    def set_access_token(self, token):
        self.access_token = token


class _CountingKite:
    def __init__(self):
        self.instrument_calls = []

    def instruments(self, exchange):
        self.instrument_calls.append(exchange)
        return [{"exchange": exchange, "tradingsymbol": f"{exchange}-A"}]


class KiteClientProviderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.token_file = os.path.join(self._tmp.name, "access_token.txt")
        self._write_token("token-monday")

        patches = [
            patch.object(zerodha_client, "ACCESS_TOKEN_FILE", self.token_file),
            patch.object(zerodha_client, "KiteConnect", _FakeKiteConnect),
            patch.object(zerodha_client, "load_config", return_value={"api_key": "key"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        zerodha_client.reset_kite_cache()
        self.addCleanup(zerodha_client.reset_kite_cache)

    def _write_token(self, token):
        with open(self.token_file, "w") as f:
            f.write(token)

    def test_repeated_calls_return_the_same_client(self):
        first = zerodha_client.kite_from_saved_token()
        second = zerodha_client.kite_from_saved_token()

        self.assertIsNotNone(first)
        self.assertIs(first, second)
        self.assertEqual(first.access_token, "token-monday")

    def test_new_login_token_produces_a_new_client(self):
        first = zerodha_client.kite_from_saved_token()
        self._write_token("token-tuesday")

        second = zerodha_client.kite_from_saved_token()

        self.assertIsNot(first, second)
        self.assertEqual(second.access_token, "token-tuesday")

    def test_missing_token_file_returns_none_and_recovers_after_login(self):
        os.remove(self.token_file)
        self.assertIsNone(zerodha_client.kite_from_saved_token())

        self._write_token("token-after-login")

        self.assertEqual(zerodha_client.kite_from_saved_token().access_token, "token-after-login")


class InstrumentCacheTests(unittest.TestCase):
    def test_instrument_list_is_downloaded_once_per_exchange_per_day(self):
        kite = _CountingKite()
        monday = date(2026, 7, 6)

        first = zerodha_client.get_instruments(kite, "NFO", today=monday)
        second = zerodha_client.get_instruments(kite, "NFO", today=monday)
        zerodha_client.get_instruments(kite, "NSE", today=monday)

        self.assertEqual(kite.instrument_calls, ["NFO", "NSE"])
        self.assertEqual(first, [{"exchange": "NFO", "tradingsymbol": "NFO-A"}])
        self.assertEqual(second, first)

    def test_instrument_list_is_refreshed_on_a_new_day(self):
        kite = _CountingKite()

        zerodha_client.get_instruments(kite, "NFO", today=date(2026, 7, 6))
        zerodha_client.get_instruments(kite, "NFO", today=date(2026, 7, 7))

        self.assertEqual(kite.instrument_calls, ["NFO", "NFO"])

    def test_each_client_has_its_own_instrument_list(self):
        kite_a, kite_b = _CountingKite(), _CountingKite()
        day = date(2026, 7, 6)

        zerodha_client.get_instruments(kite_a, "NFO", today=day)
        zerodha_client.get_instruments(kite_b, "NFO", today=day)

        self.assertEqual(kite_a.instrument_calls, ["NFO"])
        self.assertEqual(kite_b.instrument_calls, ["NFO"])

    def test_callers_cannot_corrupt_the_cached_list(self):
        kite = _CountingKite()
        day = date(2026, 7, 6)

        zerodha_client.get_instruments(kite, "NFO", today=day).clear()

        self.assertEqual(len(zerodha_client.get_instruments(kite, "NFO", today=day)), 1)

    def test_failed_download_is_not_cached(self):
        class _FlakyKite(_CountingKite):
            def instruments(self, exchange):
                self.instrument_calls.append(exchange)
                if len(self.instrument_calls) == 1:
                    raise RuntimeError("network down")
                return [{"exchange": exchange}]

        kite = _FlakyKite()
        day = date(2026, 7, 6)

        with self.assertRaises(RuntimeError):
            zerodha_client.get_instruments(kite, "NFO", today=day)

        self.assertEqual(zerodha_client.get_instruments(kite, "NFO", today=day), [{"exchange": "NFO"}])


if __name__ == "__main__":
    unittest.main()
