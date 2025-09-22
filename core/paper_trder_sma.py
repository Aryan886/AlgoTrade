import pandas as pd 
from datetime import datetime 
import time
from utils.utility import setup_paper_trading_logger
from utils.db_func import fetch_latest_delta_data, fetch_vix_data
import os
import json
from core.sma_stra import sma_strategy, get_current_entry_criteria  # Import SMA strategy
import uuid
from typing import Dict, List, Optional

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Initialize loggers
paper_logger, trade_logger, position_logger, sma_logger = setup_paper_trading_logger()

class PaperTraderSMA:
    def __init__(self, symbol="NIFTY50"):
        self.symbol = symbol
        self.position = None
        self.adjustment_history = []
        self.adjustment_impact = 0
        self.entry_criteria = None
        self.last_risk_check = None
        self.last_profit_check = None
        self.entry_time = None
        self.last_data_refresh = None
        self.data_freshness_threshold = 60  # seconds
        
        self.last_signal_attempt = None
        self.signal_cooldown = 300  # 5 minutes in seconds
        
        # Loggers
        paper_logger, trade_logger, position_logger, sma_logger = setup_paper_trading_logger()
        self.logger = paper_logger
        self.trade_logger = trade_logger
        self.position_logger = position_logger
        self.sma_logger = sma_logger  # SMA specific logger

        # JSON file for SMA positions (separate from Donchian)
        self.active_position_file = "active_position_sma.json"

        # Load up position if available
        self.load_position()

    def save_position(self):
        """Save current position and related data to JSON file"""
        if not self.position:
            # Clear active_position_sma.json safely
            try:
                open(self.active_position_file, 'w').close()
            except Exception as e:
                self.logger.exception("Failed to clear active position file: %s", e)
            return
        
        try:
            position_data = {
                'position': self.position,
                'adjustment_history': self.adjustment_history,
                'adjustment_cost': self.adjustment_impact,
                'entry_criteria': self.entry_criteria,
                'entry_time': self.entry_time.isoformat() if self.entry_time else None,
                'last_risk_check': self.last_risk_check.isoformat() if self.last_risk_check else None,
                'last_profit_check': self.last_profit_check.isoformat() if self.last_profit_check else None,
                'last_signal_attempt': self.last_signal_attempt.isoformat() if self.last_signal_attempt else None,
                'symbol': self.symbol,
                'strategy_type': 'sma_spread'
            }
            
            with open(self.active_position_file, 'w') as f:
                json.dump(position_data, f, indent=2, default=str)
            
            self.logger.debug("SMA position saved to active_position_sma.json")
            
        except Exception as e:
            self.logger.error(f"Error saving SMA position: {e}")

    def load_position(self):
        """Load position from JSON file if it exists"""
        try:
            if os.path.exists(self.active_position_file):
                with open(self.active_position_file, 'r') as f:
                    data = json.load(f)
                
                # Restore position data
                self.position = data.get('position')
                self.adjustment_history = data.get('adjustment_history', [])
                self.adjustment_impact = data.get('adjustment_cost', 0)
                self.entry_criteria = data.get('entry_criteria')
                
                # Restore datetime objects
                if data.get('entry_time'):
                    self.entry_time = datetime.fromisoformat(data['entry_time'])
                
                if data.get('last_risk_check'):
                    self.last_risk_check = datetime.fromisoformat(data['last_risk_check'])
                
                if data.get('last_profit_check'):
                    self.last_profit_check = datetime.fromisoformat(data['last_profit_check'])
                
                if data.get('last_signal_attempt'):
                    self.last_signal_attempt = datetime.fromisoformat(data['last_signal_attempt'])
                
                # Log restoration details
                if self.has_active_position():
                    self.logger.info("SMA POSITION RESTORED FROM FILE:")
                    buy_ce_sym = self.position.get('buy_ce_symbol')
                    sell_ce_sym = self.position.get('sell_ce_symbol')
                    buy_ce_price = self.position.get('current_prices', {}).get('buy_ce')
                    sell_ce_price = self.position.get('current_prices', {}).get('sell_ce')
                    self.logger.info(f"  BUY CE: {buy_ce_sym} at {buy_ce_price}")
                    self.logger.info(f"  SELL CE: {sell_ce_sym} at {sell_ce_price}")
                    self.logger.info(f"  Entry Time: {self.entry_time}")
                    self.logger.info(f"  Adjustment Cost: {self.adjustment_impact}")
                return True
                
        except Exception as e:
            self.logger.error(f"Error loading SMA position: {e}")
        
        return False

    def clear_position_file(self):
        """Remove the active position file when position is closed"""
        try:
            if os.path.exists(self.active_position_file):
                os.remove(self.active_position_file)
                self.logger.debug("SMA position file cleared")
        except Exception as e:
            self.logger.error(f"Error clearing SMA position file: {e}")

    def should_attempt_new_signal(self):
        """Check if we should attempt to generate a new signal (with cooldown)"""
        if self.has_active_position():
            return False
        
        current_time = datetime.now()
        
        if self.last_signal_attempt is None:
            return True
        
        time_since_last = (current_time - self.last_signal_attempt).total_seconds()
        
        if time_since_last < self.signal_cooldown:
            self.logger.debug(f"Signal cooldown active: {self.signal_cooldown - time_since_last:.0f}s remaining")
            return False
        
        return True

    def refresh_all_data(self):
        """Centralized data refresh with validation"""
        try:
            # Fetch all required data
            options_data = fetch_latest_delta_data(self.symbol)
            self.last_data_refresh = datetime.now()
            self.logger.debug(f"Data refreshed at {self.last_data_refresh}")
            return bool(options_data)
            
        except Exception as e:
            self.logger.error(f"Data refresh failed: {e}")
            return False

    def main_trading_loop(self):
        """Run continuously in the background during Trading hours"""
        self.logger.info("Starting SMA paper trading.....")

        while self.is_market_open():
            try:
                current_time = datetime.now()

                # Always try to refresh data first
                data_refreshed = self.refresh_all_data()
                
                if not data_refreshed:
                    self.logger.warning("Data refresh failed, waiting before retry")
                    time.sleep(10)
                    continue

                # Only make trading decisions with fresh data
                if not self.has_active_position() and self.should_attempt_new_signal():
                    self.last_signal_attempt = datetime.now()

                    signal = sma_strategy(self.symbol)
                    self.logger.debug(f"SMA strategy returned signal: {bool(signal)}")
                    
                    if signal:
                        success = self.execute_paper_trade(signal)
                        if success:
                            self.logger.info("SMA trade executed successfully")
                        else:
                            self.logger.warning("SMA trade execution failed")
                    else:
                        self.logger.debug("No SMA signal generated")
                
                # Manage existing position
                if self.has_active_position():
                    self.manage_existing_positions()
                
                # Adaptive sleep based on market conditions
                sleep_time = 30 if self.has_active_position() else 60
                time.sleep(sleep_time)

            except Exception as e:
                self.logger.error(f"Encountered error in SMA trading loop: {e}")
                time.sleep(30)

    def is_market_open(self):
        """Check if market is open (9:15 to 3:30)"""
        now = datetime.now()
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close
    
    def has_active_position(self):
        """Check for active position"""
        return self.position is not None
    
    def should_check_risk(self, current_time):
        """Check if it's time for risk check (every 5 min)"""
        if not self.last_risk_check:
            return True
        return (current_time - self.last_risk_check).total_seconds() > 300
    
    def should_check_profit(self, current_time):
        """Check for profit (every 5min)"""
        if not self.last_profit_check:
            return True
        return (current_time - self.last_profit_check).total_seconds() > 300

    def calculate_spread_pnl(self):
        """Calculate current P&L for spread position (Buy CE - Sell CE)"""
        if not self.has_active_position():
            return 0 
        
        try:
            # For spread: P&L = (Current_Buy_CE - Entry_Buy_CE) - (Current_Sell_CE - Entry_Sell_CE)
            buy_ce_entry = self.position['entry_prices'].get('buy_ce', 0)
            sell_ce_entry = self.position['entry_prices'].get('sell_ce', 0)
            
            buy_ce_current = self.position['current_prices'].get('buy_ce', 0)
            sell_ce_current = self.position['current_prices'].get('sell_ce', 0)
            
            # Buy leg P&L (positive when option price increases)
            buy_pnl = buy_ce_current - buy_ce_entry
            
            # Sell leg P&L (positive when option price decreases)  
            sell_pnl = sell_ce_entry - sell_ce_current
            
            # Total spread P&L
            total_pnl = buy_pnl + sell_pnl
            
            self.logger.debug(f"Buy CE P&L: {buy_pnl}, Sell CE P&L: {sell_pnl}, Total P&L: {total_pnl}")
            
            return total_pnl
            
        except Exception as e:
            self.logger.error(f"Error calculating spread P&L: {e}")
            return 0

    def check_stop_loss(self):
        """Check if sell CE has dropped by 20 points (stop loss condition)"""
        if not self.has_active_position():
            return False
        
        try:
            sell_ce_entry = self.position['entry_prices'].get('sell_ce', 0)
            sell_ce_current = self.position['current_prices'].get('sell_ce', 0)
            
            # Check if sell option has dropped by 20 points
            price_drop = sell_ce_entry - sell_ce_current
            
            if price_drop >= 20:
                self.logger.info(f"STOP LOSS TRIGGERED: Sell CE dropped by {price_drop} points (>= 20)")
                self.close_position("all")
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking stop loss: {e}")
            return False

    def check_entry_criteria_violation(self):
        """Check if entry criteria is still valid"""
        try:
            current_criteria = get_current_entry_criteria(self.symbol)

            # Compare with stored entry criteria
            if self.entry_criteria and current_criteria:
                for key in self.entry_criteria:
                    if self.entry_criteria[key] != current_criteria.get(key):
                        self.logger.info(f"ENTRY CRITERIA VIOLATION: {key} changed from {self.entry_criteria[key]} to {current_criteria.get(key)}")
                        self.close_position("all")
                        return True
                    
            return False
        except Exception as e:
            self.logger.error(f"Error checking entry criteria: {e}")
            return False

    def close_position(self, leg: str, close_price: Optional[float] = None) -> bool:
        """Close position (all legs for spread strategy)"""
        if not self.has_active_position():
            self.logger.warning("close_position called but no active position.")
            return False

        try:
            # Calculate final P&L
            final_pnl = self.calculate_spread_pnl()
            
            self.logger.info("CLOSING SMA SPREAD POSITION:")
            self.logger.info(f"Final Spread P&L: {final_pnl}")
            
            self.trade_logger.info("CLOSING SMA SPREAD POSITION:")
            self.trade_logger.info(f"Final Spread P&L: {final_pnl}")

            # Clear position
            self.position = None
            self.adjustment_impact = 0
            self.adjustment_history = []
            self.clear_position_file()
            
            self.logger.info("SMA spread position closed successfully")
            return True

        except Exception as e:
            self.logger.exception("Error closing SMA position: %s", e)
            return False

    def update_current_prices(self, options_data: Optional[List[Dict]] = None) -> bool:
        """Update current prices for spread position"""
        try:
            if not self.has_active_position():
                return False
            
            buy_ce_sym = self.position.get("buy_ce_symbol")
            sell_ce_sym = self.position.get("sell_ce_symbol")
            
            if options_data is None:
                options_data = fetch_latest_delta_data(self.symbol)
            if not options_data:
                return False
            
            symbol_map = {o.get("tradingsymbol"): o for o in options_data if o.get("tradingsymbol")}
            
            if not buy_ce_sym or not sell_ce_sym:
                self.logger.warning("Position missing option symbols; cannot update.")
                return False

            updated = False

            # Update Buy CE price
            buy_ce_data = symbol_map.get(buy_ce_sym)
            if buy_ce_data:
                new_price = buy_ce_data.get("last_price") or buy_ce_data.get("ltp", 0)
                if new_price:
                    self.position.setdefault("current_prices", {})["buy_ce"] = float(new_price)
                    updated = True

            # Update Sell CE price
            sell_ce_data = symbol_map.get(sell_ce_sym)
            if sell_ce_data:
                new_price = sell_ce_data.get("last_price") or sell_ce_data.get("ltp", 0)
                if new_price:
                    self.position.setdefault("current_prices", {})["sell_ce"] = float(new_price)
                    updated = True

            if updated:
                self.save_position()
                self.logger.debug("SMA position prices updated.")
                
            return updated

        except Exception as e:
            self.logger.exception("Error updating SMA current prices: %s", e)
            return False

    def manage_existing_positions(self):
        """Manage existing SMA spread positions"""
        current_time = datetime.now()
        self.logger.info("----- SMA Position management cycle started at %s -----", current_time.strftime("%H:%M:%S"))

        # Refresh market data 
        data_refreshed = self.refresh_all_data()
        if not data_refreshed:
            self.logger.warning("Skipping SMA position management - failed to refresh data")
            return

        # Update current prices
        options_data = fetch_latest_delta_data(self.symbol)
        if not options_data:
            self.logger.warning("No options data available for SMA price updates")
            return

        prices_updated = self.update_current_prices(options_data)
        current_pnl = self.calculate_spread_pnl()
        self.logger.info(f"CURRENT SMA SPREAD P&L: {current_pnl} points")

        if not prices_updated:
            self.logger.warning("Skipping SMA position management - stale/no price data")
            return

        # Risk management checks every 5 min
        if self.should_check_risk(current_time):
            # Check stop loss (20 point drop in sell CE)
            if self.check_stop_loss():
                return  # Position closed
            
            # Check entry criteria violation
            if self.check_entry_criteria_violation():
                return  # Position closed
            
            self.last_risk_check = current_time
            
        # Log current status
        if self.has_active_position():
            buy_ce_sym = self.position.get("buy_ce_symbol")
            sell_ce_sym = self.position.get("sell_ce_symbol")
            buy_ce_price = self.position['current_prices'].get('buy_ce', 0)
            sell_ce_price = self.position['current_prices'].get('sell_ce', 0)
            
            self.logger.info(
                f"[SMA CYCLE SUMMARY] Spread P&L: {current_pnl:.2f} | "
                f"BUY CE: {buy_ce_sym} @ {buy_ce_price} | "
                f"SELL CE: {sell_ce_sym} @ {sell_ce_price}"
            )
        else:
            self.logger.info("[SMA CYCLE SUMMARY] No active position")

    def execute_paper_trade(self, signal: Dict) -> bool:
        """
        Create and persist a new SMA spread position (Buy ITM CE + Sell ATM CE)
        """
        if self.has_active_position():
            self.logger.warning("execute_paper_trade called but an active SMA position already exists. Ignoring new signal.")
            return False

        try:
            # Extract options from signal (reusing ce_option and pe_option keys)
            buy_ce_opt = signal.get("ce_option")  # ITM CE to buy
            sell_ce_opt = signal.get("pe_option")  # ATM CE to sell

            # Validate minimal fields
            if not buy_ce_opt or not sell_ce_opt:
                self.logger.error("SMA Signal missing option dicts.")
                return False

            buy_ce_sym = buy_ce_opt.get("tradingsymbol")
            sell_ce_sym = sell_ce_opt.get("tradingsymbol")
            buy_ce_price = buy_ce_opt.get("last_price")
            sell_ce_price = sell_ce_opt.get("last_price")

            if not buy_ce_sym or not sell_ce_sym or buy_ce_price is None or sell_ce_price is None:
                self.logger.error("SMA Signal options missing required fields.")
                return False

            now_iso = datetime.now().isoformat(sep=" ")

            # Capture entry criteria for later validation
            try:
                self.entry_criteria = get_current_entry_criteria(self.symbol)
            except Exception:
                self.entry_criteria = None

            # Set entry time
            try:
                self.entry_time = datetime.fromisoformat(now_iso)
            except Exception:
                self.entry_time = datetime.now()

            # Create SMA spread position
            self.position = {
                "id": str(uuid.uuid4()),
                "buy_ce_symbol": buy_ce_sym,
                "sell_ce_symbol": sell_ce_sym,
                "entry_prices": {"buy_ce": float(buy_ce_price), "sell_ce": float(sell_ce_price)},
                "current_prices": {"buy_ce": float(buy_ce_price), "sell_ce": float(sell_ce_price)},
                "entry_time": now_iso,
                "strategy_type": "sma_spread",
            }

            # Initialize adjustment containers
            if not hasattr(self, "adjustment_history") or self.adjustment_history is None:
                self.adjustment_history = []

            # Persist position
            self.save_position()

            # Calculate net debit/credit
            net_cost = float(buy_ce_price) - float(sell_ce_price)

            # Log trade execution
            self.logger.info("NEW SMA SPREAD POSITION OPENED:")
            self.logger.info(f"BUY CE (ITM): {buy_ce_sym} at {buy_ce_price}")
            self.logger.info(f"SELL CE (ATM): {sell_ce_sym} at {sell_ce_price}")
            self.logger.info(f"Net Cost: {net_cost} ({('Debit' if net_cost > 0 else 'Credit')})")
            self.logger.info(f"Position opened at: {now_iso}")
            self.logger.info("SMA TRADE EXECUTION SUCCESSFUL")

            self.trade_logger.info("NEW SMA SPREAD POSITION OPENED:")
            self.trade_logger.info(f"BUY CE (ITM): {buy_ce_sym} at {buy_ce_price}")
            self.trade_logger.info(f"SELL CE (ATM): {sell_ce_sym} at {sell_ce_price}")
            self.trade_logger.info(f"Net Cost: {net_cost}")
            self.trade_logger.info("SMA TRADE EXECUTION SUCCESSFUL")

            return True

        except Exception as e:
            self.logger.exception("Failed to execute SMA paper trade: %s", e)
            return False

    def get_position_status(self):
        """Get current SMA position status for monitoring"""
        if not self.has_active_position():
            return "No active SMA position"
        
        try:
            current_pnl = self.calculate_spread_pnl()
            return {
                'position_active': True,
                'strategy_type': 'sma_spread',
                'entry_time': self.entry_time,
                'current_pnl': current_pnl,
                'buy_ce_price': self.position['current_prices'].get('buy_ce'),
                'sell_ce_price': self.position['current_prices'].get('sell_ce'),
                'last_data_refresh': self.last_data_refresh,
            }
        except Exception as e:
            return f"Error getting SMA status: {e}"

if __name__ == "__main__":
    trader = PaperTraderSMA("NIFTY50")
    print("SMA Paper trader initialized....")
    trader.main_trading_loop()  # This runs continuously
                