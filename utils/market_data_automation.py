import schedule
import time
import threading
import logging
from datetime import datetime, timedelta
from utils.data_fetcher import fetch_and_save_data
from utils.vix_fetcher import calculate_and_store_vix
from utils.db_func import store_market_data, store_signal
from utils.db_func import  calculate_and_store_high_accuracy_delta
from strategies.indicators import compute_indicators, generate_signals
import os
from typing import Optional, Dict
from broker.zerodha_client import kite_from_saved_token

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
                logger.info(f"Signal stored: {signal['signal']} for {signal['symbol']} - {signal['reason']}")
            except Exception as e:
                logger.error(f"Failed to store signal: {e}")
        
        if signals:
            logger.info(f"Generated {len(signals)} trading signals")
        else:
            logger.info("No trading signals generated")
            
    except Exception as e:
        logger.error(f"Error in signal generation: {e}")

class MarketDataAutomation:
    def __init__(self):
        self.is_running = False
        self.scheduler_thread = None
        self.market_hours = {
            'start': '09:15',
            'end': '15:30'
        }
        
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
                logger.info(f"Fetching {interval} data (attempt {attempt + 1}/{max_retries})...")
                results, df = fetch_and_save_data(intervals=[interval], return_interval=interval)
                
                if df is not None and not df.empty:
                    self.last_fetch_times[interval] = datetime.now()
                    logger.info(f"Successfully fetched {len(df)} rows of {interval} data")
                    
                    # Store market data WITHOUT VIX (VIX will be calculated separately)
                    store_market_data(df, symbol="NIFTY50", interval=interval)
                    logger.info(f"Stored {interval} market data successfully")
                    
                    # Generate and store signals
                    generate_and_store_signals(df, "NIFTY50")
                    
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
        
        logger.info("Schedule setup completed:")
        logger.info("- 1m data: Every minute (during market hours)")
        logger.info("- 5m data: Every 5 minutes (during market hours)")
        logger.info("- 15m data: Every 15 minutes (during market hours)")
        logger.info("- VIX calculation: Every 5 minutes (independent)")
        logger.info("- High-accuracy delta: Every minute (Black-Scholes)")
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
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()
        
        logger.info("Market data automation started successfully")
        logger.info("Press Ctrl+C to stop the automation")
    
    def stop(self):
        """Stop the market data automation"""
        logger.info("Stopping market data automation...")
        self.is_running = False
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=5)
        logger.info("Market data automation stopped")
    
    def get_status(self):
        """Get the current status of the automation"""
        return {
            'is_running': self.is_running,
            'market_open': self.is_market_open(),
            'last_fetch_times': self.last_fetch_times
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

def main():
    """Main function to run the market data automation"""
    automation = MarketDataAutomation()
    
    try:
        automation.start()
        
        # Keep the main thread alive
        while automation.is_running:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        automation.stop()

if __name__ == "__main__":
    main() 