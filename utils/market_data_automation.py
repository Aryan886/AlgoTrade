import schedule
import time
import threading
import logging
from datetime import datetime, timedelta
from utils.data_fetcher import fetch_and_save_data, fetch_and_save_equity 
from utils.vix_fetcher import calculate_and_store_vix
from utils.open_interest_fetcher import fetch_and_store_open_interest
from utils.db_func import store_market_data, store_signal
from utils.db_func import  calculate_and_store_high_accuracy_delta
from core.indicators import compute_indicators, generate_signals
import os
from typing import Optional, Dict
from broker.zerodha_client import kite_from_saved_token
from core.strat_donchian import donchian_ao_strategy
from core.paper_trades import PaperTraderDonchian
from core.paper_trder_sma import PaperTraderSMA
from core.equity_trader import EquityPaperTrader
from core.bot_nifty import NiftyPaperBot
from core.bot_oi import OIExpiryPaperBot
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
            #logger.info(f"High-accuracy delta calculated: Spot={spot_price}, Strikes={strike_band}")
        else:
            logger.warning("High-accuracy delta calculation failed")
    except Exception as e:
        logger.error(f"Error in high-accuracy delta calculation: {e}")

"""

def generate_and_store_signals(df, symbol="NIFTY50"):
    # Generate trading signals and store them in the database
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
"""

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
        self.sma_trader = PaperTraderSMA("NIFTY50")
        self.equity_trader = EquityPaperTrader("INFY")
        self.nifty_trader = NiftyPaperBot("NIFTY50")
        self.oi_nifty_trader = OIExpiryPaperBot("NIFTY50")
        logger.info("Paper traders (Donchian, SMA, Equity & Nifty Options) successfully initialised....")
        
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
            '15m': None,
            '1h': None,
            'oi': None
        }
        
        # Minimum intervals between fetches to avoid API rate limits
        self.min_intervals = {
            '1m': 60,    # 1 minute
            '5m': 300,   # 5 minutes
            '15m': 900,  # 15 minutes
            '1h': 3600,  # 1 hour
            'oi': 60     # 1 minute
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
                _, df = fetch_and_save_data(intervals=[interval], return_interval=interval)
                
                if df is not None and not df.empty:
                    self.last_fetch_times[interval] = datetime.now()
                    logger.info(f"Successfully fetched {len(df)} rows of {interval} data")
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
    
    def fetch_equity_with_retry(self, interval, max_retries=3):
        """Fetch equity data with retry logic"""
        if not self.is_market_open():
            logger.info(f"Market is closed, skipping {interval} equity data fetch")
            return None
            
        for attempt in range(max_retries):
            try:
                logger.info(f"Fetching {interval} equity data (attempt {attempt + 1}/{max_retries})...")
                _, df = fetch_and_save_equity("INFY", intervals=[interval], return_interval=interval) 
                
                if df is not None and not df.empty:
                    logger.info(f"Successfully fetched {len(df)} rows of {interval} equity data")
                    return df
                else:
                    logger.warning(f"Failed to fetch {interval} equity data - empty result")
                    
            except Exception as e:
                logger.error(f"Error fetching {interval} equity data (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(5 * (attempt + 1))  # Exponential backoff
                    
        logger.error(f"Failed to fetch {interval} equity data after {max_retries} attempts")
        return None

    def fetch_open_interest_with_retry(self, max_retries=3):
        """Fetch NIFTY option open-interest snapshots without blocking other jobs."""
        if not self.is_market_open():
            logger.info("Market is closed, skipping NIFTY OI fetch")
            return None

        if not self.should_fetch_data('oi'):
            logger.debug("Skipping OI fetch - too soon since last fetch")
            return None

        for attempt in range(max_retries):
            try:
                stored = fetch_and_store_open_interest("NIFTY50")
                if stored:
                    self.last_fetch_times['oi'] = datetime.now()
                    logger.info(f"Successfully stored {stored} NIFTY OI rows")
                    return stored
                logger.warning("NIFTY OI fetch returned no stored rows")
            except Exception as e:
                logger.error(f"Error fetching NIFTY OI (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(5 * (attempt + 1))

        logger.error(f"Failed to fetch NIFTY OI after {max_retries} attempts")
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

    def fetch_1h_data(self):
        """Fetch 1-hour data"""
        return self.fetch_data_with_retry('1h')
    
    def fetch_1m_equity_data(self):
        """Fetch 1minute equity data"""
        return self.fetch_equity_with_retry('1m')
    
    def fetch_5m_equity_data(self):
        """Fetch 5minute equity data"""
        return self.fetch_equity_with_retry('5m')
    
    def fetch_15m_equity_data(self):
        """Fetch 15minute equity data"""
        return self.fetch_equity_with_retry('15m')

    def fetch_open_interest_data(self):
        """Fetch NIFTY option open-interest snapshot."""
        return self.fetch_open_interest_with_retry()

    def use_oi_nifty_strategy(self, current_time=None):
        current_time = current_time or datetime.now()
        return current_time.weekday() == 1

    def get_active_nifty_strategy_name(self, current_time=None):
        return "oi_expiry" if self.use_oi_nifty_strategy(current_time=current_time) else "standard_nifty"
    
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

    def run_sma_paper_trading_cycle(self):
        """Run one cycle of SMA paper trading logic"""
        if not self.is_market_open():
            return
            
        try:
            logger.info("Running SMA paper trading cycle...")
            
            # Check for new signals if no active position
            if not self.sma_trader.has_active_position():
                signal = self.sma_trader.sma_strategy(self.sma_trader.symbol) if hasattr(self.sma_trader, 'sma_strategy') else None
                if not signal:
                    # Import and call the SMA strategy directly
                    from core.sma_stra import sma_strategy
                    signal = sma_strategy(self.sma_trader.symbol)
                
                if signal:
                    logger.info("New SMA trading signal received, executing paper trade")
                    try:
                        success = self.sma_trader.execute_paper_trade(signal)
                        if success:
                            logger.info("SMA paper trade executed successfully")
                        else:
                            logger.error("SMA paper trade execution failed")
                    except Exception as trade_error:
                        logger.error(f"Exception during SMA paper trade execution: {trade_error}")
                else:
                    logger.debug("No new SMA trading signals")
            else:
                logger.debug("SMA trader has active position, skipping signal check")

            # Manage existing position
            if self.sma_trader.has_active_position():
                logger.info("Managing existing SMA positions")
                try:
                    self.sma_trader.manage_existing_positions()
                    
                    # Log current position status
                    status = self.sma_trader.get_position_status()
                    if isinstance(status, dict):
                        logger.info(f"SMA Position Status - P&L: Rs{status.get('current_pnl', 0):.2f}")
                except Exception as manage_error:
                    logger.error(f"Error managing SMA positions: {manage_error}")
            else:
                logger.debug("No active SMA position to manage")
                
        except Exception as e:
            logger.error(f"Error in SMA paper trading cycle: {e}")

    def run_equity_paper_trading_cycle(self):
        """Run one cycle of Equity paper trading logic"""
        if not self.is_market_open():
            return
            
        try:
            logger.info("Running Equity paper trading cycle...")
            
            # Check for new signals if no active position
            if not self.equity_trader.has_active_position():
                signal = self.equity_trader.equity_strategy(self.equity_trader.symbol) if hasattr(self.equity_trader, 'equity_strategy') else None
                if not signal:
                    # Import and call the Equity strategy directly
                    from core.equity_strat import equity_strategy
                    signal = equity_strategy(self.equity_trader.symbol)
                
                if signal:
                    logger.info("New Equity trading signal received, executing paper trade")
                    try:
                        success = self.equity_trader.execute_paper_trade(signal)
                        if success:
                            logger.info("Equity paper trade executed successfully")
                        else:
                            logger.error("Equity paper trade execution failed")
                    except Exception as trade_error:
                        logger.error(f"Exception during Equity paper trade execution: {trade_error}")
                else:
                    logger.debug("No new Equity trading signals")
            else:
                logger.debug("Equity trader has active position, skipping signal check")

            # Manage existing position
            if self.equity_trader.has_active_position():
                logger.info("Managing existing Equity positions")
                try:
                    self.equity_trader.manage_existing_position()
                    
                    # Log current position status
                    status = self.equity_trader.get_position_status()
                    if isinstance(status, dict):
                        logger.info(f"Equity Position Status - P&L: Rs{status.get('current_pnl', 0):.2f}")
                except Exception as manage_error:
                    logger.error(f"Error managing Equity positions: {manage_error}")
            else:
                logger.debug("No active Equity position to manage")
                
        except Exception as e:
            logger.error(f"Error in Equity paper trading cycle: {e}")

    def run_nifty_paper_trading_cycle(self):
        """Run one cycle of Nifty Options paper trading logic"""
        now = datetime.now()
        market_open = self.is_market_open()

        if self.use_oi_nifty_strategy(current_time=now) and not market_open:
            self.oi_nifty_trader.maybe_run_tuesday_safety_cleanup(now=now)

        if not market_open:
            return

        try:
            strategy_name = self.get_active_nifty_strategy_name(current_time=now)
            logger.info(f"Running Nifty Options paper trading cycle via {strategy_name} strategy...")

            if strategy_name == "oi_expiry":
                self.oi_nifty_trader.run_once()
                oi_summary = self.oi_nifty_trader.get_status_summary()
                if (
                    oi_summary.get("type_a", {}).get("position_1_status") == "OPEN"
                    or oi_summary.get("type_a", {}).get("position_2_status") == "OPEN"
                    or oi_summary.get("type_b", {}).get("position_status") == "OPEN"
                ):
                    logger.info(
                        "OI Nifty Status - "
                        f"A1: {oi_summary.get('type_a', {}).get('position_1_status')} "
                        f"A2: {oi_summary.get('type_a', {}).get('position_2_status')} "
                        f"B: {oi_summary.get('type_b', {}).get('position_status')}"
                    )
                else:
                    logger.debug("OI Nifty trader: No active position, waiting for signals")
            else:
                self.nifty_trader.run_once()

                if not self.nifty_trader.all_lots_flat():
                    lots = self.nifty_trader.position.get("lots", {})
                    lot1_status = lots.get("lot1", {}).get("status", "N/A")
                    lot2_status = lots.get("lot2", {}).get("status", "N/A")
                    logger.info(f"Nifty Position Status - Lot1: {lot1_status}, Lot2: {lot2_status}")
                else:
                    logger.debug("Nifty trader: No active position, waiting for signals")

        except Exception as e:
            logger.error(f"Error in Nifty Options paper trading cycle: {e}")

    def get_paper_trading_summary(self):
        """Get detailed paper trading summary for Donchian, SMA, and Nifty traders"""
        try:
            donchian_status = self.paper_trader.get_position_status()
            sma_status = self.sma_trader.get_position_status()

            summary = {
                'donchian': {
                    'active_position': False,
                    'message': 'No active position'
                },
                'sma': {
                    'active_position': False,
                    'message': 'No active position'
                },
                'nifty': {
                    'selected_strategy': self.get_active_nifty_strategy_name(),
                    'active_position': False,
                    'message': 'No active position'
                }
            }
            
            # Donchian trader status
            if isinstance(donchian_status, dict) and donchian_status.get('position_active'):
                summary['donchian'] = {
                    'active_position': True,
                    'entry_time': donchian_status['entry_time'].strftime('%Y-%m-%d %H:%M:%S') if donchian_status['entry_time'] else None,
                    'current_profit': round(donchian_status['current_profit'], 2),
                    'adjustment_costs': round(donchian_status['adjustment_costs'], 2),
                    'adjustments_count': donchian_status['adjustments_count'],
                    'ce_price': donchian_status['ce_price'],
                    'pe_price': donchian_status['pe_price'],
                    'total_adjustments': len(self.paper_trader.adjustment_history),
                    'position_duration': str(datetime.now() - donchian_status['entry_time']) if donchian_status['entry_time'] else None
                }
            
            # SMA trader status
            if isinstance(sma_status, dict) and sma_status.get('position_active'):
                summary['sma'] = {
                    'active_position': True,
                    'entry_time': sma_status['entry_time'].strftime('%Y-%m-%d %H:%M:%S') if sma_status['entry_time'] else None,
                    'current_pnl': round(sma_status['current_pnl'], 2),
                    'buy_ce_price': sma_status['buy_ce_price'],
                    'sell_ce_price': sma_status['sell_ce_price'],
                    'strategy_type': sma_status.get('strategy_type', 'sma_spread'),
                    'position_duration': str(datetime.now() - sma_status['entry_time']) if sma_status['entry_time'] else None
                }

            # Nifty Options trader status
            if self.get_active_nifty_strategy_name() == 'oi_expiry':
                oi_summary = self.oi_nifty_trader.get_status_summary()
                type_a = oi_summary.get('type_a', {})
                type_b = oi_summary.get('type_b', {})
                active_position = (
                    type_a.get('position_1_status') == 'OPEN'
                    or type_a.get('position_2_status') == 'OPEN'
                    or type_b.get('position_status') == 'OPEN'
                )
                summary['nifty'] = {
                    'selected_strategy': 'oi_expiry',
                    'active_position': active_position,
                    'message': 'No active OI position' if not active_position else 'OI expiry strategy active',
                    'type_a_position_1_status': type_a.get('position_1_status', 'FLAT'),
                    'type_a_position_2_status': type_a.get('position_2_status', 'FLAT'),
                    'type_a_position_1_entry_diff': type_a.get('position_1_entry_diff'),
                    'type_a_position_2_entry_diff': type_a.get('position_2_entry_diff'),
                    'type_a_entry_flag_on': type_a.get('entry_flag_on', False),
                    'type_b_position_status': type_b.get('position_status', 'FLAT'),
                    'type_b_trigger_1_seen': type_b.get('trigger_1_seen', False),
                    'type_b_trigger_2_seen': type_b.get('trigger_2_seen', False),
                    'type_b_sell_pe_entry_ltp': type_b.get('sell_pe_entry_ltp'),
                }
            elif not self.nifty_trader.all_lots_flat():
                lots = self.nifty_trader.position.get("lots", {})
                lot1 = lots.get("lot1", {})
                lot2 = lots.get("lot2", {})
                summary['nifty'] = {
                    'selected_strategy': 'standard_nifty',
                    'active_position': True,
                    'lot1_status': lot1.get("status", "N/A"),
                    'lot2_status': lot2.get("status", "N/A"),
                    'lot1_sl': lot1.get("sl_current_level"),
                    'lot2_sl': lot2.get("sl_current_level"),
                    'opened_at': lot1.get("opened_at") or lot2.get("opened_at"),
                    'position_type': lot1.get("meta", {}).get("position_type"),
                    'num_legs': len(lot1.get("legs", [])),
                }

            return summary
            
        except Exception as e:
            return {'error': f'Error getting paper trading status: {e}'}
        
    def setup_schedule(self):
        """Setup the schedule for data fetching"""
        # Clear any existing schedules
        schedule.clear()
        
        # Market option data fetching (every minute/5min/15min)
        schedule.every().minute.do(self.fetch_1m_data)
        schedule.every(5).minutes.do(self.fetch_5m_data)
        schedule.every(15).minutes.do(self.fetch_15m_data)
        schedule.every().hour.at(":15").do(self.fetch_1h_data)
        
        # Equity data fetching (every minute/5min/15min)
        #schedule.every().minute.do(self.fetch_1m_equity_data)   
        #schedule.every(5).minutes.do(self.fetch_5m_equity_data)
        #schedule.every(15).minutes.do(self.fetch_15m_equity_data)
        
        # VIX calculation (every 5 minutes, independent of market data)
        schedule.every(5).minutes.do(calculate_vix_separately)
        
        # High-accuracy delta calculation (for delta neutral strategies)
        schedule.every().minute.do(calculate_high_accuracy_delta)

        # NIFTY option open interest snapshots (independent append-only stream)
        schedule.every().minute.do(self.fetch_open_interest_data)
        
        # Run the main trading strategy
        schedule.every().minute.do(run_trading_strategy)

        #PAPER TRADING - Run every minute during market hours
        schedule.every().minute.do(self.run_paper_trading_cycle)
        
        #SMA PAPER TRADING - Run every minute during market hours
        schedule.every().minute.do(self.run_sma_paper_trading_cycle)

        #Equity PAPER TRADING - Run every minute during market hours
        #schedule.every().minute.do(self.run_equity_paper_trading_cycle)

        #Nifty Options PAPER TRADING - Run every minute during market hours
        schedule.every().minute.do(self.run_nifty_paper_trading_cycle)

        logger.info("Schedule setup completed:")
        logger.info("- 1m data: Every minute (during market hours)")
        logger.info("- 5m data: Every 5 minutes (during market hours)")
        logger.info("- 15m data: Every 15 minutes (during market hours)")
        logger.info("- 1h data: Every hour at :15 (during market hours)")
        logger.info("- VIX calculation: Every 5 minutes (independent)")
        logger.info("- High-accuracy delta: Every minute (Black-Scholes)")
        logger.info("- NIFTY option OI: Every minute (nearest expiry, ATM +/- 300)")
        logger.info("- Trading Strategy: Every minute")
        logger.info("- Donchian Paper Trading: Every minute")
        logger.info("- SMA Paper Trading: Every minute")
        logger.info("- Nifty Options Paper Trading: Every minute")
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
            logger.info("Closing Donchian paper trading position due to system shutdown")
            self.paper_trader.close_position("all")
            
        if self.sma_trader.has_active_position():
            logger.info("Closing SMA paper trading position due to system shutdown")
            self.sma_trader.close_position("all")

        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=5)
        logger.info("Market data automation stopped")
    
    def get_status(self):
        """Get the current status of the automation"""
        donchian_status = self.paper_trader.get_position_status()
        sma_status = self.sma_trader.get_position_status()
        nifty_has_position = not self.nifty_trader.all_lots_flat()
        oi_summary = self.oi_nifty_trader.get_status_summary()
        oi_active = (
            oi_summary.get("type_a", {}).get("position_1_status") == "OPEN"
            or oi_summary.get("type_a", {}).get("position_2_status") == "OPEN"
            or oi_summary.get("type_b", {}).get("position_status") == "OPEN"
        )
        return {
            'is_running': self.is_running,
            'market_open': self.is_market_open(),
            'last_fetch_times': self.last_fetch_times,
            'donchian_trading_status': donchian_status,
            'sma_trading_status': sma_status,
            'nifty_trading_active': oi_active if self.get_active_nifty_strategy_name() == 'oi_expiry' else nifty_has_position,
            'nifty_selected_strategy': self.get_active_nifty_strategy_name(),
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
        
        # Donchian Trader Status
        print(f"\n📈 DONCHIAN TRADER:")
        donchian = paper_status.get('donchian', {})
        if donchian.get('active_position'):
            print(f" Position:  ACTIVE")
            print(f" Current P&L: Rs{donchian['current_profit']}")
            print(f"   Entry Time: {donchian['entry_time']}")
            print(f" Duration: {donchian['position_duration']}")
            print(f" Adjustments: {donchian['adjustments_count']}")
            print(f" Adjustment Costs: Rs{donchian['adjustment_costs']}")
            if donchian.get('ce_price'):
                print(f" CE Price: Rs{donchian['ce_price']}")
            if donchian.get('pe_price'):
                print(f" PE Price: Rs{donchian['pe_price']}")
        else:
            print(f" Position:  NO ACTIVE POSITION")
            print(f" Status: Waiting for trading signals...")
        
        # SMA Trader Status
        print(f"\n📊 SMA TRADER:")
        sma = paper_status.get('sma', {})
        if sma.get('active_position'):
            print(f" Position:  ACTIVE")
            print(f" Current P&L: Rs{sma['current_pnl']}")
            print(f"   Entry Time: {sma['entry_time']}")
            print(f" Duration: {sma['position_duration']}")
            print(f" Strategy: {sma.get('strategy_type', 'sma_spread')}")
            if sma.get('buy_ce_price'):
                print(f" BUY CE Price: Rs{sma['buy_ce_price']}")
            if sma.get('sell_ce_price'):
                print(f" SELL CE Price: Rs{sma['sell_ce_price']}")
        else:
            print(f" Position:  NO ACTIVE POSITION")
            print(f" Status: Waiting for trading signals...")

        # Nifty Options Trader Status
        print(f"\n📉 NIFTY OPTIONS TRADER:")
        nifty = paper_status.get('nifty', {})
        print(f" Strategy: {nifty.get('selected_strategy', 'standard_nifty')}")
        if nifty.get('active_position'):
            print(f" Position:  ACTIVE")
            if nifty.get('selected_strategy') == 'oi_expiry':
                print(f" Type A Position 1: {nifty.get('type_a_position_1_status', 'N/A')}")
                print(f" Type A Position 2: {nifty.get('type_a_position_2_status', 'N/A')}")
                print(f" Type A Entry Flag: {nifty.get('type_a_entry_flag_on', False)}")
                print(f" Type A Entry Diff 1: {nifty.get('type_a_position_1_entry_diff', 'N/A')}")
                print(f" Type A Entry Diff 2: {nifty.get('type_a_position_2_entry_diff', 'N/A')}")
                print(f" Type B Position: {nifty.get('type_b_position_status', 'N/A')}")
                print(f" Type B Trigger 1: {nifty.get('type_b_trigger_1_seen', False)}")
                print(f" Type B Trigger 2: {nifty.get('type_b_trigger_2_seen', False)}")
                print(f" Type B Sell PE Entry LTP: {nifty.get('type_b_sell_pe_entry_ltp', 'N/A')}")
            else:
                print(f" Lot1 Status: {nifty.get('lot1_status', 'N/A')}")
                print(f" Lot2 Status: {nifty.get('lot2_status', 'N/A')}")
                print(f" Lot1 SL Level: {nifty.get('lot1_sl', 'N/A')}")
                print(f" Lot2 SL Level: {nifty.get('lot2_sl', 'N/A')}")
                print(f" Opened At: {nifty.get('opened_at', 'N/A')}")
                print(f" Position Type: {nifty.get('position_type', 'N/A')}")
                print(f" Num Legs: {nifty.get('num_legs', 0)}")
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
