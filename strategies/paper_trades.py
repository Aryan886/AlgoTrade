import pandas as pd 
from datetime import datetime 
import time
from utils.utility import setup_paper_trading_logger
from utils.db_func import (
    fetch_latest_delta_data, 
    fetch_vix_data,
)
import os
import json
from strategies.strategy import donchian_ao_strategy
import uuid
import re
from typing import Dict, List, Optional

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Initialize loggers
paper_logger, trade_logger, position_logger = setup_paper_trading_logger()

class PaperTrader:
    def __init__(self, symbol="NIFTY50"):
        self.symbol = symbol
        self.position = None
        self.adjustment_history = []
        self.adjustment_cost = 0
        self.entry_criteria = None
        self.last_risk_check = None
        self.last_profit_check = None
        self.entry_time = None
        self.last_data_refresh = None
        self.data_freshness_threshold = 45  # seconds
        
        self.last_signal_attempt = None
        self.signal_cooldown = 300  # 5 minutes in seconds
        
        #loggers
        paper_logger, trade_logger, position_logger = setup_paper_trading_logger()
        self.logger = paper_logger
        self.trade_logger = trade_logger
        self.position_logger = position_logger

        #json file
        self.active_position_file = "active_position.json"


        #Load up position if available...
        self.load_position()

    def save_position(self):
        """Save current position and related data to JSON file"""
        if not self.position:
            # remove/clear active_position.json safely
            try:
                open(self.active_position_file, 'w').close()
            except Exception as e:
                self.logger.exception("Failed to clear active position file: %s", e)
            return
        
        try:
            position_data = {
                'position': self.position,
                'adjustment_history': self.adjustment_history,
                'adjustment_cost': self.adjustment_cost,
                'entry_criteria': self.entry_criteria,
                'entry_time': self.entry_time.isoformat() if self.entry_time else None,
                'last_risk_check': self.last_risk_check.isoformat() if self.last_risk_check else None,
                'last_profit_check': self.last_profit_check.isoformat() if self.last_profit_check else None,
                'last_signal_attempt': self.last_signal_attempt.isoformat() if self.last_signal_attempt else None,
                'symbol': self.symbol
            }
            
            with open('active_position.json', 'w') as f:
                json.dump(position_data, f, indent=2, default=str)
            
            self.logger.debug("Position saved to active_position.json")
            
        except Exception as e:
            self.logger.error(f"Error saving position: {e}")

    def load_position(self):
        """Load position from JSON file if it exists"""
        try:
            if os.path.exists('active_position.json'):
                with open('active_position.json', 'r') as f:
                    data = json.load(f)
                
                # Restore position data
                self.position = data.get('position')
                self.adjustment_history = data.get('adjustment_history', [])
                self.adjustment_cost = data.get('adjustment_cost', 0)
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
                    self.logger.info("POSITION RESTORED FROM FILE:")
                    ce_sym = self.position.get('ce_symbol')
                    pe_sym = self.position.get('pe_symbol')
                    ce_price = self.position.get('current_prices', {}).get('ce')
                    pe_price = self.position.get('current_prices', {}).get('pe')
                    if ce_sym:
                        self.logger.info(f"  CE: {ce_sym} at {ce_price}")
                    if pe_sym:
                        self.logger.info(f"  PE: {pe_sym} at {pe_price}")
                self.logger.info(f"  Entry Time: {self.entry_time}")
                self.logger.info(f"  Adjustment Cost: {self.adjustment_cost}")
                self.logger.info(f"  Adjustments Count: {len(self.adjustment_history)}")
                return True
                
        except Exception as e:
            self.logger.error(f"Error loading position: {e}")
        
        return False


    def parse_expiry_from_symbol(self, symbol: str):
        try:
            m = re.search(r"(NIFTY|BANKNIFTY)(\d{2}[A-Z]{3}\d{0,2})", symbol)
            if not m:
                return None

            expiry_str = m.group(2)  # e.g. "24JUN" or "24AUG01"

            # Try weekly (YYMMMDD)
            try:
                return datetime.strptime(expiry_str, "%y%b%d").date()
            except ValueError:
                pass

            # Try monthly (YYMMM)
            try:
                return datetime.strptime(expiry_str, "%y%b").date()
            except ValueError:
                pass

            return None
        except Exception as e:
            self.logger.exception("Failed to parse expiry from symbol: %s", e)
            return None
    
    def is_position_expired(self):
        if not self.has_active_position():
            return False
        try:
            current_date = datetime.now().date()
            ce_sym = self.position.get('ce_symbol')
            pe_sym = self.position.get('pe_symbol')

            if ce_sym:
                ce_expiry = self.parse_expiry_from_symbol(ce_sym)
                if ce_expiry and ce_expiry < current_date:
                    self.logger.info(f"CE option {ce_sym} expired on {ce_expiry}")
                    return True

            if pe_sym:
                pe_expiry = self.parse_expiry_from_symbol(pe_sym)
                if pe_expiry and pe_expiry < current_date:
                    self.logger.info(f"PE option {pe_sym} expired on {pe_expiry}")
                    return True

            return False
        except Exception as e:
            self.logger.error(f"Error checking position expiry: {e}")
            return False


    def is_expiry_day(self):
        if not self.has_active_position():
            return False
        try:
            current_date = datetime.now().date()
            ce_sym = self.position.get('ce_symbol')
            pe_sym = self.position.get('pe_symbol')

            if ce_sym:
                ce_expiry = self.parse_expiry_from_symbol(ce_sym)
                if ce_expiry and ce_expiry == current_date:
                    return True

            if pe_sym:
                pe_expiry = self.parse_expiry_from_symbol(pe_sym)
                if pe_expiry and pe_expiry == current_date:
                    return True

            return False
        except Exception as e:
            self.logger.error(f"Error checking expiry day: {e}")
            return False

    def should_close_for_expiry(self):
        """Check if position should be closed due to expiry (close 15 min before market close on expiry day)"""
        if not self.is_expiry_day():
            return False
            
        current_time = datetime.now().time()
        # Close positions at 3:15 PM on expiry day (15 minutes before market close)
        expiry_close_time = datetime.strptime('15:15', '%H:%M').time()
        
        return current_time >= expiry_close_time
    
    def clear_position_file(self):
        """Remove the active position file when position is closed"""
        try:
            if os.path.exists('active_position.json'):
                os.remove('active_position.json')
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
            # Fetch all required data
            options_data = fetch_latest_delta_data(self.symbol)
            #self.logger.info(f"[TEMP DEBUG] options_data: {options_data}, bool check: {bool(options_data)}")
            self.last_data_refresh = datetime.now()
            print(f"[DEBUG] options_data type: {type(options_data)}, length: {len(options_data) if options_data else 'None'}")
            self.logger.info(f"[DEBUG] options_data type: {type(options_data)}, length: {len(options_data) if options_data else 'None'}")

            # Validate data freshness and quality
            if not options_data:
                self.logger.warning("No options data received")
                return False
                
            # Store timestamp for freshness tracking
            
            self.logger.debug(f"Data refreshed at {self.last_data_refresh}")
            return True
            
        except Exception as e:
            self.logger.error(f"Data refresh failed: {e}")
            return False

    @property
    def total_adjustment_costs(self):
        """Property to get total adjustment costs"""
        return self.adjustment_cost

    def main_trading_loop(self):
        """ Run continuously in the background during Trading hours """
        self.logger.info("Starting paper trading.....")

        while self.is_market_open():
            try:
                current_time = datetime.now()

                # Always try to refresh data first
                data_refreshed = self.refresh_all_data()
                
                if not data_refreshed:
                    self.logger.warning("Data refresh failed, waiting before retry")
                    time.sleep(10)  # Shorter sleep for retry
                    continue

                # Only make trading decisions with fresh data
                if not self.has_active_position() and self.should_attempt_new_signal():
                    #importlib.reload(strategies.strategy)
                    self.last_signal_attempt = datetime.now()

                    signal = donchian_ao_strategy(self.symbol)
                    self.logger.debug(f"Strategy returned signal: {bool(signal)}")
                    
                    if signal:
                        success = self.execute_paper_trade(signal)
                        if success:
                            self.logger.info("Trade executed successfully")
                        else:
                            self.logger.warning("Trade execution failed")
                    else:
                        self.logger.debug("No signal generated (cooldown/duplicate/conditions)")
                
                # Manage existing position
                if self.has_active_position():
                    self.logger.debug(f"Has active postion? {self.has_active_position()}")
                    self.manage_existing_positions()
                
                # Adaptive sleep based on market conditions
                sleep_time = 30 if self.has_active_position() else 60
                time.sleep(sleep_time)

            except Exception as e:
                self.logger.error(f"Encountered this error : {e}")
                time.sleep(30)  # Shorter retry interval

    def is_market_open(self):
        """Check if market is open (9:15 to 3:30 )"""
        now = datetime.now()
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close
    
    def has_active_position(self):
        """"Check for active position"""
        return self.position is not None
    
    def should_check_risk(self, current_time):
        """Check if it's time for risk check(Runs every 5 min)"""
        if not self.last_risk_check:
            return True
        return (current_time - self.last_risk_check).total_seconds() > 300
    
    def should_check_profit(self, current_time):
        """Checks for profit (every 5min) - Fixed comment and timing"""
        if not self.last_profit_check:
            return True
        return (current_time - self.last_profit_check).total_seconds() > 300
    
    def close_position(self, leg: str, close_price: Optional[float] = None, auto_clear: bool = True) -> bool:
        """
        Mark a single leg ('ce' or 'pe') as closed and persist the change.
        - leg must be 'ce' or 'pe'
        - close_price: if None, uses the position's current price for that leg
        - auto_clear: if True and both legs closed, clears the active position (keeps a record in adjustment_history)
        Returns True on success.
        """
        if not self.has_active_position():
            self.logger.warning("close_position called but no active position.")
            return False

        if leg not in ("ce", "pe"):
            self.logger.error("close_position called with invalid leg: %s", leg)
            return False

        try:
            entry_price = self.position.get("entry_prices", {}).get(leg)
            if entry_price is None:
                self.logger.warning("No entry price found for %s; continuing.", leg)

            # determine close price
            if close_price is None:
                close_price = self.position.get("current_prices", {}).get(leg, 0.0)

            # normalize numeric
            try:
                close_price = float(close_price)
            except Exception:
                close_price = 0.0

            # log details
            self.logger.info("CLOSING %s OPTION:", leg.upper())
            self.logger.info("  Entry Price: %s", entry_price)
            self.logger.info("  Close Price: %s", close_price)

            # mark closed safely
            self.position.setdefault("current_prices", {})[leg] = close_price
            self.position[f"{leg}_closed"] = True

            # record adjustment history (keep cost field for compatibility)
            ts = datetime.now().isoformat(sep=" ")
            # keep old behaviour: cost default 0 (downstream code appears to expect 'cost' key)
            record = {"timestamp": ts, "action": f"closed {leg.upper()}", "price": close_price, "cost": 0}
            # ensure adjustment_history exists
            if not hasattr(self, "adjustment_history") or self.adjustment_history is None:
                self.adjustment_history = []
            self.adjustment_history.append(record)

            # persist
            self.save_position()
            self.logger.info(" %s option closed successfully", leg.upper())

            # if both legs closed, optionally clear the active position (but keep saved history elsewhere if desired)
            if auto_clear and self.position.get("ce_closed") and self.position.get("pe_closed"):
                # keep a final snapshot in file (we already saved). Optionally clear runtime position
                # Do not delete adjustment_history; leave it in memory for reporting
                self.logger.info("Both legs closed. Clearing in-memory active position.")
                self.position = None
                # persist cleared file (clear_position_file should be implemented in your file)
                try:
                    if hasattr(self, "clear_position_file"):
                        self.clear_position_file()
                    else:
                        # fallback: overwrite with empty JSON
                        open(getattr(self, "active_position_file", "active_position.json"), "w").close()
                except Exception:
                    self.logger.exception("Failed to clear position file after closing both legs.")
            return True

        except Exception as e:
            self.logger.exception("Error closing position leg: %s", e)
            return False



    def check_entry_criteria_violation(self):
        """Rule 4: Check if entry criteria is still valid"""
        try:
            from strategies.strategy import get_current_entry_criteria
            current_criteria = get_current_entry_criteria(self.symbol)

            #compare with stored entry criteria
            if self.entry_criteria and current_criteria:
                #If any essential condition has reversed, close position
                for key in self.entry_criteria:
                    if self.entry_criteria[key] != current_criteria.get(key):
                        self.logger.info(f"ENTRY CRITERIA VIOLATION : {key} changed from {self.entry_criteria[key]} to {current_criteria.get(key)}")
                        self.close_position("Entry criteria violation")
                        return True
                    
            return False
        except Exception as e:
            self.logger.error(f"Error checking entry criteria : {e}")
            return False
        
    def check_vix_breach(self):
        """Rule 3 : Check if VIX breached mid-donchian"""
        try:
            vix_data = fetch_vix_data("NIFTY50")
            if vix_data.empty:
                return False
            
            from strategies.strategy import emergency_column_fix
            vix_data = emergency_column_fix(vix_data, "VIX")
            
            latest_vix = vix_data.iloc[-1]

            # Use the correct column name after emergency fix
            if latest_vix['close'] > latest_vix['donchian_mid_vix']: 
                self.logger.info(f"VIX BREACH : Closing position due to high VIX")

                prices_updated = self.update_current_prices()
                if not prices_updated:
                    self.logger.warning("Could not update prices before VIX Closure")

                self.close_position("VIX Breach")
                return True
            
            return False
        
        except Exception as e:
            self.logger.error(f"Error checking vix breach : {e}")
            return False
        
    def find_replacement_option(self):
        """Find replacement option based on delta (adjusting)."""
        try:
            self.logger.info("Starting replacement option search...")
            options_data = fetch_latest_delta_data(self.symbol)
            if not options_data:
                self.logger.warning("No options data available for replacement")
                return

            ce_sym = self.position.get('ce_symbol')
            pe_sym = self.position.get('pe_symbol')
            ce_closed = self.position.get('ce_closed', False)
            pe_closed = self.position.get('pe_closed', False)

            remaining_symbol, remaining_type = None, None
            if ce_sym and not ce_closed:
                remaining_symbol, remaining_type = ce_sym, 'CE'
            elif pe_sym and not pe_closed:
                remaining_symbol, remaining_type = pe_sym, 'PE'

            if not remaining_symbol:
                self.logger.warning("No remaining option open - cannot replace")
                return

            # find its price & delta
            symbol_map = {o.get("symbol"): o for o in options_data if o.get("symbol")}
            base_opt = symbol_map.get(remaining_symbol)
            if not base_opt:
                self.logger.warning(f"Remaining option {remaining_symbol} not found in fresh data.")
                return

            remaining_price = base_opt.get("last_price") or base_opt.get("ltp", 0)
            remaining_delta = base_opt.get("delta", 0)
            target_type = 'PE' if remaining_type == 'CE' else 'CE'
            self.logger.info(f"Remaining option: {remaining_symbol} price={remaining_price}, delta={remaining_delta}")

            # search candidates
            candidates = []
            for opt in options_data:
                if opt.get("option_type") == target_type:
                    price = opt.get("last_price") or opt.get("ltp")
                    delta = opt.get("delta")
                    if price is None or delta is None:
                        continue
                    price_diff = abs(price - remaining_price)
                    delta_diff = abs(abs(delta) - abs(remaining_delta))
                    if price_diff <= 15 and delta_diff <= 6:
                        candidates.append(opt)

            self.logger.info(f"Found {len(candidates)} replacement candidates")

            if not candidates:
                self.logger.warning("No suitable replacement options found")
                return

            # best candidate
            best = min(candidates, key=lambda x: abs(abs(x["delta"]) - abs(remaining_delta)))
            best_sym, best_price = best["symbol"], best.get("last_price") or best.get("ltp")
            self.logger.info(f"Best replacement: {best_sym} price={best_price}, delta={best['delta']}")

            if target_type == "CE":
                self.position["ce_symbol"] = best_sym
                self.position["ce_closed"] = False
                self.position["entry_prices"]["ce"] = best_price
                self.position["current_prices"]["ce"] = best_price
            else:
                self.position["pe_symbol"] = best_sym
                self.position["pe_closed"] = False
                self.position["entry_prices"]["pe"] = best_price
                self.position["current_prices"]["pe"] = best_price

            self.save_position()
            self.logger.info(f"Replacement successful: added {target_type} {best_sym} at {best_price}")
        except Exception as e:
            self.logger.error(f"Error finding replacement option: {e}")

    def close_option(self, option_type):
        """close specific option (CE/PE) and add adjustment costs"""
        key = 'ce' if option_type.upper() == 'CE' else 'pe'
        if key in self.position['current_prices']:
            close_price = self.position['current_prices'][key]
            entry_price = self.position['entry_prices'][key]

            self.logger.info(f"CLOSING {option_type} OPTION:")
            self.logger.info(f"  Entry Price: {entry_price}")
            self.logger.info(f"  Close Price: {close_price}")

            adjustment_cost = max(0, (close_price - entry_price))
            self.adjustment_cost += adjustment_cost

            self.logger.info(f"  Adjustment Cost: {adjustment_cost}")
            self.logger.info(f"  Total Adjustment Cost: {self.adjustment_cost}")

            # mark as closed instead of deleting symbol
            self.position[f"{key}_closed"] = True
            self.position['current_prices'][key] = 0

            self.adjustment_history.append({
                'timestamp': datetime.now().isoformat(sep=" "),
                'action': f'closed {option_type}',
                'price': close_price,
                'cost': adjustment_cost
            })

            self.save_position()
            self.logger.info(f" {option_type} option closed successfully")
        else:
            self.logger.warning(f"Cannot close {option_type} - not found in current positions")

    def check_positon_adjustment(self):
        """Rule 2: Check if position adjustment is needed"""
        self.logger.debug("Position checker called successfully!!!")
        if not self.has_active_position():
            return
        
        try:
            ce_price = self.position['current_prices'].get('CE', 0)
            pe_price = self.position['current_prices'].get('PE', 0)
            
            # ADD DETAILED LOGGING
            self.logger.debug(f"ADJUSTMENT CHECK - CE: {ce_price}, PE: {pe_price}")
            
            # Check if one option is below 100
            below_100 = ce_price < 100 or pe_price < 100
            price_diff = abs(ce_price - pe_price)
            
            self.logger.debug(f"ADJUSTMENT CHECK - Below 100: {below_100}, Price diff: {price_diff}")
            
            if below_100:
                self.logger.info(f"ADJUSTMENT TRIGGER - One option below 100 (CE: {ce_price}, PE: {pe_price})")
                
                if price_diff > 18:
                    self.logger.info(f"ADJUSTMENT EXECUTING - Price difference {price_diff} >  18 threshold")
                    
                    # Close the higher priced option
                    if ce_price > pe_price:
                        self.logger.info(f"Closing CE option at {ce_price} (higher than PE {pe_price})")
                        self.close_option('CE')
                    else:
                        self.logger.info(f"Closing PE option at {pe_price} (higher than CE {ce_price})")
                        self.close_option('PE')

                    # Find replacement option
                    self.logger.info("Searching for replacement option...")
                    self.find_replacement_option()
                else:
                    self.logger.info(f"ADJUSTMENT SKIPPED - Price difference {price_diff} <= 18 threshold")
            else:
                self.logger.debug(f"ADJUSTMENT NOT NEEDED - Both options above 100")

        except Exception as e:
            self.logger.error(f"Error in position adjustment : {e}")


    def calculate_current_profit(self):
        """Calculate current profit considering all adjustments"""
        self.logger.debug("Calculate Profit function sucessfully Called!!")
        if not self.has_active_position():
            return 0 
        
        try:
            self.logger.debug(f"Entry prices: {self.position['entry_prices']}")
            self.logger.debug(f"Current prices: {self.position['current_prices']}")
            
            #Base profit calculation
            entry_total = sum(self.position['entry_prices'].values())
            current_total = sum(self.position['current_prices'].values())
            current_profit = entry_total - current_total

            #Subtract adjustment costs
            net_profit = current_profit - self.total_adjustment_costs

            self.logger.debug(f"Entry total: {entry_total}, Current total: {current_total}")
            self.logger.debug(f"Gross profit: {current_profit}, Adjustment costs: {self.total_adjustment_costs}")
            self.logger.debug(f"Net profit: {net_profit}")

            return net_profit
            
        except Exception as e:
            self.logger.error(f"Error in calculate_current_profit: {e}")
            return 0  
    
    def check_profit_target(self):
        """Rule 1: Check if profit target(16pts) is reached """
        if not self.has_active_position():
            return False
        
        try:
            profit = self.calculate_current_profit()

            if profit >= 16:
                self.logger.info(f"PROFIT TARGET REACHED : {profit} points")
                self.close_position("Profit target reached")
                return True
            
            return False
        except Exception as e:
            self.logger.error(f"Error checking profit target : {e}")
            return False
        
    def update_current_prices(self, options_data: List[Dict]) -> bool:
        """
        Efficiently update current_prices for the active position from fresh options_data.
        Returns True if at least one price updated, False otherwise.
        """
        try:
            if not self.has_active_position():
                return False

            # fast lookup by symbol
            if not options_data:
                self.logger.warning("update_current_prices called with empty options_data.")
                return False
            symbol_map = {o.get("symbol"): o for o in options_data if o.get("symbol")}

            ce_sym = self.position.get("ce_symbol")
            pe_sym = self.position.get("pe_symbol")
            if not ce_sym or not pe_sym:
                self.logger.warning("Position missing ce_symbol/pe_symbol; cannot update.")
                return False

            updated = False

            # update CE if open
            if not self.position.get("ce_closed", False):
                ce_data = symbol_map.get(ce_sym)
                if ce_data and ("last_price" in ce_data or "ltp" in ce_data):
                    new_price = ce_data.get("last_price", ce_data.get("ltp"))
                    try:
                        new_price = float(new_price)
                    except Exception:
                        new_price = None
                    if new_price is not None:
                        if self.position.get("current_prices", {}).get("ce") != new_price:
                            self.position.setdefault("current_prices", {})["ce"] = new_price
                            updated = True
                else:
                    self.logger.debug("CE %s not found or missing price in options_data", ce_sym)

            # update PE if open
            if not self.position.get("pe_closed", False):
                pe_data = symbol_map.get(pe_sym)
                if pe_data and ("last_price" in pe_data or "ltp" in pe_data):
                    new_price = pe_data.get("last_price", pe_data.get("ltp"))
                    try:
                        new_price = float(new_price)
                    except Exception:
                        new_price = None
                    if new_price is not None:
                        if self.position.get("current_prices", {}).get("pe") != new_price:
                            self.position.setdefault("current_prices", {})["pe"] = new_price
                            updated = True
                else:
                    self.logger.debug("PE %s not found or missing price in options_data", pe_sym)

            if updated:
                self.save_position()
                self.logger.debug("Position prices updated.")
                return True

            # nothing updated; keep silent
            return False

        except Exception as e:
            self.logger.exception("Error updating current prices: %s", e)
            return False

    def manage_existing_positions(self):
        """Manage existing positions with data freshness checks and expiry handling"""
        current_time = datetime.now()

        # FIRST: Check for expired positions
        if self.is_position_expired():
            self.logger.warning("Position contains expired options - closing immediately")
            self.close_position("Options expired")
            return

        # SECOND: Check if we should close due to expiry today
        if self.should_close_for_expiry():
            self.logger.info("Closing position - expiry day auto-close (3:15 PM)")
            # Update prices one final time before closing
            self.update_current_prices()
            self.close_position("Expiry day auto-close")
            return

        # THIRD: Normal position management continues...
        # Always refresh market data 
        data_refreshed = self.refresh_all_data()
        if not data_refreshed:
            self.logger.warning("Skipping position management - failed to refresh data")
            return

        # Update current prices with freshness validation
        prices_updated = self.update_current_prices()
        current_profit = self.calculate_current_profit()
        self.logger.info(f"CURRENT NET P&L: {current_profit} points")

        if not prices_updated:
            self.logger.warning("Skipping position management - stale/no price data")
            return  # Skip this iteration if data is stale

        # Rule 1: Check profit target every 5min (only with fresh data)
        if self.should_check_profit(current_time):
            if self.check_profit_target():
                return #Position closed
            self.last_profit_check = current_time
            
        #Rule 2: Dynamic position adjustment (only with fresh data)
        self.check_positon_adjustment()

        #Rule 3 & 4 : Risk management checks every 5 min
        if self.should_check_risk(current_time):
            if self.check_vix_breach() or self.check_entry_criteria_violation():
                return #Position closed
            self.last_risk_check = current_time
            
        #Forced logging : 
        if self.has_active_position():
            ce_price = self.position['current_prices'].get('CE', 0)
            pe_price = self.position['current_prices'].get('PE', 0)
            self.logger.info(f"CURRENT PRICES - CE: ₹{ce_price}, PE: ₹{pe_price}")
            
            # Force adjustment check
            self.check_positon_adjustment()
                
    def execute_paper_trade(self, signal: Dict) -> bool:
        """
        Create and persist a new single-position entry.
        Stores only symbols and numeric prices (no raw option dicts).
        Returns True on success, False otherwise.
        """
        if self.has_active_position():
            self.logger.warning("execute_paper_trade called but an active position already exists. Ignoring new signal.")
            return False

        try:
            ce_opt = signal.get("ce_option")
            pe_opt = signal.get("pe_option")

            # validate minimal fields
            if not ce_opt or not pe_opt:
                self.logger.error("Signal missing CE/PE option dicts.")
                return False
            ce_sym = ce_opt.get("symbol")
            pe_sym = pe_opt.get("symbol")
            ce_price = ce_opt.get("last_price")
            pe_price = pe_opt.get("last_price")

            if not ce_sym or not pe_sym or ce_price is None or pe_price is None:
                self.logger.error("Signal CE/PE missing 'symbol' or 'last_price' fields.")
                return False

            now_iso = datetime.now().isoformat(sep=" ")

            # compact position representation (stable across saves)
            self.position = {
                "id": str(uuid.uuid4()),
                "ce_symbol": ce_sym,
                "pe_symbol": pe_sym,
                "entry_prices": {"ce": float(ce_price), "pe": float(pe_price)},
                "current_prices": {"ce": float(ce_price), "pe": float(pe_price)},
                "entry_time": now_iso,
                "ce_closed": False,
                "pe_closed": False,
            }

            # initialize adjustment containers if missing in object model
            if not hasattr(self, "adjustment_history") or self.adjustment_history is None:
                self.adjustment_history = []

            # persist once
            self.save_position()

            # concise logging
            self.logger.info("NEW POSITION OPENED:")
            self.logger.info(f"CE: {ce_sym} at {ce_price}")
            self.logger.info(f"PE: {pe_sym} at {pe_price}")
            self.logger.info(f"Total entry points: {float(ce_price) + float(pe_price)}")
            self.logger.info(f"Position opened at: {now_iso}")
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
            current_profit = self.calculate_current_profit()
            return {
                'position_active': True,
                'entry_time': self.entry_time,
                'current_profit': current_profit,
                'adjustment_costs': self.total_adjustment_costs,
                'adjustments_count': len(self.adjustment_history),
                'ce_price': self.position['current_prices'].get('CE'),
                'pe_price': self.position['current_prices'].get('PE'),
                'last_data_refresh': self.last_data_refresh,
                'price_timestamps': self.position.get('price_timestamps', {})
            }
        except Exception as e:
            return f"Error getting status: {e}"

if __name__ == "__main__":
    trader = PaperTrader("NIFTY50")
    print("Paper trader initialized....")
    trader.main_trading_loop() # this runs continuously