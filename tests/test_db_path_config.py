import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config import resolve_db_path


class ResolveDbPathTests(unittest.TestCase):
    def test_defaults_to_original_database_file(self):
        self.assertEqual(resolve_db_path(env={}), "db/trading_bot.db")

    def test_environment_variable_overrides_default(self):
        env = {"ALGOTRADE_DB_PATH": "db/db2/trading_bot.db"}

        self.assertEqual(resolve_db_path(env=env), "db/db2/trading_bot.db")

    def test_blank_environment_variable_is_ignored(self):
        self.assertEqual(resolve_db_path(env={"ALGOTRADE_DB_PATH": "  "}), "db/trading_bot.db")

    def test_live_modules_share_the_configured_path(self):
        from config.config import DB_PATH
        import utils.data_fetcher
        import utils.db_func
        import utils.db_setup
        import utils.vix_fetcher
        import inspect

        self.assertEqual(utils.db_func.DB_PATH, DB_PATH)
        self.assertEqual(utils.data_fetcher.DB_PATH, DB_PATH)
        for func in (
            utils.db_func.store_market_data,
            utils.vix_fetcher.store_vix_data,
            utils.db_setup.create_tables,
            utils.db_setup.prepare_database,
        ):
            default = inspect.signature(func).parameters["db_path"].default
            self.assertEqual(default, DB_PATH, func.__name__)


if __name__ == "__main__":
    unittest.main()
