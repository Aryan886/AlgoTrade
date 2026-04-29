#Paper trading module for Donchian AO strategy
import pandas as pd 
from datetime import date, datetime, timedelta 
import time
from utils.utility import setup_paper_trading_logger
from utils.db_func import (
    fetch_latest_delta_data, 
    fetch_latest_delta_snapshot,
    fetch_vix_data,
)
import os
import json
from core.strat_donchian import donchian_ao_strategy
import uuid
import re
from typing import Dict, List, Optional

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Initialize loggers
paper_logger, trade_logger, position_logger, sma_logger, equity_logger, _nifty_logger = setup_paper_trading_logger()

class PaperTraderDonchian:
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
        self.latest_delta_snapshot = None
        
        #loggers
        paper_logger, trade_logger, position_logger, sma_logger, equity_logger, _nifty_logger = setup_paper_trading_logger()
        self.logger = paper_logger
        self.trade_logger = trade_logger
        self.position_logger = position_logger

        #json file
        self.active_position_file = "active_position.json"

        #Load up position if available...
        self.load_position()

    def _normalize_expiry_value(self, expiry_value) -> Optional[str]:
        if expiry_value is None:
            return None
        if isinstance(expiry_value, datetime):
            return expiry_value.date().isoformat()
        if isinstance(expiry_value, date):
            return expiry_value.isoformat()
        expiry_text = str(expiry_value).strip()
        if not expiry_text:
            return None
        try:
            return date.fromisoformat(expiry_text[:10]).isoformat()
        except ValueError:
            return None

    def _monthly_contract_expiry(self, year: int, month: int) -> date:
        next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        cursor = next_month - timedelta(days=1)
        while cursor.weekday() != 1:  # Tuesday
            cursor -= timedelta(days=1)
        return cursor

    def _resolve_position_expiry(self, leg_key: str) -> Optional[date]:
        if not self.position:
            return None

        expiry_field = f"{leg_key.lower()}_expiry"
        stored_expiry = self._normalize_expiry_value(self.position.get(expiry_field))
        if stored_expiry:
            try:
                return date.fromisoformat(stored_expiry)
            except ValueError:
                return None

        symbol_field = f"{leg_key.lower()}_symbol"
        return self.parse_expiry_from_symbol(self.position.get(symbol_field))

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
                'adjustment_cost': self.adjustment_impact,
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
                self.adjustment_impact = data.get('adjustment_cost', 0)
                self.entry_criteria = data.get('entry_criteria')
                
                # Initialize new replacement tracking fields if they don't exist
                if self.position:
                    if 'replacement_cycles' not in self.position:
                        self.position['replacement_cycles'] = 0
                    if 'last_replacement_attempt' not in self.position:
                        self.position['last_replacement_attempt'] = None
                    if 'waiting_for_replacement' not in self.position:
                        self.position['waiting_for_replacement'] = False
                    if 'ce_effectively_closed' not in self.position:
                        self.position['ce_effectively_closed'] = False
                    if 'pe_effectively_closed' not in self.position:
                        self.position['pe_effectively_closed'] = False
                    if 'ce_expiry' not in self.position:
                        self.position['ce_expiry'] = self._normalize_expiry_value(
                            self.parse_expiry_from_symbol(self.position.get('ce_symbol'))
                        )
                    else:
                        self.position['ce_expiry'] = self._normalize_expiry_value(self.position.get('ce_expiry'))
                    if 'pe_expiry' not in self.position:
                        self.position['pe_expiry'] = self._normalize_expiry_value(
                            self.parse_expiry_from_symbol(self.position.get('pe_symbol'))
                        )
                    else:
                        self.position['pe_expiry'] = self._normalize_expiry_value(self.position.get('pe_expiry'))
                
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
                self.logger.info(f"  Adjustment Cost: {self.adjustment_impact}")
                self.logger.info(f"  Adjustments Count: {len(self.adjustment_history)}")
                return True
                
        except Exception as e:
            self.logger.error(f"Error loading position: {e}")
        
        return False

    def parse_expiry_from_symbol(self, symbol: str):
        try:
            symbol_text = str(symbol or "").strip().upper()
            if not symbol_text:
                return None

            weekly_match = re.search(r"(?:NIFTY|BANKNIFTY)(\d{2}[A-Z]{3}\d{2})(?=\d{3,5}(?:CE|PE)$)", symbol_text)
            if weekly_match:
                try:
                    weekly_expiry = datetime.strptime(weekly_match.group(1), "%y%b%d").date()
                    if weekly_expiry.weekday() == 1:
                        return weekly_expiry
                except ValueError:
                    pass

            monthly_match = re.search(r"(?:NIFTY|BANKNIFTY)(\d{2}[A-Z]{3})(?=\d{3,5}(?:CE|PE)$)", symbol_text)
            if monthly_match:
                try:
                    parsed_month = datetime.strptime(monthly_match.group(1), "%y%b")
                    return self._monthly_contract_expiry(parsed_month.year, parsed_month.month)
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
                ce_expiry = self._resolve_position_expiry("ce")
                if ce_expiry and ce_expiry < current_date:
                    self.logger.info(f"CE option {ce_sym} expired on {ce_expiry}")
                    return True

            if pe_sym:
                pe_expiry = self._resolve_position_expiry("pe")
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
                ce_expiry = self._resolve_position_expiry("ce")
                if ce_expiry and ce_expiry == current_date:
                    return True

            if pe_sym:
                pe_expiry = self._resolve_position_expiry("pe")
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

    def _parse_snapshot_timestamp(self, timestamp_value):
        if not timestamp_value:
            return None
        if isinstance(timestamp_value, datetime):
            return timestamp_value
        try:
            return datetime.fromisoformat(str(timestamp_value))
        except Exception:
            try:
                return datetime.strptime(str(timestamp_value), "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None

    def _get_active_required_contracts(self) -> List[Dict]:
        if not self.has_active_position():
            return []

        required_contracts = []
        if not self.position.get("ce_closed", False):
            ce_symbol = self.position.get("ce_symbol")
            if ce_symbol:
                required_contracts.append({"tradingsymbol": ce_symbol, "option_type": "CE"})
        if not self.position.get("pe_closed", False):
            pe_symbol = self.position.get("pe_symbol")
            if pe_symbol:
                required_contracts.append({"tradingsymbol": pe_symbol, "option_type": "PE"})
        return required_contracts

    def _format_price_update_summary(self, price_status: Dict) -> str:
        found = ", ".join(price_status.get("found_symbols", [])) or "none"
        missing = ", ".join(price_status.get("missing_symbols", [])) or "none"
        expiries = ", ".join(price_status.get("snapshot_expiries", [])) or "none"
        return (
            f"status={price_status.get('status')} "
            f"snapshot_ts={price_status.get('snapshot_timestamp')} "
            f"found={found} missing={missing} "
            f"changed={price_status.get('changed_symbols', [])} "
            f"fresh={price_status.get('is_fresh')} expiries={expiries}"
        )

    def refresh_all_data(self, required_contracts: Optional[List[Dict]] = None, refresh_if_missing: bool = False):
        """Centralized data refresh with validation"""
        try:
            snapshot = fetch_latest_delta_snapshot(
                self.symbol,
                required_contracts=required_contracts,
                refresh_if_missing=refresh_if_missing,
            )
            options_data = snapshot.get("options_data", [])
            self.latest_delta_snapshot = snapshot
            self.last_data_refresh = datetime.now()
            snapshot_timestamp = snapshot.get("timestamp")
            missing_required = snapshot.get("missing_required_contracts", [])
            print(f"[DEBUG] options_data type: {type(options_data)}, length: {len(options_data) if options_data else 'None'}")
            self.logger.info(
                f"[DEBUG] options_data type: {type(options_data)}, length: {len(options_data) if options_data else 'None'}, "
                f"snapshot_ts: {snapshot_timestamp}, missing_required: {missing_required}"
            )

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
        return self.adjustment_impact

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
        FIXED VERSION - Mark a single leg as closed with proper adjustment cost tracking.
        Always fetches fresh prices before closing unless close_price is explicitly provided.
        """
        if not self.has_active_position():
            self.logger.warning("close_position called but no active position.")
            return False

        if leg.lower() == "all":
            self.logger.info("Closing ALL legs due to rule trigger")
            # Update prices before closing all legs
            try:
                self.update_current_prices()
            except Exception as e:
                self.logger.error(f"Failed to update prices before closing all legs: {e}")
                self.logger.error("Proceeding with last known prices")
            
            success_ce = self.close_position("ce", auto_clear=False)
            success_pe = self.close_position("pe", auto_clear=False)
            if success_ce or success_pe:
                try:
                    final_pnl = self.calculate_current_profit()
                    self.logger.info(f"Final Net P&L on closure: {final_pnl}")
                    self.trade_logger.info(f"Final Net P&L on closure: {final_pnl}")
                except Exception as e:
                    self.logger.error(f"Could not calculate final P&L due to: {e}")

                if auto_clear:
                    self.logger.info("Both legs closed. Clearing in-memory active position.")
                    self.position = None
                    self.clear_position_file()
                return True
            return False

        if leg not in ("ce", "pe"):
            self.logger.error("close_position called with invalid leg: %s", leg)
            return False

        try:
            # Get entry price (try both cases)
            entry_price = (self.position.get("entry_prices", {}).get(leg) or 
                        self.position.get("entry_prices", {}).get(leg.upper()))
            
            if entry_price is None:
                self.logger.error(f"No entry price found for {leg} leg - this should not happen!")
                self.logger.error("Available entry_prices keys: %s", list(self.position.get("entry_prices", {}).keys()))
                entry_price = 0.0

            # Determine close price - fetch fresh if not provided
            if close_price is None:
                # Try to get fresh price first
                try:
                    self.update_current_prices()
                except Exception as e:
                    self.logger.error(f"Failed to update prices before closing {leg}: {e}")
                    self.logger.error("Using last known price from position data")
                
                close_price = (self.position.get("current_prices", {}).get(leg) or 
                            self.position.get("current_prices", {}).get(leg.upper()))
                
                if close_price is None:
                    self.logger.error(f"No current price found for {leg} leg after update attempt")
                    self.logger.error("Available current_prices keys: %s", list(self.position.get("current_prices", {}).keys()))
                    close_price = entry_price  # Fallback to entry price

            # Normalize numeric values
            try:
                close_price = float(close_price)
                entry_price = float(entry_price)
            except (ValueError, TypeError) as e:
                self.logger.error(f"Failed to convert prices to float: entry_price={entry_price}, close_price={close_price}")
                self.logger.error(f"Conversion error: {e}")
                close_price = 0.0
                entry_price = 0.0

            # Calculate adjustment cost (loss/gain from this leg)
            adjustment_cost = close_price - entry_price

            # Update total adjustment impact
            self.adjustment_impact += adjustment_cost

            # Log details with more context
            self.logger.info("CLOSING %s OPTION:", leg.upper())
            self.logger.info("  Entry Price: %s", entry_price)
            self.logger.info("  Close Price: %s", close_price)
            self.logger.info("  Adjustment Cost: %s", adjustment_cost)
            self.logger.info("  Total Adjustment Cost: %s", self.adjustment_impact)

            self.trade_logger.info("CLOSING %s OPTION:", leg.upper())
            self.trade_logger.info("  Entry Price: %s", entry_price)
            self.trade_logger.info("  Close Price: %s", close_price)
            self.trade_logger.info("  Adjustment Cost: %s", adjustment_cost)

            # Mark as closed
            self.position[f"{leg}_closed"] = True

            # IMPORTANT: Remove closed leg from current_prices to avoid double counting
            if "current_prices" in self.position:
                self.position["current_prices"].pop(leg, None)
                self.position["current_prices"].pop(leg.upper(), None)

            # Record in adjustment history with actual cost
            ts = datetime.now().isoformat(sep=" ")
            record = {
                "timestamp": ts, 
                "action": f"closed {leg.upper()}", 
                "entry_price": entry_price,
                "close_price": close_price, 
                "cost": adjustment_cost
            }
            
            if not hasattr(self, "adjustment_history") or self.adjustment_history is None:
                self.adjustment_history = []
            self.adjustment_history.append(record)

            # Persist changes
            self.save_position()
            self.logger.info("%s option closed successfully", leg.upper())

            # Auto-clear logic
            if auto_clear and self.position.get("ce_closed") and self.position.get("pe_closed"):
                try:
                    final_pnl = self.calculate_current_profit()
                    self.logger.info(f"Final Net P&L on closure: {final_pnl}")
                    self.trade_logger.info(f"Final Net P&L on closure: {final_pnl}")
                except Exception as e:
                    self.logger.error(f"Could not calculate final profit due to: {e}")
                    
                # Clear adjustment tracking after trade completion
                self.logger.info(f"Trade completed. Total adjustment impact for this trade: {self.adjustment_impact}")
                self.logger.info(f"Number of adjustments made: {len(self.adjustment_history)}")
                self.adjustment_impact = 0.0
                self.adjustment_history = []
                final_pnl = 0.0
                self.logger.info("Adjustment costs cleared after trade completion")
                    
                self.logger.info("Both legs closed. Clearing in-memory active position.")
                self.trade_logger.info("Both legs closed. Clearing in-memory active position.")
                self.position = None
                
                try:
                    self.clear_position_file()
                except Exception:
                    self.logger.exception("Failed to clear position file after closing both legs.")

            return True

        except Exception as e:
            self.logger.exception("Error closing position leg: %s", e)
            return False

    def check_entry_criteria_violation(self):
        """Rule 4: Check if entry criteria is still valid"""
        try:
            from core.strat_donchian import get_current_entry_criteria
            current_criteria = get_current_entry_criteria(self.symbol)

            self.logger.info(f"[{self.symbol}] Current criteria fetched: {current_criteria}")

            current_values = [v for v in current_criteria.values() if v in (-1, 1)]
            if len(current_values) < 4:
                self.logger.warning(f"Incomplete criteria data: {current_criteria}")
                return False


            #compare with stored entry criteria
            if self.entry_criteria and current_criteria:
                # Check if all four essentials are now aligned in same direction
                current_values = list(current_criteria.values())
                
                # Close only if all essentials are now +1 or all are -1
                if len(set(current_values)) == 1:  # All values are the same
                    self.logger.info(f"ENTRY CRITERIA VIOLATION: All essentials aligned in same direction {current_values[0]}")
                    self.logger.info(f"Current criteria: {current_criteria}")
                    self.close_position("all")
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
            
            from core.strat_donchian import emergency_column_fix
            vix_data = emergency_column_fix(vix_data, "VIX")
            
            latest_vix = vix_data.iloc[-1]

            # Use the correct column name after emergency fix
            if latest_vix['close'] > latest_vix['donchian_mid_vix']: 
                self.logger.info(f"VIX BREACH : Closing position due to high VIX")

                price_status = self.update_current_prices()
                if price_status.get("status") in {"missing", "stale", "error"}:
                    self.logger.warning(
                        "Could not update prices before VIX Closure: %s",
                        self._format_price_update_summary(price_status),
                    )

                self.close_position("all")
                return True
            
            return False
        
        except Exception as e:
            self.logger.error(f"Error checking vix breach : {e}")
            return False
        
    def find_replacement_option(self):
        """Find replacement option based on delta (adjusting)."""
        try:
            self.logger.info("Starting replacement option search...")
            options_data = fetch_latest_delta_data(
                self.symbol,
                required_contracts=self._get_active_required_contracts(),
                refresh_if_missing=True,
            )
            if not options_data:
                self.logger.warning("No options data available for replacement")
                return

            ce_sym = self.position.get('ce_symbol')
            pe_sym = self.position.get('pe_symbol')
            ce_closed = self.position.get('ce_closed', False)
            pe_closed = self.position.get('pe_closed', False)

            # Determine which leg needs replacement
            ce_effectively_closed = self.position.get("ce_effectively_closed", False)
            pe_effectively_closed = self.position.get("pe_effectively_closed", False)
            
            remaining_symbol, remaining_type = None, None
            target_type = None
            
            # Check for effectively closed legs first (0-price legs)
            if ce_effectively_closed and pe_sym and not pe_closed:
                remaining_symbol, remaining_type = pe_sym, 'PE'
                target_type = 'CE'
                self.logger.info("Replacing effectively closed CE leg")
            elif pe_effectively_closed and ce_sym and not ce_closed:
                remaining_symbol, remaining_type = ce_sym, 'CE'
                target_type = 'PE'
                self.logger.info("Replacing effectively closed PE leg")
            # Fallback to original logic for actually closed legs
            elif ce_sym and not ce_closed:
                remaining_symbol, remaining_type = ce_sym, 'CE'
                target_type = 'PE'
            elif pe_sym and not pe_closed:
                remaining_symbol, remaining_type = pe_sym, 'PE'
                target_type = 'CE'

            #if both legs are already closed, skip replacement
            if (self.position.get("ce_closed", False) and self.position.get("pe_closed", False)):
                self.logger.warning("Both legs already closed, No replacements to be made")
                return
            
            if not remaining_symbol or not target_type:
                self.logger.warning("No remaining option open or target type unclear - cannot replace")
                return

            # build symbol lookup (use 'tradingsymbol' consistently)
            symbol_map = {
                o.get("tradingsymbol").upper(): o
                for o in options_data
                if o.get("tradingsymbol")
            }

            base_opt = symbol_map.get(remaining_symbol.upper())
            if not base_opt:
                self.logger.warning(f"Remaining option {remaining_symbol} not found in fresh data.")
                return

            remaining_price = base_opt.get("last_price") or base_opt.get("ltp", 0)
            remaining_delta = base_opt.get("delta", 0)
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
                # Increment cycle counter for timeout tracking
                self.position["replacement_cycles"] = self.position.get("replacement_cycles", 0) + 1
                self.save_position()
                return

            # best candidate
            best = min(candidates, key=lambda x: abs(abs(x["delta"]) - abs(remaining_delta)))
            best_sym, best_price = best["tradingsymbol"], best.get("last_price") or best.get("ltp")
            best_expiry = self._normalize_expiry_value(best.get("expiry") or best.get("expiry_date"))
            self.logger.info(f"Best replacement: {best_sym} price={best_price}, delta={best['delta']}")

            if target_type == "CE":
                self.position["ce_symbol"] = best_sym
                self.position["ce_expiry"] = best_expiry
                self.position["ce_closed"] = False
                self.position["ce_effectively_closed"] = False  # Clear effectively closed flag
                self.position["entry_prices"]["ce"] = best_price
                self.position["entry_prices"]["CE"] = best_price
                self.position["current_prices"]["ce"] = best_price
                self.position["current_prices"]["CE"] = best_price
            else:
                self.position["pe_symbol"] = best_sym
                self.position["pe_expiry"] = best_expiry
                self.position["pe_closed"] = False
                self.position["pe_effectively_closed"] = False  # Clear effectively closed flag
                self.position["entry_prices"]["pe"] = best_price
                self.position["entry_prices"]["PE"] = best_price
                self.position["current_prices"]["pe"] = best_price
                self.position["current_prices"]["PE"] = best_price

            # Reset waiting state when replacement is successful
            self.position["waiting_for_replacement"] = False
            self.position["replacement_cycles"] = 0
            self.position["last_replacement_attempt"] = None
            
            self.save_position()
            self.logger.info(f"Replacement successful: added {target_type} {best_sym} at {best_price}")
        except Exception as e:
            self.logger.error(f"Error finding replacement option: {e}")

    
    def check_positon_adjustment(self):
        """Rule 2: Check if position adjustment is needed"""
        self.logger.debug("Position checker called successfully!!!")
        if not self.has_active_position():
            return
        
        try:
            #collect only active leg prices
            active_legs = {}
            if not self.position.get("ce_closed", False):
                active_legs['CE'] = self.position['current_prices'].get('CE', 0)
            if not self.position.get("pe_closed", False):
                active_legs['PE'] = self.position['current_prices'].get('PE', 0)

            ce_closed = self.position.get("ce_closed", False)
            pe_closed = self.position.get("pe_closed", False)
            waiting_for_replacement = self.position.get("waiting_for_replacement", False)

            # Check if we're waiting for replacement and handle timeout
            if waiting_for_replacement:
                replacement_cycles = self.position.get("replacement_cycles", 0)
                if replacement_cycles >= 2:
                    self.logger.info(f"REPLACEMENT TIMEOUT - No replacement found after {replacement_cycles} cycles, squaring off trade")
                    self.close_position("all")
                    return
                else:
                    self.logger.info(f"WAITING FOR REPLACEMENT - Cycle {replacement_cycles}/2, skipping price check")
                    return

            #Adjustment logic requires both legs to be active
            if len(active_legs) < 2:
                self.logger.debug("Adjustment check skipped - one or both legs already closed")
                # Reset waiting state if both legs are closed
                if waiting_for_replacement:
                    self.position["waiting_for_replacement"] = False
                    self.position["replacement_cycles"] = 0
                    self.save_position()
                return
            
            ce_price = self.position['current_prices'].get('CE', 0)
            pe_price = self.position['current_prices'].get('PE', 0)
            

            # ADD DETAILED LOGGING
            self.logger.debug(f"ADJUSTMENT CHECK - CE: {ce_price}, PE: {pe_price}")
            
            # Special handling when one option has price of 0
            if ce_price == 0 or pe_price == 0:
                self.logger.info(f"REPLACEMENT NEEDED - One option has price of 0 (CE: {ce_price}, PE: {pe_price})")
                
                # Determine which leg needs replacement
                if ce_price == 0 and pe_price > 0:
                    self.logger.info(f"CE option priced at 0, keeping PE at {pe_price} open, searching for CE replacement")
                    # Mark CE as effectively closed for profit calculations
                    self.position["ce_effectively_closed"] = True
                elif pe_price == 0 and ce_price > 0:
                    self.logger.info(f"PE option priced at 0, keeping CE at {ce_price} open, searching for PE replacement")
                    # Mark PE as effectively closed for profit calculations
                    self.position["pe_effectively_closed"] = True
                else:
                    # Both are 0 - this shouldn't happen, but handle gracefully
                    self.logger.warning("Both options have price of 0 - closing all")
                    self.close_position('all')
                    return
                
                # Set waiting state and increment cycles
                self.position["waiting_for_replacement"] = True
                self.position["replacement_cycles"] = self.position.get("replacement_cycles", 0) + 1
                self.position["last_replacement_attempt"] = datetime.now().isoformat()
                self.save_position()

                # Find replacement option for the 0-price leg
                self.logger.info("Searching for replacement option...")
                self.find_replacement_option()
                return
            
            # Check if one option is below 100
            below_100 = ce_price < 100 or pe_price < 100
            price_diff = abs(ce_price - pe_price)
            
            self.logger.debug(f"ADJUSTMENT CHECK - Below 100: {below_100}, Price diff: {price_diff}")
            
            if below_100:
                self.logger.info(f"ADJUSTMENT TRIGGER - One option below 100 (CE: {ce_price}, PE: {pe_price})")
                
                if self.position["ce_effectively_closed"] or self.position["pe_effectively_closed"]:
                    self.logger.info("One leg is effectively closed, skipping further adjustment to avoid compounding.")
                    return

                
                if price_diff > 18 and not self.position.get("ce_effectively_closed", False) and not self.position.get("pe_effectively_closed", False):
                    self.logger.info(f"ADJUSTMENT EXECUTING - Price difference {price_diff} >  18 threshold")
                    
                    # Close the higher priced option
                    if ce_price > pe_price:
                        self.logger.info(f"Closing CE option at {ce_price} (higher than PE {pe_price})")
                        self.close_position('ce')
                    else:
                        self.logger.info(f"Closing PE option at {pe_price} (higher than CE {ce_price})")
                        self.close_position('pe')

                    # Set waiting state and increment cycles
                    self.position["waiting_for_replacement"] = True
                    self.position["replacement_cycles"] = self.position.get("replacement_cycles", 0) + 1
                    self.position["last_replacement_attempt"] = datetime.now().isoformat()
                    self.save_position()

                    # Find replacement option
                    self.logger.info("Searching for replacement option...")
                    self.find_replacement_option()
                else:
                    self.logger.info(f"ADJUSTMENT SKIPPED - Price difference {price_diff} <= 18 threshold or either of options effectively closed..")
            else:
                self.logger.debug(f"ADJUSTMENT NOT NEEDED - Both options above 100")

        except Exception as e:
            self.logger.error(f"Error in position adjustment : {e}")

    def calculate_current_profit(self):
        """Calculate current profit considering all adjustments - FIXED VERSION"""
        self.logger.debug("Calculate Profit function successfully Called!!")
        if not self.has_active_position():
            return 0 
        
        try:
            # Calculate profit only for OPEN legs
            entry_total = 0
            current_total = 0
            
            # Only include CE if it's not closed and not effectively closed
            if not self.position.get("ce_closed", False) and not self.position.get("ce_effectively_closed", False):
                ce_entry = self.position['entry_prices'].get('ce') or self.position['entry_prices'].get('CE', 0)
                ce_current = self.position['current_prices'].get('ce') or self.position['current_prices'].get('CE', 0)
                entry_total += ce_entry
                current_total += ce_current
                self.logger.debug(f"CE (open): entry={ce_entry}, current={ce_current}")
            else:
                self.logger.debug("CE leg is closed or effectively closed - excluded from profit calculation")
                
            # Only include PE if it's not closed and not effectively closed
            if not self.position.get("pe_closed", False) and not self.position.get("pe_effectively_closed", False):
                pe_entry = self.position['entry_prices'].get('pe') or self.position['entry_prices'].get('PE', 0)
                pe_current = self.position['current_prices'].get('pe') or self.position['current_prices'].get('PE', 0)
                entry_total += pe_entry
                current_total += pe_current
                self.logger.debug(f"PE (open): entry={pe_entry}, current={pe_current}")
            else:
                self.logger.debug("PE leg is closed or effectively closed - excluded from profit calculation")

            # Base profit from open positions only
            current_profit = entry_total - current_total

            # Subtract adjustment costs (losses from closed positions)
            net_profit = current_profit - self.adjustment_impact

            self.logger.debug(f"Open legs - Entry total: {entry_total}, Current total: {current_total}")
            self.logger.debug(f"Gross profit (open legs): {current_profit}")
            self.logger.debug(f"Total adjustment impact: {self.adjustment_impact}")
            self.logger.debug(f"Net profit: {net_profit}")

            return net_profit
            
        except Exception as e:
            self.logger.error(f"Error in calculate_current_profit: {e}")
            return 0
        
    """
    def check_profit_target(self):
        #Rule 1: Check if profit target(16pts) is reached
        if not self.has_active_position():
            return False
        
        try:
            profit = self.calculate_current_profit()

            if profit >= 18:
                self.logger.info(f"PROFIT TARGET REACHED : {profit} points")
                self.close_position("all")
                return True
            
            return False
        except Exception as e:
            self.logger.error(f"Error checking profit target : {e}")
            return False
        """
    
    def update_current_prices(
        self,
        options_data: Optional[List[Dict]] = None,
        snapshot_timestamp: Optional[str] = None,
    ) -> Dict:
        """Update prices for open legs and report whether quotes were updated, unchanged, or missing."""
        try:
            if not self.has_active_position():
                return {
                    "status": "no_position",
                    "updated": False,
                    "unchanged": False,
                    "missing_symbols": [],
                    "found_symbols": [],
                    "changed_symbols": [],
                    "snapshot_timestamp": snapshot_timestamp,
                    "snapshot_expiries": [],
                    "is_fresh": False,
                }
            
            ce_sym = self.position.get("ce_symbol")
            pe_sym = self.position.get("pe_symbol")
            
            if options_data is None:
                snapshot = fetch_latest_delta_snapshot(
                    self.symbol,
                    required_contracts=self._get_active_required_contracts(),
                    refresh_if_missing=True,
                )
                options_data = snapshot.get("options_data", [])
                snapshot_timestamp = snapshot.get("timestamp")
            if not options_data:
                self.logger.warning("update_current_prices has no options_data available.")
                return {
                    "status": "missing",
                    "updated": False,
                    "unchanged": False,
                    "missing_symbols": [sym for sym in [ce_sym, pe_sym] if sym],
                    "found_symbols": [],
                    "changed_symbols": [],
                    "snapshot_timestamp": snapshot_timestamp,
                    "snapshot_expiries": [],
                    "is_fresh": False,
                }
            
            symbol_map = {o.get("tradingsymbol"): o for o in options_data if o.get("tradingsymbol")}
            snapshot_expiries = sorted({
                str(o.get("expiry") or o.get("expiry_date"))
                for o in options_data
                if o.get("expiry") or o.get("expiry_date")
            })
            
            if not ce_sym or not pe_sym:
                self.logger.warning("Position missing ce_symbol/pe_symbol; cannot update.")
                return {
                    "status": "error",
                    "updated": False,
                    "unchanged": False,
                    "missing_symbols": [sym for sym in [ce_sym, pe_sym] if not sym],
                    "found_symbols": [],
                    "changed_symbols": [],
                    "snapshot_timestamp": snapshot_timestamp,
                    "snapshot_expiries": snapshot_expiries,
                    "is_fresh": False,
                }

            updated = False
            found_symbols = []
            missing_symbols = []
            changed_symbols = []

            # Update CE ONLY if it's not closed
            if not self.position.get("ce_closed", False):
                ce_data = symbol_map.get(ce_sym)
                if ce_data:
                    found_symbols.append(ce_sym)
                    raw_price = ce_data.get("last_price") or ce_data.get("ltp")
                    try:
                        new_price = float(raw_price) if raw_price is not None else None
                    except Exception:
                        new_price = None
                    if new_price is not None:
                        old_price = self.position.get("current_prices", {}).get("ce")
                        if old_price != new_price:
                            updated = True
                            changed_symbols.append(ce_sym)
                        self.position.setdefault("current_prices", {})["ce"] = new_price
                        self.position["current_prices"]["CE"] = new_price
                    else:
                        missing_symbols.append(ce_sym)
                else:
                    missing_symbols.append(ce_sym)
            else:
                # Ensure closed CE is not in current_prices
                if "current_prices" in self.position:
                    self.position["current_prices"].pop("ce", None)
                    self.position["current_prices"].pop("CE", None)

            # Update PE ONLY if it's not closed  
            if not self.position.get("pe_closed", False):
                pe_data = symbol_map.get(pe_sym)
                if pe_data:
                    found_symbols.append(pe_sym)
                    raw_price = pe_data.get("last_price") or pe_data.get("ltp")
                    try:
                        new_price = float(raw_price) if raw_price is not None else None
                    except Exception:
                        new_price = None
                    if new_price is not None:
                        old_price = self.position.get("current_prices", {}).get("pe")
                        if old_price != new_price:
                            updated = True
                            changed_symbols.append(pe_sym)
                        self.position.setdefault("current_prices", {})["pe"] = new_price
                        self.position["current_prices"]["PE"] = new_price
                    else:
                        missing_symbols.append(pe_sym)
                else:
                    missing_symbols.append(pe_sym)
            else:
                # Ensure closed PE is not in current_prices
                if "current_prices" in self.position:
                    self.position["current_prices"].pop("pe", None)
                    self.position["current_prices"].pop("PE", None)

            if updated:
                self.save_position()
                self.logger.debug("Position prices updated (open legs only).")

            parsed_snapshot_timestamp = self._parse_snapshot_timestamp(snapshot_timestamp)
            is_fresh = self.is_data_fresh(
                parsed_snapshot_timestamp,
                max_age_seconds=self.data_freshness_threshold,
            ) if parsed_snapshot_timestamp else False

            if missing_symbols:
                status = "missing"
            elif not is_fresh:
                status = "stale"
            elif updated:
                status = "updated"
            else:
                status = "unchanged"

            return {
                "status": status,
                "updated": updated,
                "unchanged": status == "unchanged",
                "missing_symbols": missing_symbols,
                "found_symbols": found_symbols,
                "changed_symbols": changed_symbols,
                "snapshot_timestamp": snapshot_timestamp,
                "snapshot_expiries": snapshot_expiries,
                "is_fresh": is_fresh,
            }

        except Exception as e:
            self.logger.exception("Error updating current prices: %s", e)
            return {
                "status": "error",
                "updated": False,
                "unchanged": False,
                "missing_symbols": [],
                "found_symbols": [],
                "changed_symbols": [],
                "snapshot_timestamp": snapshot_timestamp,
                "snapshot_expiries": [],
                "is_fresh": False,
            }
        
    def manage_existing_positions(self):
        """Manage existing positions with data freshness checks and expiry handling"""
        current_time = datetime.now()
        self.logger.info("----- Position management cycle started at %s -----", current_time.strftime("%H:%M:%S"))
        required_contracts = self._get_active_required_contracts()

        # SAFETY NET: If both legs are closed, clear the position
        if self.has_active_position():
            if self.position.get("ce_closed", False) and self.position.get("pe_closed", False):
                self.logger.warning("Both legs already closed. Clearing active position to avoid zombie state.")
                self.position = None
                self.clear_position_file()
                return


        # FIRST: Check for expired positions
        if self.is_position_expired():
            self.logger.warning("Position contains expired options - closing immediately")
            self.close_position("all")
            return

        # SECOND: Check if we should close due to expiry today
        if self.should_close_for_expiry():
            self.logger.info("Closing position - expiry day auto-close (3:15 PM)")
            # Update prices one final time before closing
            self.update_current_prices(snapshot_timestamp=(self.latest_delta_snapshot or {}).get("timestamp"))
            self.close_position("all")
            return

        # THIRD: Normal position management continues...
        # Always refresh market data 
        data_refreshed = self.refresh_all_data(
            required_contracts=required_contracts,
            refresh_if_missing=True,
        )
        if not data_refreshed:
            self.logger.warning("Skipping position management - failed to refresh data")
            return

        snapshot = self.latest_delta_snapshot or {}
        options_data = snapshot.get("options_data", [])
        snapshot_timestamp = snapshot.get("timestamp")
        missing_required_contracts = snapshot.get("missing_required_contracts", [])
        if not options_data:
            self.logger.warning("No options data available for price updates")
            return

        if missing_required_contracts:
            self.logger.warning(
                "Latest delta snapshot still missing active contracts: %s (snapshot_ts=%s)",
                missing_required_contracts,
                snapshot_timestamp,
            )

        price_status = self.update_current_prices(
            options_data,
            snapshot_timestamp=snapshot_timestamp,
        )
        current_profit = self.calculate_current_profit()
        self.logger.info(f"CURRENT NET P&L: {current_profit} points")
        self.logger.info("[PRICE UPDATE] %s", self._format_price_update_summary(price_status))

        if price_status.get("status") in {"missing", "stale", "error"}:
            self.logger.warning("Skipping position management - stale/no price data")
            return  # Skip this iteration if data is stale

        # Rule 1: Check profit target every 5min (only with fresh data)
        """
        if self.should_check_profit(current_time):
            if self.check_profit_target():
                return #Position closed
            self.last_profit_check = current_time
        """
            
        #Rule 2: Dynamic position adjustment (only with fresh data)
        self.check_positon_adjustment()

        #Rule 3 & 4 : Risk management checks every 5 min
        if self.should_check_risk(current_time):
            if self.check_vix_breach() or self.check_entry_criteria_violation():
                return #Position closed
            self.last_risk_check = current_time
            
        #Forced logging : 
        if self.has_active_position():
            ce_sym = self.position.get("ce_symbol")
            pe_sym = self.position.get("pe_symbol")
            ce_price = self.position['current_prices'].get('CE', 0)
            pe_price = self.position['current_prices'].get('PE', 0)
            net_pnl = self.calculate_current_profit()
            
            self.logger.info(
                f"[CYCLE SUMMARY] Net P&L: {net_pnl:.2f} | "
                f"CE: {ce_sym} @ {ce_price} ({'closed' if self.position.get('ce_closed') else 'open'}) | "
                f"PE: {pe_sym} @ {pe_price} ({'closed' if self.position.get('pe_closed') else 'open'})"
            )
            # Force adjustment check
            self.check_positon_adjustment()
        else:
            self.logger.info("[CYCLE SUMMARY] No active position currently available")
                
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
            ce_sym = ce_opt.get("tradingsymbol")
            pe_sym = pe_opt.get("tradingsymbol")
            ce_price = ce_opt.get("last_price")
            pe_price = pe_opt.get("last_price")
            ce_expiry = self._normalize_expiry_value(ce_opt.get("expiry_date") or ce_opt.get("expiry"))
            pe_expiry = self._normalize_expiry_value(pe_opt.get("expiry_date") or pe_opt.get("expiry"))

            if not ce_sym or not pe_sym or ce_price is None or pe_price is None:
                self.logger.error("Signal CE/PE missing 'tradingsymbol' or 'last_price' fields.")
                return False

            now_iso = datetime.now().isoformat(sep=" ")

            # Capture entry essentials and entry_time for persistence and later validation
            try:
                from core.strat_donchian import get_current_entry_criteria
                self.entry_criteria = get_current_entry_criteria(self.symbol)
            except Exception:
                # If essentials cannot be captured, leave as None but proceed with trade
                self.entry_criteria = None
            # Maintain attribute entry_time alongside position's stored string
            try:
                self.entry_time = datetime.fromisoformat(now_iso)
            except Exception:
                self.entry_time = datetime.now()

            # compact position representation (stable across saves)
            self.position = {
                "id": str(uuid.uuid4()),
                "ce_symbol": ce_sym,
                "pe_symbol": pe_sym,
                "ce_expiry": ce_expiry,
                "pe_expiry": pe_expiry,
                "entry_prices": {"ce": float(ce_price), "CE": float(ce_price), "pe": float(pe_price), "PE": float(pe_price)},
                "current_prices": {"ce": float(ce_price), "CE": float(ce_price), "pe": float(pe_price), "PE": float(pe_price)},
                "entry_time": now_iso,
                "ce_closed": False,
                "pe_closed": False,
                "ce_effectively_closed": False,
                "pe_effectively_closed": False,
                "replacement_cycles": 0,
                "last_replacement_attempt": None,
                "waiting_for_replacement": False,
            }

            # initialize adjustment containers if missing in object model
            self.adjustment_history = []
            self.adjustment_impact = 0.0
            

            # persist once
            self.save_position()

            # concise logging
            self.logger.info("NEW POSITION OPENED:")
            self.logger.info(f"CE: {ce_sym} at {ce_price}")
            self.logger.info(f"PE: {pe_sym} at {pe_price}")
            self.logger.info(f"Total entry points: {float(ce_price) + float(pe_price)}")
            self.logger.info(f"Position opened at: {now_iso}")
            self.logger.info("TRADE EXECUTION SUCCESSFUL")

            self.trade_logger.info("NEW POSITION OPENED:")
            self.trade_logger.info(f"CE: {ce_sym} at {ce_price}")
            self.trade_logger.info(f"PE: {pe_sym} at {pe_price}")
            self.trade_logger.info(f"Total entry points: {float(ce_price) + float(pe_price)}")
            self.trade_logger.info(f"Position opened at: {now_iso}")
            self.trade_logger.info("TRADE EXECUTION SUCCESSFUL")


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
    trader = PaperTraderDonchian("NIFTY50")
    print("Paper trader initialized....")
    trader.main_trading_loop() # this runs continuously
