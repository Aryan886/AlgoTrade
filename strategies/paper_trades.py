import pandas as pd 
import logging
from datetime import datetime 
import time
from typing import Dict, List, Tuple, Optional
from strategies.strategy import donchian_ao_strategy
from utils.db_func import (
    fetch_latest_delta_data, 
    fetch_vix_data,
)
logging.basicConfig(filename="logs/paper_trades.log", level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

class PaperTrader:
    def __init__(self, symbol="NIFTY50"):
        self.symbol = symbol
        self.position = None,
        self.adjustment_history = [],
        self.adjustment_cost = 0,
        self.entry_criteria = None,
        self.last_risk_check = None,
        self.last_profit_check = None,
        self.entry_time = None

    @property
    def total_adjustment_costs(self):
        """Property to get total adjustment costs"""
        return self.adjustment_cost

    def  main_trading_loop(self):
        """ Run continuously in the background during Trading hours """
        logging.info("Starting paper trading.....")

        while self.is_market_open():
            try:
                current_time = datetime.now()

                #Check for new signals if no active position
                if not self.has_active_position():
                    signal = donchian_ao_strategy(self.symbol)
                    if signal:
                        self.execute_paper_trade(signal)

                #Manage existing position
                if self.has_active_position():
                    self.manage_existing_positions()
                
                #Sleep for 1min before next iteration
                time.sleep(60)

            except Exception as e:
                logging.error(f"Encountered this error : {e}")
                time.sleep(60)

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
        """Checks for profit (every 1min)"""
        if not self.last_profit_check:
            return True
        return (current_time - self.last_profit_check).total_seconds() > 300
    
    def close_position(self, reason):
        """Close active position(completely)"""
        if not self.has_active_position():
            return
        
        try:
            final_profit = self.calculate_current_profit()

            logging.info(f"POSITION CLOSED - Reason: {reason}")
            logging.info(f"Final P&L: {final_profit} points")
            logging.info(f"Total adjustment costs: {self.total_adjustment_costs}")
            logging.info(f"Position duration: {datetime.now() - self.entry_time}")

            #Resets position
            self.position = None
            self.entry_time = None
            self.entry_criteria = None
            self.adjustment_history = []
            self.adjustment_cost = 0 

        except Exception as e:
            logging.error(f"Error closing position: {e}")
    
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
                        logging.info(f"ENTRY CRITERIA VIOLATION : {key} changed from {self.entry_criteria[key]} to {current_criteria.get(key)}")
                        self.close_position("Entry criteria violation")
                        return True
                    
            return False
        except Exception as e:
            logging.error(f"Error checking entry criteria : {e}")
            return False
        
    def check_vix_breach(self):
        """Rule 3 : Check if VIX breached mid-donchian"""
        try:
            from strategies.indicators import add_donchian_channel

            vix_data = fetch_vix_data("VIX")
            if vix_data.empty:
                return False
            
            vix_data = add_donchian_channel(vix_data, period=20, suffix="_vix")
            latest_vix = vix_data.iloc[-1]

            if latest_vix['Close'] > latest_vix['Donchian_Mid_vix']:
                logging.info(f"VIX BREACH : Closing position due to high VIX")
                self.close_position("VIX Breach")
                return True
            
            return False
        
        except Exception as e:
            logging.error(f"Error checking vix breach : {e}")
            return False
        
    def find_replacement_option(self):
        """Find replacement option based on delta(adjusting)"""
        try: 
            options_data = fetch_latest_delta_data(self.symbol)
            if not options_data:
                return
            
            #Determine which option we STILL have
            remaining_option = None
            if self.position['ce_option']:
                remaining_option = 'CE'
                remaining_price = self.position['current_prices']['CE']
                remaining_delta = self.position['ce_option']['delta']
                target_type = 'PE'

            elif self.position['pe_option']:
                remaining_option = 'PE'
                remaining_price = self.position['current_prices']['PE']
                remaining_delta = self.position['pe_option']['delta']
                target_type = 'CE'
            else:
                return 
            
            #Find replacement option
            candidates = []
            for opt in options_data:
                if opt['option_type'] == target_type:
                    price_diff = abs(opt['ltp'] - remaining_price)
                    delta_diff = abs(abs(opt['delta']) - abs(remaining_delta))

                    if price_diff <= 15 and delta_diff <= 6:
                        candidates.append(opt)
            
            if candidates:
                #Select best candidate (closest delta match)
                best_candidate = min(candidates, key=lambda x: abs(abs(x['delta']) - abs(remaining_delta)))

                #Add new option to position
                if target_type == 'CE':
                    self.position['ce_option'] = best_candidate
                else:
                    self.position['pe_option'] = best_candidate

                self.position['entry_prices'][target_type] = best_candidate['ltp']
                self.position['current_prices'][target_type] = best_candidate['ltp']

                logging.info(f"Added replacement {target_type} : {best_candidate['tradingsymbol']} at {best_candidate['ltp']}")

        except Exception as e : 
            logging.error(f"Error finding replacement option : {e}")
            
    def close_option(self, option_type):
        """Close specific option (CE/PE) and add adjustment costs"""
        if option_type in self.position['current_prices']:
            close_price = self.position['current_prices'][option_type]
            entry_price = self.position['entry_prices'][option_type]

            #Add adjustment cost (loss from closing respective option)
            adjustment_cost = max(0, (close_price - entry_price))
            self.adjustment_cost += adjustment_cost

            #Remmove from position 
            if option_type == 'CE':
                self.position['ce_option'] = None

            else:
                self.position['pe_option'] = None

            del self.position['current_prices'][option_type]
            del self.position['entry_prices'][option_type]

            self.adjustment_history.append({
                'timestamp': datetime.now(),
                'action' : f'Closed {option_type}',
                'price' : close_price,
                'cost': adjustment_cost
            })

    def check_positon_adjustment(self):
        """Rule 2: Check if position adjustment is needed"""
        if not self.has_active_position():
            return
        
        try:
            ce_price = self.position['current_prices'].get('CE', 0)
            pe_price = self.position['current_prices'].get('PE', 0)

            #Check if one option is below 100
            if ce_price < 100  or pe_price < 100:
                price_diff = abs(ce_price - pe_price)

                if price_diff > 18:
                    #Close the higher priced option
                    if ce_price > pe_price:
                        self.close_option('CE')
                        logging.info(f"Close CE option at {ce_price} due to adjustment criteria")
                    
                    else:
                        self.close_option('PE')
                        logging.info(f"Close PE option at {pe_price} due to adjustment criteria")

                    #Find replacement option
                    self.find_replacement_option()

        except Exception as e:
            logging.error(f"Error in position adjustment : {e}")


    def calculate_current_profit(self):
        """Calculate current profit considering all adjustmetns"""
        if not self.has_active_position():
            return 0 
        
        #Base profit calculation
        entry_total = sum(self.position['entry_prices'].values())
        current_total = sum(self.position['current_prices'].values())
        current_profit = entry_total - current_total

        #Subtract adjustment costs
        net_profit = current_profit - self.total_adjustment_costs

        return net_profit
    
    def check_profit_target(self):
        """Rule 1: Check if profit target(16pts) is reached """
        if not self.has_active_position():
            return False
        
        try:
            profit = self.calculate_current_profit()

            if profit >= 16:
                logging.info(f"PROFIT TARGET REACHED : {profit} points")
                self.close_position("Profit target reached")
                return True
            
            return False
        except Exception as e:
            logging.error(f"Error checking profit target : {e}")
            return False
        
    def update_current_prices(self):
        """Update current ooption prices"""
        if not self.has_active_position():
            return 
        
        try: 
            options_data = fetch_latest_delta_data(self.symbol)
            if not options_data:
                return 
            
            #Update CE price
            if self.position['ce_option']:
                ce_symbol = self.position['ce_option']['tradingsymbol']
                for opt in options_data:
                    if opt['tradingsymbol'] == ce_symbol:
                        self.position['current_prices']['CE'] = opt['ltp']
                        break

            #Update PE price
            if self.position['pe_option']:
                pe_symbol = self.position['pe_option']['tradingsymbol']
                for opt in options_data:
                    if opt['tradingsymbol'] == pe_symbol:
                        self.position['current_prices']['PE'] = opt['ltp']
                        break
                
        except Exception as e:
            logging.error(f"Error updating current prices : {e}")

    def manage_existing_positions(self):
        """Mnage exisiting positions according to profit booking rule"""
        current_time = datetime.now()

        #Update current prices
        self.update_current_prices()

        #Rule 1: Check profit target every min(Universal override rule)
        if self.should_check_profit(current_time):
            if self.check_profit_target():
                return #Position closed
            self.last_profit_check = current_time
            
        #Rule 2: Dynamic position adjustment
        self.check_positon_adjustment()

        #Rule 3 & 4 : Risk management checks every 5 min
        if self.should_check_risk(current_time):
            if self.check_vix_breach() or self.check_entry_criteria_violation():
                return #Position closed
            self.last_risk_check = current_time
            
    def execute_paper_trade(self, signal):
        """Execute a new paper trade based on strategy signal"""
        if signal is None:
            return
        
        try:
            #Extract options from signal (assuming strategy returns first_leg, second_leg)
            if isinstance(signal, tuple) and len(signal) == 2:
                first_leg, second_leg = signal
                options = [first_leg, second_leg]

            else:
                logging.warning("Invalid signal format received")
                return 
            
            #Create position
            self.position = {
                'ce_option': None,
                'pe_option': None,
                'entry_prices': {},
                'current_prices': {},
                'entry_time': datetime.now()
            }

            #Classify and store options
            for option in options:
                if option['option_type'] == 'CE':
                    self.position['ce_option'] = option
                    self.position['entry_prices']['CE'] = option['ltp']
                    self.position['current_prices']['CE'] = option['ltp']
                elif option['option_type'] == 'PE':
                    self.position['pe_option'] = option
                    self.position['entry_prices']['PE'] = option['ltp']
                    self.position['current_prices']['PE'] = option['ltp']

            # Store entry criteria for validation
            # Note: You'll need to modify your strategy to return entry criteria
            from strategies.strategy import get_current_entry_criteria
            self.entry_criteria = get_current_entry_criteria(self.symbol)
            self.entry_time = datetime.now()
            self.last_profit_check = datetime.now()
            self.last_risk_check = datetime.now()
            
            logging.info(f"NEW POSITION OPENED:")
            logging.info(f"CE: {self.position['ce_option']['tradingsymbol']} at {self.position['entry_prices']['CE']}")
            logging.info(f"PE: {self.position['pe_option']['tradingsymbol']} at {self.position['entry_prices']['PE']}")
            logging.info(f"Total entry points: {sum(self.position['entry_prices'].values())}")
            
        except Exception as e:
            logging.error(f"Error executing paper trade: {e}")
    
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
                'pe_price': self.position['current_prices'].get('PE')
            }
        except Exception as e:
            return f"Error getting status: {e}"

if __name__ == "__main__":
    trader = PaperTrader("NIFTY50")
    print("Paper trader initialized....")
    trader.main_trading_loop() #this runs continuously