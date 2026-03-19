"""
Equity Paper Trading Bot - Donchian + AO + VWAP + SMA Strategy
Following the structure of paper_trades.py
"""

import pandas as pd
from datetime import datetime
import time
from utils.utility import setup_paper_trading_logger
import os
import json
from core.equity_strat import (
    equity_strategy,
    get_current_price,
    check_ao_red_candles,
    get_5sma_low,
    calculate_skipping_low_stop_loss
)
import uuid

# Initialize loggers
paper_logger, trade_logger, position_logger, sma_logger, equity_logger, _nifty_logger = setup_paper_trading_logger()


class EquityPaperTrader:
    def __init__(self, symbol="INFY", quantity=1):
        self.symbol = symbol
        self.quantity = quantity
        self.position = None
        self.entry_time = None
        self.last_data_refresh = None
        self.data_freshness_threshold = 60  # seconds
        
        self.last_signal_attempt = None
        self.signal_cooldown = 300  # 5 minutes in seconds
        
        # Loggers
        paper_logger, trade_logger, position_logger, sma_logger, equity_logger, _nifty_logger = setup_paper_trading_logger()
        self.logger = equity_logger
        
        # JSON file
        self.active_position_file = "active_equity_position.json"
        
        # Load up position if available
        self.load_position()
    
    def save_position(self):
        """Save current position and related data to JSON file"""
        if not self.position:
            # Remove/clear active_equity_position.json safely
            try:
                open(self.active_position_file, 'w').close()
            except Exception as e:
                self.logger.exception("Failed to clear active position file: %s", e)
            return
        
        try:
            position_data = {
                'position': self.position,
                'entry_time': self.entry_time.isoformat() if self.entry_time else None,
                'last_signal_attempt': self.last_signal_attempt.isoformat() if self.last_signal_attempt else None,
                'symbol': self.symbol,
                'quantity': self.quantity
            }
            
            with open(self.active_position_file, 'w') as f:
                json.dump(position_data, f, indent=2, default=str)
            
            self.logger.debug("Position saved to active_equity_position.json")
            
        except Exception as e:
            self.logger.error(f"Error saving position: {e}")
    
    def load_position(self):
        """Load position from JSON file if it exists"""
        try:
            if os.path.exists(self.active_position_file):
                with open(self.active_position_file, 'r') as f:
                    data = json.load(f)
                
                # Restore position data
                self.position = data.get('position')
                self.quantity = data.get('quantity', 1)
                
                # Restore datetime objects
                if data.get('entry_time'):
                    self.entry_time = datetime.fromisoformat(data['entry_time'])
                
                if data.get('last_signal_attempt'):
                    self.last_signal_attempt = datetime.fromisoformat(data['last_signal_attempt'])
                
                # Log restoration details
                if self.has_active_position():
                    self.logger.info("POSITION RESTORED FROM FILE:")
                    self.logger.info(f"  Symbol: {self.position.get('symbol')}")
                    self.logger.info(f"  Entry Price: {self.position.get('entry_price')}")
                    self.logger.info(f"  Stop Loss: {self.position.get('stop_loss')}")
                    self.logger.info(f"  Quantity: {self.quantity}")
                    self.logger.info(f"  Entry Time: {self.entry_time}")
                return True
                
        except Exception as e:
            self.logger.error(f"Error loading position: {e}")
        
        return False
    
    def clear_position_file(self):
        """Remove the active position file when position is closed"""
        try:
            if os.path.exists(self.active_position_file):
                os.remove(self.active_position_file)
                self.logger.debug("Position file cleared")
        except Exception as e:
            self.logger.error(f"Error clearing position file: {e}")
    
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
    
    def is_data_fresh(self, timestamp, max_age_seconds=30):
        """Check if data is fresh enough for trading decisions"""
        if not timestamp:
            return False
        
        current_time = datetime.now()
        data_age = (current_time - timestamp).total_seconds()
        return data_age <= max_age_seconds
    
    def refresh_all_data(self):
        """Centralized data refresh with validation"""
        try:
            # Update last refresh time
            self.last_data_refresh = datetime.now()
            self.logger.debug(f"Data refreshed at {self.last_data_refresh}")
            return True
            
        except Exception as e:
            self.logger.error(f"Data refresh failed: {e}")
            return False
    
    def main_trading_loop(self):
        """Run continuously in the background during trading hours"""
        self.logger.info("Starting equity paper trading.....")
        
        while self.is_market_open():
            self.logger.info(f"=== TRADING LOOP CYCLE STARTED at {datetime.now().strftime('%H:%M:%S')} ===")
            try:
                current_time = datetime.now()
                
                # Always try to refresh data first
                data_refreshed = self.refresh_all_data()
                
                if current_time.hour == 15 and current_time.minute >= 25:
                    if self.has_active_position():
                        self.logger.info("Market closing soon, closing active position")
                        self.close_position(reason="Market Close")
                    else:
                        self.logger.info("Market closing soon, no active position to close")
                    break  # Exit loop after handling close

                if not data_refreshed:
                    self.logger.warning("Data refresh failed, waiting before retry")
                    time.sleep(10)
                    continue
                
                # Only make trading decisions with fresh data
                if not self.has_active_position() and self.should_attempt_new_signal():
                    self.last_signal_attempt = datetime.now()
                    
                    signal = equity_strategy(self.symbol)
                    self.logger.debug(f"Strategy returned signal: {bool(signal)}")
                    
                    if signal:
                        success = self.execute_paper_trade(signal)
                        if success:
                            self.logger.info("Trade executed successfully")
                        else:
                            self.logger.warning("Trade execution failed")
                    else:
                        self.logger.debug("No signal generated (conditions not met)")
                
                # Manage existing position
                if self.has_active_position():
                    self.logger.info(f"=== MANAGING ACTIVE POSITION ===")
                    self.manage_existing_position()
                else:
                    # Log when no position is active
                    current_price = get_current_price(self.symbol)
                    if current_price:
                        self.logger.info(f"[NO POSITION] {self.symbol} @ {current_price:.2f} - Waiting for signal")
                    else:
                        self.logger.warning(f"[NO POSITION] {self.symbol} - Could not get current price")
                
                # Adaptive sleep - check every 1 minute
                sleep_time = 60
                time.sleep(sleep_time)
            
            except Exception as e:
                self.logger.error(f"Encountered this error: {e}")
                time.sleep(30)  # Shorter retry interval
    
    def is_market_open(self):
        """Check if market is open (9:15 to 3:30)"""
        now = datetime.now()
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close
    
    def has_active_position(self):
        """Check for active position"""
        return self.position is not None
    
    def close_position(self, reason: str = "Manual") -> bool:
        """Close the current position"""
        if not self.has_active_position():
            self.logger.warning("close_position called but no active position.")
            return False
        
        try:
            # Get current price
            current_price = get_current_price(self.symbol)
            if current_price is None:
                self.logger.error("Could not get current price to close position")
                return False
            
            entry_price = self.position.get('entry_price')
            stop_loss = self.position.get('stop_loss')
            pnl = (current_price - entry_price) * self.quantity
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            
            # Calculate additional metrics
            price_movement = current_price - entry_price
            price_movement_pct = (price_movement / entry_price) * 100
            stop_distance = current_price - stop_loss
            stop_distance_pct = (stop_distance / current_price) * 100
            
            # Determine if it was a good or bad trade
            trade_result = "PROFITABLE" if pnl > 0 else "LOSS"
            
            self.logger.info("CLOSING POSITION:")
            self.logger.info(f"  Symbol: {self.symbol}")
            self.logger.info(f"  Entry Price: {entry_price:.2f}")
            self.logger.info(f"  Exit Price: {current_price:.2f}")
            self.logger.info(f"  Stop Loss: {stop_loss:.2f}")
            self.logger.info(f"  Quantity: {self.quantity}")
            self.logger.info(f"  Price Movement: {price_movement:.2f} ({price_movement_pct:.2f}%)")
            self.logger.info(f"  Final P&L: {pnl:.2f} ({pnl_pct:.2f}%)")
            self.logger.info(f"  Trade Result: {trade_result}")
            self.logger.info(f"  Reason: {reason}")
            
            # Log partial exit summary if applicable
            if self.position.get('ao_risk_applied', False):
                partial_exit_price = self.position.get('partial_exit_price')
                partial_exit_pnl = self.position.get('partial_exit_pnl')
                total_pnl = partial_exit_pnl + pnl
                self.logger.info(f"  Partial Exit Summary:")
                self.logger.info(f"    50% Exit P&L: {partial_exit_pnl:.2f}")
                self.logger.info(f"    Remaining 50% P&L: {pnl:.2f}")
                self.logger.info(f"    Total P&L: {total_pnl:.2f}")
            
            # Clear position
            self.position = None
            self.clear_position_file()
            
            return True
            
        except Exception as e:
            self.logger.exception("Error closing position: %s", e)
            return False
    
    def check_stop_loss(self):
        """Check if stop loss is hit"""
        if not self.has_active_position():
            return False
        
        try:
            current_price = get_current_price(self.symbol)
            if current_price is None:
                self.logger.warning("Could not get current price for stop loss check")
                return False
            
            stop_loss = self.position.get('stop_loss')
            entry_price = self.position.get('entry_price')
            
            # Calculate distances for logging
            stop_distance = current_price - stop_loss
            stop_distance_pct = (stop_distance / current_price) * 100
            
            # Log stop loss proximity
            if stop_distance_pct < 1.0:
                self.logger.warning(f"STOP LOSS PROXIMITY: Price {current_price:.2f} is {stop_distance_pct:.2f}% above stop {stop_loss:.2f}")
            elif stop_distance_pct < 2.0:
                self.logger.info(f"STOP LOSS WARNING: Price {current_price:.2f} is {stop_distance_pct:.2f}% above stop {stop_loss:.2f}")
            
            if current_price <= stop_loss:
                pnl = (current_price - entry_price) * self.quantity
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                
                self.logger.info("STOP LOSS HIT:")
                self.logger.info(f"  Current Price: {current_price:.2f}")
                self.logger.info(f"  Stop Loss: {stop_loss:.2f}")
                self.logger.info(f"  Entry Price: {entry_price:.2f}")
                self.logger.info(f"  Final P&L: {pnl:.2f} ({pnl_pct:.2f}%)")
                self.logger.info(f"  Quantity: {self.quantity}")
                
                self.close_position(reason="Stop Loss Hit")
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking stop loss: {e}")
            return False
    
    def check_ao_risk_management(self):
        """
        Check if 5 continuous red AO candles
        If yes: Exit 50% at current price, move remaining 50% stop to 5 SMA low with filter
        """
        if not self.has_active_position():
            return False
        
        try:
            # Check if 5 consecutive red AO candles
            if not check_ao_red_candles(self.symbol, count=5):
                return False
            
            self.logger.info("AO RISK MANAGEMENT TRIGGERED: 5 consecutive red AO candles")
            
            # Check if already applied AO risk management
            if self.position.get('ao_risk_applied', False):
                self.logger.debug("AO risk management already applied")
                return False
            
            current_price = get_current_price(self.symbol)
            if current_price is None:
                return False
            
            # Get 5 SMA low with filter
            sma_5_low_stop = get_5sma_low(self.symbol, filter_pct=0.0025)
            if sma_5_low_stop is None:
                self.logger.warning("Could not get 5 SMA low for AO risk management")
                return False
            
            entry_price = self.position.get('entry_price')
            
            # Calculate 50% exit P&L
            half_qty = self.quantity / 2
            half_pnl = (current_price - entry_price) * half_qty
            
            self.logger.info(f"AO RISK MANAGEMENT:")
            self.logger.info(f"  Exiting 50% ({half_qty} qty) at {current_price}")
            self.logger.info(f"  50% Exit P&L: {half_pnl:.2f}")
            self.logger.info(f"  Moving remaining 50% stop to: {sma_5_low_stop}")
            
            
            # Update position
            self.quantity = half_qty
            self.position['stop_loss'] = sma_5_low_stop
            self.position['ao_risk_applied'] = True
            self.position['partial_exit_price'] = current_price
            self.position['partial_exit_pnl'] = half_pnl
            
            self.save_position()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in AO risk management: {e}")
            return False
    
    def update_stop_loss(self):
        """
        Dynamically update stop loss to skipping low
        Recalculate every check
        """
        if not self.has_active_position():
            return
        
        try:
            new_stop_loss = calculate_skipping_low_stop_loss(self.symbol, filter_pct=0.0025)
            
            if new_stop_loss is None:
                self.logger.debug("Could not calculate new stop loss - skipping update")
                return
            
            old_stop_loss = self.position.get('stop_loss')
            current_price = get_current_price(self.symbol)
            
            # Only update if new stop loss is higher (trailing stop)
            if new_stop_loss > old_stop_loss:
                improvement = new_stop_loss - old_stop_loss
                improvement_pct = (improvement / old_stop_loss) * 100
                
                self.logger.info("STOP LOSS UPDATED:")
                self.logger.info(f"  Old Stop: {old_stop_loss:.2f}")
                self.logger.info(f"  New Stop: {new_stop_loss:.2f}")
                self.logger.info(f"  Improvement: +{improvement:.2f} (+{improvement_pct:.2f}%)")
                if current_price:
                    new_distance = current_price - new_stop_loss
                    new_distance_pct = (new_distance / current_price) * 100
                    self.logger.info(f"  New Distance to Stop: {new_distance:.2f} ({new_distance_pct:.2f}%)")
                
                self.position['stop_loss'] = new_stop_loss
                self.save_position()
            else:
                self.logger.debug(f"Stop loss unchanged: {old_stop_loss:.2f} (new: {new_stop_loss:.2f})")
            
        except Exception as e:
            self.logger.error(f"Error updating stop loss: {e}")
    
    def manage_existing_position(self):
        """Manage existing position with all checks"""
        current_time = datetime.now()
        self.logger.info("----- Position management cycle started at %s -----", current_time.strftime("%H:%M:%S"))
        
        # Get current price and log detailed status
        current_price = get_current_price(self.symbol)
        if current_price:
            entry_price = self.position.get('entry_price')
            stop_loss = self.position.get('stop_loss')
            current_pnl = (current_price - entry_price) * self.quantity
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            
            # Calculate distance to stop loss
            stop_distance = current_price - stop_loss
            stop_distance_pct = (stop_distance / current_price) * 100
            
            # Calculate distance from entry
            entry_distance = current_price - entry_price
            entry_distance_pct = (entry_distance / entry_price) * 100
            
            # Determine position status
            position_status = "PROFIT" if current_pnl > 0 else "LOSS"
            risk_status = "HIGH RISK" if stop_distance_pct < 2 else "NORMAL"
            
            self.logger.info(
                f"[CYCLE SUMMARY] {self.symbol} | "
                f"Price: {current_price:.2f} | "
                f"Entry: {entry_price:.2f} | "
                f"Stop: {stop_loss:.2f} | "
                f"P&L: {current_pnl:.2f} ({pnl_pct:.2f}%) | "
                f"Status: {position_status} | "
                f"Risk: {risk_status}"
            )
            
            self.logger.info(
                f"[DETAILED STATUS] "
                f"Distance to Stop: {stop_distance:.2f} ({stop_distance_pct:.2f}%) | "
                f"Distance from Entry: {entry_distance:.2f} ({entry_distance_pct:.2f}%) | "
                f"Quantity: {self.quantity} | "
                f"AO Risk Applied: {self.position.get('ao_risk_applied', False)}"
            )
            
            # Log partial exit info if applicable
            if self.position.get('ao_risk_applied', False):
                partial_exit_price = self.position.get('partial_exit_price')
                partial_exit_pnl = self.position.get('partial_exit_pnl')
                self.logger.info(
                    f"[PARTIAL EXIT INFO] "
                    f"50% Exited at: {partial_exit_price:.2f} | "
                    f"Partial P&L: {partial_exit_pnl:.2f} | "
                    f"Remaining Qty: {self.quantity}"
                )
        else:
            self.logger.warning("Could not get current price for position management")
        
        # Check stop loss first
        if self.check_stop_loss():
            return  # Position closed
        
        # Check AO risk management
        self.check_ao_risk_management()
        
        # Update trailing stop loss
        self.update_stop_loss()
    
    def execute_paper_trade(self, signal: dict) -> bool:
        """
        Create and persist a new position entry
        Returns True on success, False otherwise
        """
        if self.has_active_position():
            self.logger.warning("execute_paper_trade called but an active position already exists. Ignoring new signal.")
            return False
        
        try:
            now_iso = datetime.now().isoformat(sep=" ")
            self.entry_time = datetime.now()
            
            # Create position
            self.position = {
                "id": str(uuid.uuid4()),
                "symbol": signal['symbol'],
                "entry_price": signal['entry_price'],
                "stop_loss": signal['stop_loss'],
                "entry_method": signal['entry_method'],
                "entry_time": now_iso,
                "ao_risk_applied": False,
                "partial_exit_price": None,
                "partial_exit_pnl": None
            }
            
            # Persist
            self.save_position()
            
            # Log entry
            self.logger.info("NEW POSITION OPENED:")
            self.logger.info(f"  Symbol: {self.symbol}")
            self.logger.info(f"  Entry Price: {signal['entry_price']}")
            self.logger.info(f"  Stop Loss: {signal['stop_loss']}")
            self.logger.info(f"  Quantity: {self.quantity}")
            self.logger.info(f"  Entry Method: {signal['entry_method']}")
            self.logger.info(f"  Position opened at: {now_iso}")
            self.logger.info("TRADE EXECUTION SUCCESSFUL")
            
            return True
            
        except Exception as e:
            self.logger.exception("Failed to execute paper trade: %s", e)
            return False
    
    def get_position_status(self):
        """Get current position status for monitoring"""
        if not self.has_active_position():
            return "No active position"
        
        try:
            current_price = get_current_price(self.symbol)
            if current_price:
                entry_price = self.position.get('entry_price')
                current_pnl = (current_price - entry_price) * self.quantity
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
            else:
                current_pnl = 0
                pnl_pct = 0
            
            return {
                'position_active': True,
                'symbol': self.symbol,
                'entry_time': self.entry_time,
                'entry_price': self.position.get('entry_price'),
                'current_price': current_price,
                'stop_loss': self.position.get('stop_loss'),
                'quantity': self.quantity,
                'current_pnl': current_pnl,
                'pnl_pct': pnl_pct,
                'entry_method': self.position.get('entry_method'),
                'ao_risk_applied': self.position.get('ao_risk_applied', False),
                'last_data_refresh': self.last_data_refresh
            }
        except Exception as e:
            return f"Error getting status: {e}"


if __name__ == "__main__":
    # Example: Trade INFY with quantity of 10 shares
    trader = EquityPaperTrader(symbol="INFY", quantity=10)
    print("Equity paper trader initialized....")
    trader.main_trading_loop()  # This runs continuously