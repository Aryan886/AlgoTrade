import os
import yaml
from pathlib import Path
import logging

def load_config():
    base_path = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_path, '..', 'config', 'secrets.yaml')
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)


def get_data_path(filename: str) -> Path:
    """
    Returns the full path to a file 
    """
    base_dir = Path(__file__).resolve().parents[1]  # Go to project root
    return base_dir / "database" / "datas" / filename


def save_debug_csv(df, name, folder="debug"):
    base_dir = os.path.dirname(os.path.abspath(__file__))  # location of utils.py
    target_folder = os.path.join(base_dir, folder)
    os.makedirs(target_folder, exist_ok=True)
    path = os.path.join(target_folder, f"{name}.csv")
    df.to_csv(path)
    print(f"Saved debug file: {path}")


# Setup dedicated paper trading logger
def setup_paper_trading_logger():
    """Setup dedicated logger for paper trading with separate files"""
    
    # Create paper trading logger
    paper_logger = logging.getLogger('paper_trading')
    paper_logger.setLevel(logging.INFO)
    paper_logger.handlers.clear()  # Clear any existing handlers
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )
    
    trade_formatter = logging.Formatter(
        '%(asctime)s - %(message)s'
    )
    
    # 1. Main paper trading log (all paper trading activities)
    from logging.handlers import RotatingFileHandler
    main_handler = RotatingFileHandler(
        'logs/paper_trading_main.log', 
        maxBytes=10*1024*1024, 
        backupCount=5
    )
    main_handler.setFormatter(detailed_formatter)
    main_handler.setLevel(logging.INFO)
    
    # 2. Trade actions only (entries, exits, adjustments)
    trade_handler = RotatingFileHandler(
        'logs/paper_trading_actions.log',
        maxBytes=5*1024*1024,
        backupCount=3
    )
    trade_handler.setFormatter(trade_formatter)
    trade_handler.setLevel(logging.INFO)
    
    # 3. Position management (profit checks, adjustments, etc.)
    position_handler = RotatingFileHandler(
        'logs/paper_trading_positions.log',
        maxBytes=5*1024*1024,
        backupCount=3
    )
    position_handler.setFormatter(detailed_formatter)
    position_handler.setLevel(logging.DEBUG)

    # 4. Logger for sma strategy specific logs
    sma_strategy_handler = RotatingFileHandler(
        'logs/strat_sma.log',
        maxBytes=5*1024*1024,
        backupCount=3
    )
    sma_strategy_handler.setFormatter(detailed_formatter)
    sma_strategy_handler.setLevel(logging.DEBUG)
    
    #5. Logger for equity donchian strategy specific logs
    equity_handler = RotatingFileHandler(
        'logs/strat_equity.log',
        maxBytes=5*1024*1024,
        backupCount=3
    )
    equity_handler.setFormatter(detailed_formatter)
    equity_handler.setLevel(logging.DEBUG)

    # Add handlers to logger
    paper_logger.addHandler(main_handler)
    
    # Create separate loggers for specific activities
    trade_logger = logging.getLogger('paper_trading.trades')
    trade_logger.setLevel(logging.INFO)
    trade_logger.handlers.clear()
    trade_logger.addHandler(trade_handler)
    trade_logger.propagate = False  # Don't propagate to parent logger
    
    position_logger = logging.getLogger('paper_trading.positions')
    position_logger.setLevel(logging.DEBUG)
    position_logger.handlers.clear()
    position_logger.addHandler(position_handler)
    position_logger.propagate = False
    
    sma_logger = logging.getLogger('paper_trading.strat_sma')
    sma_logger.setLevel(logging.DEBUG)
    sma_logger.handlers.clear()
    sma_logger.addHandler(sma_strategy_handler)
    sma_logger.propagate = False  # Don't propagate to parent logger

    equity_logger = logging.getLogger('paper_trading.strat_equity_donchian')
    equity_logger.setLevel(logging.DEBUG)
    equity_logger.handlers.clear()
    equity_logger.addHandler(equity_handler)

    # 6. Logger for nifty options strategy specific logs
    nifty_handler = RotatingFileHandler(
        'logs/strat_nifty.log',
        maxBytes=5*1024*1024,
        backupCount=3
    )
    nifty_handler.setFormatter(detailed_formatter)
    nifty_handler.setLevel(logging.DEBUG)

    nifty_logger = logging.getLogger('paper_trading.strat_nifty')
    nifty_logger.setLevel(logging.DEBUG)
    nifty_logger.handlers.clear()
    nifty_logger.addHandler(nifty_handler)
    nifty_logger.propagate = False

    return paper_logger, trade_logger, position_logger, sma_logger, equity_logger, nifty_logger

def standardize_column_names(df):
    """
    Standardize column names to match expected format for indicators
    Converts common variations to standard format
    """
    column_mapping = {
        'open': 'open', #keep if already correct
        'high': 'high', 
        'low': 'low', #keep if already correct
        'close': 'close', #keep if already correct
        'volume': 'volume', #keep if already correct
        'Open': 'open',   
        'High': 'high',   
        'Low': 'low',     
        'Close': 'close', 
        'Volume': 'volume',
        'SL': 'sl',
        'SH': 'sh'
    }
    
    # Rename columns if they exist
    existing_columns = {col: column_mapping.get(col, col) for col in df.columns if col in column_mapping}
    
    if existing_columns:
        df = df.rename(columns=existing_columns)
        print(f"Standardized columns: {existing_columns}")
    
    return df
