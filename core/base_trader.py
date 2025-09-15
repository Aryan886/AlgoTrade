import os
import json
import uuid
from datetime import datetime
import time
from typing import Optional, Dict, Any

from utils.db_func import fetch_latest_delta_data
from utils.utility import setup_paper_trading_logger

#create logs directory if not exists
if not os.path.exists("logs"):
    os.makedirs("logs")

class BaseTrader:
    def __init__(self, symbol="NIFTY50", strategy_func=None, position_file="active_position.json"):
        self.symbol = symbol
        self.strategy_func = strategy_func
        self.active_position_file = position_file

        # state
        self.position = None
        self.adjustment_history = []
        self.adjustment_cost = 0
        self.entry_time = None
        self.last_signal_attempt = None
        self.signal_cooldown = 300  # 5 min
        self.last_data_refresh = None

        # loggers
        self.logger, self.trade_logger, self.position_logger = setup_paper_trading_logger()

        # restore old position if exists
        self.load_position()

    #-----Position Management Methods-----#
    def save_position(self):
        if not self.position:
            try:
                open(self.active_position_file, 'w').close()  # Clear file if no position
            except Exception as e:
                self.logger.error(f"Error clearing position file: {e}")
            return
        
        try:
            data = {
                "position": self.position,
                "adjustment_history": self.adjustment_history,
                "adjustment_cost": self.adjustment_cost,
                "entry_time": self.entry_time.isoformat() if self.entry_time else None,
                "last_signal_attempt": self.last_signal_attempt.isoformat() if self.last_signal_attempt else None,
                "symbol": self.symbol,
            }
            with open(self.active_position_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            self.logger.info(f"Position saved: {self.active_position_file}")
        
        except Exception as e:
            self.logger.error(f"Error saving position: {e}")

    def load_position(self):
        try:
            if os.path.exists(self.active_position_file):
                with open(self.active_position_file, "r") as f:
                    data = json.load(f)
                self.position = data.get("position")
                self.adjustment_history = data.get("adjustment_history", [])
                self.adjustment_cost = data.get("adjustment_cost", 0)
                if data.get("entry_time"):
                    self.entry_time = datetime.fromisoformat(data["entry_time"])
                if data.get("last_signal_attempt"):
                    self.last_signal_attempt = datetime.fromisoformat(data["last_signal_attempt"])
                self.logger.info(f"Restored position from {self.active_position_file}")
        except Exception as e:
            self.logger.error(f"Error loading position: {e}")

    def has_active_position(self):
        return self.position is not None

    def should_attempt_new_signal(self):
        if self.has_active_position():
            return False
        now = datetime.now()
        if not self.last_signal_attempt:
            return True
        return (now - self.last_signal_attempt).total_seconds() >= self.signal_cooldown

        
    #----Trade Execution Methods----#
    def execute_paper_trade(self, signal:Dict) -> bool:
        if self.has_active_position():
            self.logger.warning("Active position exists.")
            return False

        try:
            ce_opt = signal.get("ce_option")
            pe_opt = signal.get("pe_option")
            if not ce_opt or not pe_opt:
                self.logger.error("Invalid signal data.")
                return False

            ce_sym, ce_price = ce_opt["tradingsymbol"], ce_opt["last_price"]
            pe_sym, pe_price = pe_opt["tradingsymbol"], pe_opt["last_price"]

            now_iso = datetime.now().isoformat()
            self.position = {
                "id": str(uuid.uuid4()),
                "ce_symbol": ce_sym,
                "pe_symbol": pe_sym,
                "entry_time": now_iso,
                "entry_prices": {
                    "ce_price": ce_price,
                    "pe_price": pe_price
                },
                "ce_closed": False,
                "pe_closed": False,
            }
            self.entry_time = datetime.now()
            self.save_position()

            #main loggin
            self.logger.info(f"NEW POSITION: {self.position}")
            self.logger.info(f"  CE: {ce_sym} @ {ce_price}")
            self.logger.info(f"  PE: {pe_sym} @ {pe_price}")

            #trade specific logging
            self.trade_logger.info(f"NEW POSITION: {self.position}")
            self.trade_logger.info(f"  CE: {ce_sym} @ {ce_price}")
            self.trade_logger.info(f"  PE: {pe_sym} @ {pe_price}")
            return True
        
        except Exception as e:
            self.logger.error(f"Error executing paper trade: {e}")
            return False
        
    #-----Main Trading Loop-----#
    def main_trading_loop(self):
        self.logger.info(f"Starting trader for {self.strategy_func.__name__}...")
        while True:
            try:
                if not self.has_active_position() and self.should_attempt_new_signal():
                    self.last_signal_attempt = datetime.now()
                    signal = self.strategy_func(self.symbol) if self.strategy_func else None
                    if signal:
                        success = self.execute_paper_trade(signal)
                        if success:
                            self.logger.info("Trade executed successfully")
                time.sleep(60)  # wait 1 min before next check
            except Exception as e:
                self.logger.error(f"Trader loop error: {e}")
                time.sleep(30)