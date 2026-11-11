# This is used to get the live data (from Kite API) for NIFTY 50 only
import os
import pandas as pd
from datetime import datetime, timedelta
from utils.utility import get_data_path
from broker.zerodha_client import get_instruments, kite_from_saved_token
from utils.db_func import store_market_data, store_signal, store_sma_from_df, store_equity_data
from core.indicators import compute_indicators, generate_signals, compute_smas, compute_smas_with_high_low


from config.config import CONFIG, DB_PATH

# Days of history each interval needs so the longest indicator has enough candles.
LOOKBACK_DAYS = {'1m': 5, '5m': 7, '15m': 10, '1h': 180}

# Raw candles already downloaded, per (symbol, interval). A run only asks Kite
# for candles from the last one held (it may still have been forming) onwards.
_candle_windows = {}


def reset_candle_windows():
    """Forget the held candles so the next run downloads the full history again."""
    _candle_windows.clear()


def _merge_into_window(window, fresh, now, interval):
    """Replace/append the fresh candles and drop those older than the lookback."""
    merged = pd.concat([window[window.index < fresh.index[0]], fresh]).sort_index()
    merged = merged[~merged.index.duplicated(keep='last')]

    lookback_days = LOOKBACK_DAYS.get(interval)
    if lookback_days is not None:
        cutoff = pd.Timestamp(
            now.replace(hour=9, minute=15, second=0, microsecond=0) - timedelta(days=lookback_days)
        )
        if merged.index.tz is not None:
            cutoff = cutoff.tz_localize(merged.index.tz)
        merged = merged[merged.index >= cutoff]
    return merged


class Nifty50DataFetcher:
    def __init__(self):
        self.kite = None
        self.instrument_token = None

    def connect(self):
        #Connect to Kite API using saved token
        try:
            self.kite = kite_from_saved_token()
            if self.kite:
                print("Connected to Kite API successfully!")
                return True
            else:
                print("Failed to connect to Kite API. Please run login_kite() first.")
                return False
        except Exception as e:
            print(f"Failed to connect to Kite API: {e}")
            return False

    def get_instrument_token(self, symbol="NIFTY 50"):
        """Get instrument token for the given symbol"""
        try:
            if self.kite is None:
                print("Kite connection not established")
                return None
                
            instruments = get_instruments(self.kite, "NSE")
            print(f"[DEBUG] Total instruments found: {len(instruments)}")
            
            # Look for NIFTY 50 with different possible symbols
            nifty_symbols = ['NIFTY 50', 'NIFTY50', 'NIFTY', 'NIFTY50.NS']
            found_token = None
            
            for inst in instruments:
                if inst['tradingsymbol'] in nifty_symbols:
                    found_token = inst['instrument_token']
                    print(f"[DEBUG] Found NIFTY 50 with symbol '{inst['tradingsymbol']}' and token: {found_token}")
                    break
            
            if found_token:
                self.instrument_token = found_token
                print(f"NIFTY 50 instrument token: {self.instrument_token}")
                return self.instrument_token
            else:
                print("[DEBUG] NIFTY 50 not found. Checking first few instruments:")
                for i, inst in enumerate(instruments[:10]):
                    print(f"  {i}: {inst['tradingsymbol']} - {inst['instrument_token']}")
                print("NIFTY 50 instrument not found in NSE instruments list.")
                return None
        except Exception as e:
            print(f"Error getting instrument token: {e}")
            return None

    def fetch_historical_data(self, interval, from_date, to_date):
        """Fetch historical data using Kite API"""
        if not self.instrument_token:
            print("Instrument token not available. Please connect first.")
            return None
        
        if self.kite is None:
            print("Kite connection not established")
            return None
        
        try:
            print(f"Fetching {interval} data from {from_date} to {to_date}")
            data = self.kite.historical_data(
                instrument_token=self.instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval
            )
            
            if data:
                # Convert to DataFrame
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                
                # Rename columns to match expected format
                df.rename(columns={
                    'open': 'Open',
                    'high': 'High', 
                    'low': 'Low',
                    'close': 'Close',
                    'volume': 'Volume'
                }, inplace=True)
                
                print(f"Successfully fetched {len(df)} rows of {interval} data")
                return df
            else:
                print(f"No data received for {interval}")
                return None
                
        except Exception as e:
            print(f"Error fetching historical data: {e}")
            return None

    def fetch_live_data(self, interval='minute', now=None, since=None):
        """
        Fetch live NIFTY 50 candles up to now.
        With `since`, only candles from that timestamp onwards are requested;
        otherwise the full lookback for the interval is downloaded.
        """

        if not self.kite:
            print("Not connected to Kite API. Please connect first.")
            return None
        
        # Convert interval format to Kite API format
        interval_map = {
            '1m': 'minute',
            '5m': '5minute', 
            '15m': '15minute',
            '1h': '60minute'
        }
        
        kite_interval = interval_map.get(interval, interval)
        
        # Get current date and time
        now = now or datetime.now()
        today_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
        
        # Check if market is open
        current_time = now.time()
        market_start = datetime.strptime('09:15', '%H:%M').time()
        market_end = datetime.strptime('15:30', '%H:%M').time()
        
        if not (market_start <= current_time <= market_end):
            print("Market is closed. Current time:", current_time)
            return None
        
        # Check if it's a weekday
        today_weekday = now.weekday()
        if today_weekday >= 5:  # Saturday = 5, Sunday = 6
            print("Market is closed on weekends")
            return None

        if since is not None:
            return self.fetch_historical_data(kite_interval, since, now)

        # Extend the start date to get more historical data for indicators
        # This ensures we have enough data for Donchian (20 periods) and AO (34 periods)
        if interval == '1m':
            # For 1m data, get 5 days of data to ensure enough for indicators
            extended_start = today_start - timedelta(days=5)
            print(f"[DEBUG] For 1m interval, extending date range from {today_start} to {extended_start}")
            today_start = extended_start
        elif interval == '5m':
            # For 5m data, get 7 days of data (Kite API might have limitations)
            extended_start = today_start - timedelta(days=7)
            print(f"[DEBUG] For 5m interval, extending date range from {today_start} to {extended_start}")
            today_start = extended_start
        elif interval == '15m':
            # For 15m data, get 10 days of data (Kite API might have limitations)
            extended_start = today_start - timedelta(days=10)
            print(f"[DEBUG] For 15m interval, extending date range from {today_start} to {extended_start}")
            today_start = extended_start
        elif interval == '1h':
            # For 1h data, fetch enough history to populate longer SMAs consistently.
            extended_start = today_start - timedelta(days=180)
            print(f"[DEBUG] For 1h interval, extending date range from {today_start} to {extended_start}")
            today_start = extended_start
        
        print(f"[DEBUG] Fetching data for interval: {interval} (Kite format: {kite_interval})")
        print(f"[DEBUG] From: {today_start} To: {now}")
        print(f"[DEBUG] Current time: {now}")
        print(f"[DEBUG] Today's weekday: {today_weekday} (0=Monday, 6=Sunday)")
        
        # Try with extended date range first
        print(f"[DEBUG] fetch_live_data: interval={interval}, requesting from {today_start} to {now}")
        result = self.fetch_historical_data(kite_interval, today_start, now)
        
        if result is not None and not result.empty:
            return result
        else:
            print(f"[DEBUG] Extended range failed, trying with original range")
            # Fallback to original range
            original_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
            return self.fetch_historical_data(kite_interval, original_start, now)

def fetch_and_save_data(intervals=["1m", "5m", "15m"], return_interval=None, symbol="NIFTY50", period="1d", db_path=DB_PATH, now=None):
    """
    Fetch and save data for multiple intervals.
    
    Parameters:
        intervals: List of intervals to fetch
        return_interval: If specified, return data for this interval
        symbol: Symbol to fetch data for
        period: Period for data fetching
        db_path: Database to store into
        now: Current time (defaults to the wall clock)

    The first run for an interval downloads its full lookback and stores every
    row. Later runs download only candles since the last one held, recompute
    indicators on the in-memory window and store only the new/changed rows.

    Returns:
        List of tuples (interval, df, file_path) or (results, return_df) if return_interval specified
    """
    results = []
    return_df = None
    
    # Initialize data fetcher
    fetcher = Nifty50DataFetcher()
    
    # Connect to Kite API
    if not fetcher.connect():
        print("Failed to connect to Kite API")
        return results if not return_interval else (results, None)
    
    # Get instrument token
    if not fetcher.get_instrument_token():
        print("Failed to get instrument token")
        return results if not return_interval else (results, None)
    
    for interval in intervals:
        print(f"\n--- Processing {interval} interval ---")
        
        # Initialize full_path variable
        full_path = None
        
        # Fetch data: everything on the first run, only the new candles afterwards
        window_key = (symbol, interval)
        window = _candle_windows.get(window_key)
        write_from = None
        if window is None or window.empty:
            df = fetcher.fetch_live_data(interval, now=now)
        else:
            write_from = window.index[-1]
            fresh = fetcher.fetch_live_data(interval, now=now, since=write_from)
            if fresh is not None and not fresh.empty:
                df = _merge_into_window(window, fresh, now or datetime.now(), interval)
            else:
                df = None

        if df is not None and not df.empty:
            _candle_windows[window_key] = df

            # Compute indicators
            df = compute_indicators(df)

            # Normalize interval for DB (same as you do for market_data)
            db_interval_map = {
                '1m': '1m',
                '5m': '5m',
                '15m': '15m',
                '1h': '1h',
            }
            db_interval = db_interval_map.get(interval, interval)

            # Add sma
            try:
                store_sma_from_df(df, symbol=symbol, interval=db_interval, db_path=db_path, write_from=write_from)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"Store sma from df failed: {e}")

            # Generate signals
            signals = generate_signals(df, symbol)
            
            # Store signals in database
            for signal in signals:
                try:
                    store_signal(
                        timestamp=signal['timestamp'],
                        symbol=signal['symbol'],
                        signal=signal['signal'],
                        reason=signal['reason'],
                        confidence_score=signal['confidence_score'],
                        db_path=db_path
                    )
                    print(f"[SIGNAL STORED] {signal['signal']} signal for {signal['symbol']}")
                except Exception as e:
                    print(f"[ERROR] Failed to store signal: {e}")
            
            # Per-run CSV dump, only when explicitly enabled (debugging aid)
            if CONFIG.get("SAVE_CANDLE_CSV", False):
                data_path = get_data_path(f"{symbol}/{interval}")
                data_path.mkdir(parents=True, exist_ok=True)

                timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{symbol}_{interval}_{timestamp_str}.csv"
                full_path = data_path / filename

                df.to_csv(full_path)
                print(f"Saved data to: {full_path}")
            #print(f"[DEBUG] Sample data:\n{df.head()}")
            # Store in database (VIX will be calculated separately)
            # Convert interval format for database table names
            db_interval_map = {
                '1m': '1m',  # Use 1m for database table
                '5m': '5m',  # Use 5m for database table
                '15m': '15m',  # Use 15m for database table
                '1h': '1h'
            }
            db_interval = db_interval_map.get(interval, interval) or interval
            store_market_data(df, symbol=symbol, interval=db_interval, db_path=db_path, write_from=write_from)
            if return_interval and interval == return_interval:
                df.dropna(inplace=True)
                return_df = df.copy()
        else:
            print(f"No data was downloaded for interval {interval}.")
            print(f"[DEBUG] df is None: {df is None}")
            if df is not None:
                print(f"[DEBUG] df is empty: {df.empty}")
                print(f"[DEBUG] df shape: {df.shape}")
            # Ensure full_path is None when data fetching fails
            full_path = None
        results.append((interval, df, full_path))
    if return_interval:
        return results, return_df
    return results


class EquityDataFetcher:
    def __init__(self):
        self.kite = None
    
    def connect(self):
        try:
            self.kite = kite_from_saved_token()
            if self.kite:
                print("Connected to Kite API successfully!")
                return True
            else:
                print("Failed to connect to Kite API. Please run login_kite() first.")
                return False
        except Exception as e:
            print(f"Failed to connect to Kite API: {e}")
            return False
        
    def fetch_equity_historical(self, symbol: str, interval: str, from_date, to_date):
        """Fetch historical OHLCV for a stock (equity symbol like INFY)."""
        interval_map = {
            '1m': 'minute',
            '5m': '5minute',
            '15m': '15minute'
        }
        kite_interval = interval_map.get(interval, interval)

        if self.kite is None:
            print("Kite connection not established for equities")
            return None

        try:
            #Find instrument token
            instruments = get_instruments(self.kite, "NSE")
            token = None
            for inst in instruments:
                if inst['tradingsymbol'] == symbol:
                    token = inst['instrument_token']
                    break

            if not token:
                print(f"Instrument token not found for symbol: {symbol}")
                return None
            
            print(f"Fetching {interval} data for {symbol} from {from_date} to {to_date}")
            data = self.kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval=kite_interval
            )

            if not data:
                print(f"No data received for {symbol} at interval {interval}")
                return None
            
            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            df.rename(columns={
                'open': 'open', "high": 'high', 'low': 'low', 'close': 'close', 'volume': 'volume'
            }, inplace=True)

            #Compute VWAP before storing
            typical_price = (df['high'] + df['low'] + df['close']) / 3
            df['vwap'] = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()

            #Compute additional indicators
            df = compute_indicators(df)

            #Compute SMAs
            df = compute_smas(df)


            return df
        
        except Exception as e:
            print(f"Error fetching historical data for {symbol}: {e}")
            return None


def fetch_and_save_equity(symbol: str, intervals = ["1m","5m", "15m"],return_interval=None, db_path=DB_PATH):
    """
    Fetch and store equity OHLCV+VWAP for given stock symbol into equity_data_<interval> tables.
    """
    fetcher = EquityDataFetcher()
    if not fetcher.connect():
        return ([], None) if return_interval else []

    results = []
    return_df = None
    now = datetime.now()
    from_date = now - timedelta(days=5)  # adjust as needed
    to_date = now

    for interval in intervals:
        df = fetcher.fetch_equity_historical(symbol, interval, from_date, to_date)
        if df is not None and not df.empty:
            store_equity_data(df, symbol, interval=interval, db_path=db_path)
            print(f"[EQUITY STORED] {symbol} ({interval}) with {len(df)} rows")
            
            # Store equity SMA data with high/low tracking
            from utils.db_func import store_equity_sma_from_df
            try:
                sma_rows = store_equity_sma_from_df(df, symbol, interval=interval, db_path=db_path)
                print(f"[EQUITY SMA STORED] {symbol} ({interval}) SMA with {sma_rows} rows")
            except Exception as e:
                print(f"[EQUITY SMA ERROR] Failed to store SMA data for {symbol} ({interval}): {e}")
            
            results.append((interval, df))

        if return_interval and interval == return_interval and df is not None:
            df.dropna(inplace=True)
            return_df = df.copy()

    if return_interval:
        return results, return_df
    return results

if __name__ == "__main__":
    print("Testing NIFTY 50 data fetcher...")
    fetch_and_save_equity("NIFTY 50")
