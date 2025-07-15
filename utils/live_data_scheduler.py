import schedule
import time
import threading
from datetime import datetime, timedelta
from utils.data_fetcher import fetch_and_save_data
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('live_data_scheduler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class LiveDataScheduler:
    def __init__(self):
        self.is_running = False
        self.scheduler_thread = None
        self.market_hours = {
            'start': '09:15',
            'end': '15:30'
        }
        
    def is_market_open(self):
        """Check if market is currently open (9:15 AM to 3:30 PM IST, Monday to Friday)"""
        now = datetime.now()
        
        # Check if it's weekend
        if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False
            
        # Check if it's within market hours
        current_time = now.time()
        market_start = datetime.strptime(self.market_hours['start'], '%H:%M').time()
        market_end = datetime.strptime(self.market_hours['end'], '%H:%M').time()
        
        return market_start <= current_time <= market_end
    
    def fetch_1m_data(self):
        """Fetch 1-minute data"""
        if not self.is_market_open():
            logger.info("Market is closed, skipping 1m data fetch")
            return
            
        try:
            logger.info("Fetching 1-minute data...")
            results, df = fetch_and_save_data(intervals=["1m"], return_interval="1m")
            if df is not None:
                logger.info(f"Successfully fetched {len(df)} rows of 1m data")
            else:
                logger.warning("Failed to fetch 1m data")
        except Exception as e:
            logger.error(f"Error fetching 1m data: {e}")
    
    def fetch_5m_data(self):
        """Fetch 5-minute data"""
        if not self.is_market_open():
            logger.info("Market is closed, skipping 5m data fetch")
            return
            
        try:
            logger.info("Fetching 5-minute data...")
            results, df = fetch_and_save_data(intervals=["5m"], return_interval="5m")
            if df is not None:
                logger.info(f"Successfully fetched {len(df)} rows of 5m data")
            else:
                logger.warning("Failed to fetch 5m data")
        except Exception as e:
            logger.error(f"Error fetching 5m data: {e}")
    
    def fetch_15m_data(self):
        """Fetch 15-minute data"""
        if not self.is_market_open():
            logger.info("Market is closed, skipping 15m data fetch")
            return
            
        try:
            logger.info("Fetching 15-minute data...")
            results, df = fetch_and_save_data(intervals=["15m"], return_interval="15m")
            if df is not None:
                logger.info(f"Successfully fetched {len(df)} rows of 15m data")
            else:
                logger.warning("Failed to fetch 15m data")
        except Exception as e:
            logger.error(f"Error fetching 15m data: {e}")
    
    def setup_schedule(self):
        """Setup the schedule for data fetching"""
        # Clear any existing schedules
        schedule.clear()
        
        # Schedule 1-minute data fetching every minute during market hours
        schedule.every().minute.do(self.fetch_1m_data)
        
        # Schedule 5-minute data fetching every 5 minutes during market hours
        schedule.every(5).minutes.do(self.fetch_5m_data)
        
        # Schedule 15-minute data fetching every 15 minutes during market hours
        schedule.every(15).minutes.do(self.fetch_15m_data)
        
        logger.info("Schedule setup completed:")
        logger.info("- 1m data: Every minute")
        logger.info("- 5m data: Every 5 minutes")
        logger.info("- 15m data: Every 15 minutes")
    
    def run_scheduler(self):
        """Run the scheduler in a separate thread"""
        logger.info("Starting live data scheduler...")
        self.is_running = True
        
        while self.is_running:
            try:
                schedule.run_pending()
                time.sleep(1)  # Check every second
            except KeyboardInterrupt:
                logger.info("Scheduler interrupted by user")
                break
            except Exception as e:
                logger.error(f"Error in scheduler: {e}")
                time.sleep(5)  # Wait 5 seconds before retrying
        
        logger.info("Live data scheduler stopped")
    
    def start(self):
        """Start the live data scheduler"""
        if self.is_running:
            logger.warning("Scheduler is already running")
            return
            
        self.setup_schedule()
        
        # Start scheduler in a separate thread
        self.scheduler_thread = threading.Thread(target=self.run_scheduler, daemon=True)
        self.scheduler_thread.start()
        
        logger.info("Live data scheduler started successfully")
        logger.info("Press Ctrl+C to stop the scheduler")
    
    def stop(self):
        """Stop the live data scheduler"""
        logger.info("Stopping live data scheduler...")
        self.is_running = False
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
        schedule.clear()
        logger.info("Live data scheduler stopped")
    
    def get_status(self):
        """Get current scheduler status"""
        return {
            'is_running': self.is_running,
            'market_open': self.is_market_open(),
            'next_jobs': schedule.get_jobs()
        }

def main():
    """Main function to run the live data scheduler"""
    scheduler = LiveDataScheduler()
    
    try:
        scheduler.start()
        
        # Keep the main thread alive
        while scheduler.is_running:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        scheduler.stop()

if __name__ == "__main__":
    main() 