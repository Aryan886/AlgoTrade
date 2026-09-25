import os
import yaml
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

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


def _ensure_logs_dir() -> None:
    os.makedirs("logs", exist_ok=True)


def _handler_matches_path(handler: logging.Handler, filename: str) -> bool:
    handler_path = getattr(handler, "baseFilename", None)
    if not handler_path:
        return False
    return os.path.abspath(handler_path) == os.path.abspath(filename)


def _configure_logger(
    name: str,
    level: int,
    filename: str,
    formatter: logging.Formatter,
    *,
    max_bytes: int,
    backup_count: int,
    propagate: bool = False,
) -> logging.Logger:
    _ensure_logs_dir()
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = propagate

    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler) and _handler_matches_path(handler, filename):
            handler.setLevel(level)
            handler.setFormatter(formatter)
            return logger

    handler = RotatingFileHandler(
        filename,
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


# Setup dedicated paper trading logger
def setup_paper_trading_logger():
    """Setup dedicated logger for paper trading with separate files"""

    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )

    trade_formatter = logging.Formatter(
        '%(asctime)s - %(message)s'
    )

    paper_logger = _configure_logger(
        'paper_trading',
        logging.INFO,
        'logs/paper_trading_main.log',
        detailed_formatter,
        max_bytes=10 * 1024 * 1024,
        backup_count=5,
        propagate=False,
    )

    trade_logger = _configure_logger(
        'paper_trading.trades',
        logging.INFO,
        'logs/paper_trading_actions.log',
        trade_formatter,
        max_bytes=5 * 1024 * 1024,
        backup_count=3,
    )

    position_logger = _configure_logger(
        'paper_trading.positions',
        logging.DEBUG,
        'logs/paper_trading_positions.log',
        detailed_formatter,
        max_bytes=5 * 1024 * 1024,
        backup_count=3,
    )

    sma_logger = _configure_logger(
        'paper_trading.strat_sma',
        logging.DEBUG,
        'logs/strat_sma.log',
        detailed_formatter,
        max_bytes=5 * 1024 * 1024,
        backup_count=3,
    )

    equity_logger = _configure_logger(
        'paper_trading.strat_equity_donchian',
        logging.DEBUG,
        'logs/strat_equity.log',
        detailed_formatter,
        max_bytes=5 * 1024 * 1024,
        backup_count=3,
    )

    nifty_logger = _configure_logger(
        'paper_trading.strat_nifty',
        logging.DEBUG,
        'logs/strat_nifty.log',
        detailed_formatter,
        max_bytes=5 * 1024 * 1024,
        backup_count=3,
    )

    return paper_logger, trade_logger, position_logger, sma_logger, equity_logger, nifty_logger


def setup_oi_logging():
    """Setup dedicated rotating loggers for OI strategy evaluation, trades, and position monitoring."""
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )
    trade_formatter = logging.Formatter(
        '%(asctime)s - %(message)s'
    )

    oi_strategy_logger = _configure_logger(
        'paper_trading.strat_oi',
        logging.DEBUG,
        'logs/strat_oi.log',
        detailed_formatter,
        max_bytes=5 * 1024 * 1024,
        backup_count=3,
    )
    oi_trade_logger = _configure_logger(
        'paper_trading.oi_trades',
        logging.INFO,
        'logs/oi_trade_actions.log',
        trade_formatter,
        max_bytes=5 * 1024 * 1024,
        backup_count=3,
    )
    oi_position_logger = _configure_logger(
        'paper_trading.oi_positions',
        logging.DEBUG,
        'logs/oi_position_monitor.log',
        detailed_formatter,
        max_bytes=5 * 1024 * 1024,
        backup_count=3,
    )
    return oi_strategy_logger, oi_trade_logger, oi_position_logger

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
