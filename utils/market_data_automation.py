import schedule
import time
import threading
import logging
from datetime import datetime, timedelta
from utils.data_fetcher import fetch_and_save_data
from utils.vix_fetcher import calculate_and_store_vix
from utils.db_func import store_market_data, store_signal
from utils.db_func import  calculate_and_store_high_accuracy_delta
from core.indicators import compute_indicators, generate_signals
import os
from typing import Optional, Dict
from broker.zerodha_client import kite_from_saved_token
from core.strat_donchian import donchian_ao_strategy
from core.paper_trades import PaperTraderDonchian
import argparse
import json

#Generate signal function at line 220s generate_pdf is currently stopped


# Set up logging with rotation
from logging.handlers import RotatingFileHandler

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Set up logging with rotation (max 10MB per file, keep 5 files)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('logs/market_data_automation.log', maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Set up logging for trades
logging.basicConfig(filename='logs/trade_actions.log',
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')


def log_trade_action(first_leg, second_leg):
    if first_leg and second_leg:
        delta_diff = abs(abs(first_leg['delta']) - abs(second_leg['delta']))
        msg = (f"TRADE TAKEN: {first_leg['tradingsymbol']} (Delta: {first_leg['delta']}) & "
               f"{second_leg['tradingsymbol']} (Delta: {second_leg['delta']}) | "
               f"Delta Difference: {delta_diff}")
        print(msg)
        logging.info(msg)
    else:
        msg = "NO TRADE: No suitable option pair found."
        print(msg)
        logging.info(msg)

def calculate_vix_separately():
    """Calculate VIX independently of market data fetching"""
    try:
        logger.info("Starting separate VIX calculation...")
        vix_value = calculate_and_store_vix("NIFTY50")
        if vix_value:
            logger.info(f"VIX calculated separately: {vix_value:.2f}")
        else:
            logger.warning("Separate VIX calculation failed")
    except Exception as e:
        logger.error(f"Error in separate VIX calculation: {e}")

def calculate_high_accuracy_delta():
    """Calculate high-accuracy delta using Black-Scholes formula"""
    try:
        logger.info("Starting high-accuracy delta calculation...")
        delta_data = calculate_and_store_high_accuracy_delta("NIFTY50")
        if delta_data:
            spot_price = delta_data.get('spot_price', 0)
            strike_band = delta_data.get('strike_band', [])
            logger.info(f"High-accuracy delta calculated: Spot={spot_price}, Strikes={strike_band}")
        else:
            logger.warning("High-accuracy delta calculation failed")
    except Exception as e:
        logger.error(f"Error in high-accuracy delta calculation: {e}")

def generate_and_store_signals(df, symbol="NIFTY50"):
    """Generate and store trading signals"""
    try:
        logger.info("Generating trading signals...")
        signals = generate_signals(df, symbol)
        
        for signal in signals:
            try:
                store_signal(
                    timestamp=signal['timestamp'],
                    symbol=signal['symbol'],
                    signal=signal['signal'],
                    reason=signal['reason'],
                    confidence_score=signal['confidence_score']
                )
                #logger.info(f"Signal stored: {signal['signal']} for {signal['symbol']} - {signal['reason']}")
            except Exception as e:
                logger.error(f"Failed to store signal: {e}")
        
        if signals:
            logger.info(f"Generated {len(signals)} trading signals")
        else:
            logger.info("No trading signals generated")
            
    except Exception as e:
        logger.error(f"Error in signal generation: {e}")

def run_trading_strategy():
    """Execute the main trading strategy and log the outcome."""
    try:
        logger.info("Running Donchian AO trading strategy...")
        result = donchian_ao_strategy(symbol="NIFTY50")
        if isinstance(result, tuple) and len(result) == 2:
            first_leg, second_leg = result
            log_trade_action(first_leg, second_leg)
        else:
            log_trade_action(None, None) # Log that no trade was taken
    except Exception as e:
        logger.error(f"Error running trading strategy: {e}")


class MarketDataAutomation:
    def __init__(self):
        self.is_running = False
        self.scheduler_thread = None
        self.market_hours = {
            'start': '09:15',
            'end': '15:30'
        }

        self.paper_trader = PaperTraderDonchian("NIFTY50")
        logger.info("Paper trader successfully initialised....")
        
        # Market holidays for 2025 (you can update this list)
        self.market_holidays_2025 = [
            '2025-01-26',  # Republic Day
            '2025-03-07',  # Holi
            '2025-04-02',  # Ram Navami
            '2025-04-14',  # Dr Ambedkar Jayanti
            '2025-05-01',  # Maharashtra Day
            '2025-08-15',  # Independence Day
            '2025-10-02',  # Mahatma Gandhi Jayanti
            '2025-11-14',  # Diwali
            '2025-12-25',  # Christmas
        ]
        
        # Track last successful fetch times to avoid duplicate calls
        self.last_fetch_times: Dict[str, Optional[datetime]] = {
            '1m': None,
            '5m': None,
            '15m': None
        }
        
        # Minimum intervals between fetches to avoid API rate limits
        self.min_intervals = {
            '1m': 60,    # 1 minute
            '5m': 300,   # 5 minutes
            '15m': 900   # 15 minutes
        }
    
    def is_market_holiday(self, date=None):
        """Check if the given date is a market holiday"""
        if date is None:
            date = datetime.now()
        
        date_str = date.strftime('%Y-%m-%d')
        return date_str in self.market_holidays_2025
    
    def is_market_open(self):
        """Check if market is currently open"""
        now = datetime.now()
        
        if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False
            
        if self.is_market_holiday(now):
            return False
            
        current_time = now.time()
        market_start = datetime.strptime(self.market_hours['start'], '%H:%M').time()
        market_end = datetime.strptime(self.market_hours['end'], '%H:%M').time()
        
        return market_start <= current_time <= market_end
    
    def should_fetch_data(self, interval):
        """Check if we should fetch data based on last fetch time and minimum interval"""
        last_fetch = self.last_fetch_times[interval]
        if last_fetch is None:
            return True
            
        time_since_last = (datetime.now() - last_fetch).total_seconds()
        return time_since_last >= self.min_intervals[interval]
    
    def fetch_data_with_retry(self, interval, max_retries=3):
        """Fetch data with retry logic - INCLUDES signal generation"""
        if not self.is_market_open():
            logger.info(f"Market is closed, skipping {interval} data fetch")
            return None
            
        if not self.should_fetch_data(interval):
            logger.debug(f"Skipping {interval} fetch - too soon since last fetch")
            return None
            
        for attempt in range(max_retries):
            try:
               # logger.info(f"Fetching {interval} data (attempt {attempt + 1}/{max_retries})...")
                results, df = fetch_and_save_data(intervals=[interval], return_interval=interval)
                
                if df is not None and not df.empty:
                    self.last_fetch_times[interval] = datetime.now()
                    logger.info(f"Successfully fetched {len(df)} rows of {interval} data")
                    
                    # Store market data WITHOUT VIX (VIX will be calculated separately)
                    store_market_data(df, symbol="NIFTY50", interval=interval)
                    logger.info(f"Stored {interval} market data successfully")
                    
                    # Generate and store signals
                    #generate_and_store_signals(df, "NIFTY50")
                    
                    return df
                else:
                    logger.warning(f"Failed to fetch {interval} data - empty result")
                    
            except Exception as e:
                logger.error(f"Error fetching {interval} data (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(5 * (attempt + 1))  # Exponential backoff
                    
        logger.error(f"Failed to fetch {interval} data after {max_retries} attempts")
        return None
    
    def fetch_1m_data(self):
        """Fetch 1-minute data"""
        return self.fetch_data_with_retry('1m')
    
    def fetch_5m_data(self):
        """Fetch 5-minute data"""
        return self.fetch_data_with_retry('5m')
    
    def fetch_15m_data(self):
        """Fetch 15-minute data"""
        return self.fetch_data_with_retry('15m')
    
    def run_paper_trading_cycle(self):
        """Run one cycle of paper trading logic"""
        if not self.is_market_open():
            return
            
        try:
            logger.info("Running paper trading cycle...")
            
            # Check for new signals if no active position
            if not self.paper_trader.has_active_position():
                signal = donchian_ao_strategy(self.paper_trader.symbol)
                if signal:
                    logger.info("New trading signal received, executing paper trade")
                    # Add proper error handling and check return value
                    try:
                        success = self.paper_trader.execute_paper_trade(signal)
                        if success:
                            logger.info(" Paper trade executed successfully")
                        else:
                            logger.error(" Paper trade execution failed")
                    except Exception as trade_error:
                        logger.error(f" Exception during paper trade execution: {trade_error}")
                else:
                    logger.debug("No new trading signals")

            # Manage existing position
            if self.paper_trader.has_active_position():
                logger.info(" Managing existing positions")  # Changed from DEBUG to INFO
                try:
                    self.paper_trader.manage_existing_positions()
                    
                    # Log current position status
                    status = self.paper_trader.get_position_status()
                    if isinstance(status, dict):
                        logger.info(f" Position Status - P&L: Rs{status.get('current_profit', 0):.2f}, "
                                f"Adjustments: {status.get('adjustments_count', 0)}")
                except Exception as manage_error:
                    logger.error(f" Error managing positions: {manage_error}")
            else:
                logger.debug("No active position to manage")
                
        except Exception as e:
            logger.error(f" Error in paper trading cycle: {e}")

    def get_paper_trading_summary(self):
        """Get detailed paper trading summary"""
        try:
            status = self.paper_trader.get_position_status()

            if isinstance(status, dict) and status.get('position_active'):
                return{
                    'active_position':True,
                    'entry_time': status['entry_time'].strftime('%Y-%m-%d %H:%M:%S') if status['entry_time'] else None,
                    'current_profit': round(status['current_profit'], 2),
                    'adjustment_costs' : round(status['adjustment_costs'], 2),
                    'adjustments_count': status['adjustments_count'],
                    'ce_price': status['ce_price'],
                    'pe_price': status['pe_price'],
                    'total_adjustments': len(self.paper_trader.adjustment_history),
                    'position_duration': str(datetime.now() - status['entry_time'])
                }
            
            else:
                return {'active_position': False, 'message': 'No active position'}
            
        except Exception as e:
            return {'error': f'Error getting paper trading status : {e}'}
        

    def setup_schedule(self):
        """Setup the schedule for data fetching"""
        # Clear any existing schedules
        schedule.clear()
        
        # Market data fetching (every minute/5min/15min)
        schedule.every().minute.do(self.fetch_1m_data)
        schedule.every(5).minutes.do(self.fetch_5m_data)
        schedule.every(15).minutes.do(self.fetch_15m_data)
        
        # VIX calculation (every 5 minutes, independent of market data)
        schedule.every(5).minutes.do(calculate_vix_separately)
        
        # High-accuracy delta calculation (for delta neutral strategies)
        schedule.every().minute.do(calculate_high_accuracy_delta)
        
        # Run the main trading strategy
        schedule.every().minute.do(run_trading_strategy)

        #PAPER TRADING - Run every minute during market hours
        schedule.every().minute.do(self.run_paper_trading_cycle)

        logger.info("Schedule setup completed:")
        logger.info("- 1m data: Every minute (during market hours)")
        logger.info("- 5m data: Every 5 minutes (during market hours)")
        logger.info("- 15m data: Every 15 minutes (during market hours)")
        logger.info("- VIX calculation: Every 5 minutes (independent)")
        logger.info("- High-accuracy delta: Every minute (Black-Scholes)")
        logger.info("- Trading Strategy: Every minute")
        logger.info("- Signal generation: Integrated with data fetching")
    
    def run_scheduler(self):
        """Run the scheduler in a separate thread"""
        logger.info("Starting market data automation...")
        self.is_running = True
        
        while self.is_running:
            try:
                schedule.run_pending()
                time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
                break
            except Exception as e:
                logger.error(f"Error in scheduler: {e}")
                time.sleep(5)
        
        logger.info("Market data automation stopped")
    
    def start(self):
        """Start the market data automation"""
        if self.is_running:
            logger.warning("Market data automation is already running")
            return
        
        # Test connection first
        if not self.test_connection():
            logger.error("Connection test failed. Please check your Kite API credentials.")
            return
        
        # Setup schedule
        self.setup_schedule()
        
        # Start scheduler in a separate thread
        self.scheduler_thread = threading.Thread(target=self.run_scheduler)
        self.scheduler_thread.daemon = False
        self.scheduler_thread.start()
        
        logger.info("Market data automation started successfully")
        logger.info("Press Ctrl+C to stop the automation")
    
    def stop(self):
        """Stop the market data automation"""
        logger.info("Stopping market data automation...")
        self.is_running = False

        if self.paper_trader.has_active_position():
            logger.info("Closing paper trading position due to system shutdown")
            self.paper_trader.close_position("all")

        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=5)
        logger.info("Market data automation stopped")
    
    def get_status(self):
        """Get the current status of the automation"""
        paper_status = self.paper_trader.get_position_status()
        return {
            'is_running': self.is_running,
            'market_open': self.is_market_open(),
            'last_fetch_times': self.last_fetch_times,
            'paper_trading_status': paper_status
        }
    
    def test_connection(self):
        """Test the Kite API connection"""
        try:
            logger.info("Testing Kite API connection...")
            kite = kite_from_saved_token()
            if kite:
                # Test instruments fetch
                instruments = kite.instruments("NSE")
                logger.info(f"Connection test successful! Kite API is accessible. Got {len(instruments)} instruments.")
                return True
            else:
                logger.error("Failed to connect to Kite API")
                return False
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False


    def print_status_report(automation):
        """Print a formatted status report"""
        print("\n" + "="*60)
        print(" MARKET DATA & PAPER TRADING STATUS")
        print("="*60)
        
        # General Status
        general_status = automation.get_status()
        print(f"System Running: {' YES' if general_status['is_running'] else ' NO'}")
        print(f"Market Open: {' YES' if general_status['market_open'] else ' NO'}")
        print(f"Current Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Data Fetch Status
        print(f"\n LAST DATA FETCH TIMES:")
        for interval, last_time in general_status['last_fetch_times'].items():
            if last_time:
                time_str = last_time.strftime('%H:%M:%S')
                time_ago = (datetime.now() - last_time).total_seconds() / 60
                print(f"   {interval}: {time_str} ({time_ago:.1f} min ago)")
            else:
                print(f"   {interval}: Never")
        
        # Paper Trading Status
        print(f"\n💼 PAPER TRADING STATUS:")
        paper_status = automation.get_paper_trading_summary()
        
        if paper_status.get('active_position'):
            print(f" Position:  ACTIVE")
            print(f" Current P&L: Rs{paper_status['current_profit']}")
            print(f"   Entry Time: {paper_status['entry_time']}")
            print(f" Duration: {paper_status['position_duration']}")
            print(f" Adjustments: {paper_status['adjustments_count']}")
            print(f" Adjustment Costs: Rs{paper_status['adjustment_costs']}")
            if paper_status['ce_price']:
                print(f" CE Price: Rs{paper_status['ce_price']}")
            if paper_status['pe_price']:
                print(f" PE Price: Rs{paper_status['pe_price']}")
        else:
            print(f" Position:  NO ACTIVE POSITION")
            print(f" Status: Waiting for trading signals...")
        
        if paper_status.get('error'):
            print(f"     Error: {paper_status['error']}")
        
        print("="*60)
        print()

def main():
    """Main function to run the market data automation + command line support"""
    parser = argparse.ArgumentParser(description='Market Data Automation with Paper Trading')
    parser.add_argument('--status', '-s', action='store_true', 
                       help='Show current status and exit')
    parser.add_argument('--json', '-j', action='store_true',
                       help='Output status in JSON format (use with --status)')
    
    args = parser.parse_args()
    
    
    automation = MarketDataAutomation()

    if args.status:
        try:
            if args.json:
                #JSON output for clearer understanding
                status_data = {
                    'general': automation.get_status(),
                    'paper_trading': automation.get_paper_trading_summary(),
                    'timestamp': datetime.now().isoformat()
                }
                print(json.dumps(status_data, indent=2, default=str))

            else:
                #Easy readable understanding
                automation.print_status_report()

        except Exception as e:
            print(f"Error getting status : {e}")
        return
    
    try:
        automation.start()

        logger.info("==== INTEGRATED AUTOMATION STARTED===")
        logger.info("Runnign : Market Data gathering and Paper Trading")
        logger.info("Press Ctrl+C to stop")
        
        # Keep the main thread alive
        while automation.is_running:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received interrupt signal....")
    finally:
        automation.stop()

if __name__ == "__main__":
    main() 