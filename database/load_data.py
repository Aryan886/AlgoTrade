#This will be used to get old data for backtesting
import pandas as pd
from utils.utility import get_data_path
from pathlib import Path
from pprint import pprint


def load_data(filepath: str) -> pd.DataFrame:
    path = get_data_path(filepath)
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df

def get_latest_data_file(symbol: str, interval: str, tag: str = "") -> Path:
    folder = get_data_path(f"{symbol}/{interval}")
    pattern = f"*{tag}*.csv" if tag else "*.csv"
    files = sorted(folder.glob(pattern), reverse=True)
    return files[0] if files else None

def load_latest_data(symbol: str, interval: str) -> pd.DataFrame:
    latest_file = get_latest_data_file(symbol, interval)
    if latest_file is None:
        raise FileNotFoundError(f"No data file found for {symbol} at {interval} interval.")

    print(f"Loading file: {latest_file}")  

    df = pd.read_csv(
        latest_file,
        index_col=0,
        parse_dates=True,
        date_format="%Y-%m-%d %H:%M:%S "  
    )
    print(f"Loaded {len(df)} rows")

    #Changing columns to Numeric
    price_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    for col in price_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    #Dropping rows where essential price column is missing
    df.dropna(subset=['Close'], inplace=True)
    #print(df.columns)
    return df


if __name__ == "__main__":
    symbol = "NSEI"
    interval = "1m"

    try:
        df = load_latest_data(symbol, interval)
        print(f"Loaded data for {symbol} at {interval}: {len(df)} rows")
        pprint(df.head())  # Just to show a few rows
    except Exception as e:
        print(f"Error: {e}")
